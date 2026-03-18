"""Test fixtures and configuration for FastAPI test suite."""

import asyncio
import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

import fastapi_app.core.config as config_module

# CRITICAL: Override settings BEFORE any app import
# This prevents lru_cache from caching the production settings
from fastapi_app.core.config import Settings, get_settings
from fastapi_app.core.redis_keys import (
	access_key as _access_key_fn,
)
from fastapi_app.core.redis_keys import (
	game_session_key as _game_session_key_fn,
)
from fastapi_app.core.redis_keys import (
	gamification_settings_key,
	registration_options_key,
)
from fastapi_app.core.redis_keys import (
	hierarchy_key as _hierarchy_key_fn,
)
from fastapi_app.core.redis_keys import (
	session_key as _session_key_fn,
)
from fastapi_app.core.redis_keys import (
	wallet_key as _wallet_key_fn,
)

_test_settings = Settings(
	redis_url="redis://127.0.0.1:13001",
	jwt_secret="test-secret-key-for-unit-tests",
	jwt_algorithm="HS256",
	bitmap_json_path="/tmp/test-bitmaps",
	frappe_url="http://localhost:8000",
	frappe_site="test.local",
	frappe_api_key="test-key",
	frappe_api_secret="test-secret",
	voucher_hmac_secret="test-hmac-secret",
	# Rate limiting: low values for fast tests
	global_rate_limit=10,
	global_rate_limit_window=60,
	reviews_rate_limit=5,
	session_rate_limit=3,
	ws_max_connections_per_user=3,
	lc_join_rate_limit=5,
	lc_submit_rate_limit=2,
)

get_settings.cache_clear()
config_module.get_settings = lambda: _test_settings

# NOW safe to import app and other dependencies
from httpx import ASGITransport

from fastapi_app.api.deps import get_frappe_client, get_redis
from fastapi_app.core.security import create_access_token
from fastapi_app.main import app


@pytest.fixture
def test_prefix() -> str:
	"""
	Generate per-test Redis key namespace prefix.

	Returns:
		A unique prefix string in format "test:{8-char-hex}:" for per-test
		Redis key isolation to prevent cross-test pollution.
	"""
	return f"test:{uuid4().hex[:8]}:"


@pytest.fixture
async def redis_client() -> AsyncGenerator[redis.Redis, None]:
	"""
	Async Redis client fixture connected to test Redis instance.

	Creates a new client per test function with proper cleanup.

	Yields:
		redis.asyncio.Redis client connected to the Memora Redis instance
		(from .env REDIS_URL) with decode_responses=True for string operations.

	Raises:
		ConnectionError: If Redis is not available at the configured URL.
	"""
	client = redis.Redis.from_url(
		_test_settings.redis_url,
		decode_responses=True,
	)
	yield client
	await client.close()


@pytest.fixture(autouse=True)
async def cleanup_keys(redis_client: redis.Redis, test_prefix: str) -> AsyncGenerator[None, None]:
	"""
	Automatically clean up test Redis keys after each test.

	Uses SCAN+DELETE pattern to remove all keys matching the test prefix,
	ensuring no state leakage between tests. NEVER uses FLUSHDB (per FR-009
	constraint of shared Redis with production Frappe).

	Args:
		redis_client: Redis client fixture
		test_prefix: Test-specific key prefix fixture

	Yields:
		None after test completes, cleanup runs in teardown.
	"""
	# Reset global state so each test gets a clean slate
	import fastapi_app.api.deps as deps_module

	deps_module._frappe_client = None
	deps_module._session_fid_cache.clear()
	# Clear process-local caches to prevent order-dependent stale data
	from fastapi_app.services.hierarchy import _local_hierarchy_cache

	_local_hierarchy_cache.clear()
	import fastapi_app.services.hierarchy as hierarchy_module

	hierarchy_module._free_content_subjects_cache = None
	from fastapi_app.services.access import _grants_cache, _plan_subjects_cache

	_grants_cache.clear()
	_plan_subjects_cache.clear()
	from fastapi_app.services.stats import _stats_local_cache

	_stats_local_cache.clear()
	from fastapi_app.services.progress import _progress_exists_cache

	_progress_exists_cache.clear()

	yield

	# Cleanup: Scan and delete all keys matching test prefix and memora:* test keys
	# (tests create memora:catalog:* keys and other memora:* keys via redis_keys.py builders)
	patterns_to_clean = [
		f"{test_prefix}*",
		"memora:catalog:*",
		"memora:session:PLAYER-TEST-*",
		"memora:session:USER-*",
		"memora:settings:gamification",  # Clear cached settings between tests
		"memora:access:PLAYER-TEST-*",
		"memora:wallet:PLAYER-TEST-*",
		"memora:progress:USER-*",
		"memora:progress:PLAYER-*",
		"memora:stats:USER-*",
		"memora:stats:USR-*",
		"memora:stats:PLAYER-*",
		"memora:gamesession:USER-*",
		"memora:devices:USER-*",
		"memora:pending:PLAYER-*",
		"memora:pending_reg:*",  # OTP pending registrations (random token IDs)
		"memora:phone_reserved:*",
		"memora:reset_otp:*",
		"memora:reset_token:*",
		"memora:plan:PLAN-TEST-*",
		"memora:dirty:*",
		"memora:buffer:*",
		"memora:global_rl:*",  # Global rate limit counters
		"memora:rl:*",  # Per-player rate limit counters
		"memora:reviews_overview:*",
		"memora:ratelimit:*",
		"memora:hierarchy:SUBJ-TEST-*",
		"memora:subjects_with_free_content",
		"memora:profile:PLAYER-TEST-*",
		"memora:practice:PLAYER-TEST-*",
		"memora:practice:hierarchy_meta:*",
		"memora:freeze:PLAYER-TEST-*",
		"memora:plan_change_ts:PLAYER-TEST-*",
		"memora:daily_xp:PLAYER-TEST-*",
		"memora:player_plan:PLAYER-TEST-*",
		"memora:lc:*",  # Live challenge keys (status, questions, count, submitted, joined, meta)
		"memora:lc_reaction_rl:*",  # Reaction rate limit keys
		"memora:lc_burst:*",  # Reaction burst pub/sub channels (leftover keys)
		registration_options_key(),
	]

	for pattern in patterns_to_clean:
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(
				cursor,
				match=pattern,
				count=1000,
			)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

	deps_module._session_fid_cache.clear()


@pytest.fixture
def mock_frappe() -> AsyncMock:
	"""
	Mock FrappeClient for testing without Frappe API calls.

	Returns:
		AsyncMock instance with pre-configured methods:
			- .call → AsyncMock(return_value=None)
			- .get_grant_keys → AsyncMock(return_value=[])
			- .create_subscription → AsyncMock(return_value={})
			- .close → AsyncMock()
	"""
	mock = AsyncMock()
	mock.call = AsyncMock(return_value=None)
	mock.get_grant_keys = AsyncMock(return_value=[])
	mock.create_subscription = AsyncMock(return_value={})
	mock.close = AsyncMock()
	return mock


@pytest.fixture
def make_player_token():
	"""
	Factory fixture to create player JWT tokens for testing.

	Returns:
		Callable factory that creates (token_str, family_id) tuples.
		Default parameters match typical player token payload.

	Usage:
		token, family_id = make_player_token()
		token, family_id = make_player_token(player_id="CUSTOM-ID")
	"""

	def _make_token(
		player_id: str = "PLAYER-TEST-001",
		plan_id: str = "PLAN-TEST-001",
		display_name: str = "Test Player",
	) -> tuple[str, str]:
		"""
		Create a player access token.

		Args:
			player_id: Player document name (e.g., 'PLAYER-00001')
			plan_id: Player's plan document name (e.g., 'PLAN-00001')
			display_name: Player's display name

		Returns:
			Tuple of (token_string, family_id_uuid) for session management.
		"""
		family_id = str(uuid4())
		token = create_access_token(
			user_id=player_id,
			plan_id=plan_id,
			display_name=display_name,
			family_id=family_id,
			mobile="201000000000",  # Egyptian format
		)
		return token, family_id

	return _make_token


@pytest.fixture
def make_admin_token():
	"""
	Factory fixture to create admin JWT tokens for testing.

	Returns:
		Callable factory that creates (token_str, family_id) tuples
		with 'System Manager' role for admin endpoints.

	Usage:
		token, family_id = make_admin_token()
		token, family_id = make_admin_token(email="custom@admin.local")
	"""

	def _make_token(email: str = "admin@test.local") -> tuple[str, str]:
		"""
		Create an admin access token.

		Args:
			email: Admin email address (goes in 'sub' claim)

		Returns:
			Tuple of (token_string, family_id_uuid) for session management.
		"""
		family_id = str(uuid4())
		token = create_access_token(
			user_id=email,
			plan_id="PLAN-ADMIN",
			display_name="Admin User",
			family_id=family_id,
			email=email,
			role="System Manager",
		)
		return token, family_id

	return _make_token


@pytest.fixture
async def app_client(redis_client: redis.Redis, mock_frappe: AsyncMock) -> AsyncGenerator[AsyncClient, None]:
	"""
	FastAPI test client with dependency overrides for isolated testing.

	Wire in test Redis and mock Frappe to isolate tests from external
	dependencies. Dependency overrides allow tests to control Redis and
	Frappe behavior.

	Args:
		redis_client: Test Redis fixture
		mock_frappe: Mock FrappeClient fixture

	Yields:
		httpx.AsyncClient configured with FastAPI app and dependency
		overrides for testing.
	"""
	import fastapi_app.api.deps as deps_mod

	# Override dependencies to use test instances
	app.dependency_overrides[get_redis] = lambda: redis_client

	# Override async get_frappe_client with a function that returns the mock
	# Must be callable (not AsyncMock directly) so FastAPI can call it
	async def get_mock_frappe_client() -> AsyncMock:
		return mock_frappe

	app.dependency_overrides[get_frappe_client] = get_mock_frappe_client

	# CRITICAL: Also set the _frappe_client singleton so that service factory
	# functions (get_plan_service, get_settings_service, etc.) which call
	# get_frappe_client() directly (not via Depends) also get the mock.
	deps_mod._frappe_client = mock_frappe

	# Set redis_pool on app.state so middleware (e.g., GlobalRateLimitMiddleware) can access it
	app.state.redis_pool = redis_client.connection_pool

	# Set LiveChallengeService singleton on app.state (used by deps.py)
	from fastapi_app.services.live_challenge import LiveChallengeService

	lc_service = LiveChallengeService(redis_client, mock_frappe)
	app.state.live_challenge_service = lc_service

	transport = ASGITransport(app=app)
	client = AsyncClient(transport=transport, base_url="http://test")

	yield client

	# Cleanup: Close client, clear dependency overrides, reset singleton
	await client.aclose()
	app.dependency_overrides.clear()
	deps_mod._frappe_client = None


@pytest.fixture
async def authed_client(
	app_client: AsyncClient,
	redis_client: redis.Redis,
	make_player_token,
) -> AsyncGenerator[tuple[AsyncClient, str, str, str], None]:
	"""
	FastAPI test client authenticated as a regular player.

	Sets up:
	1. Player JWT token via make_player_token factory
	2. Redis session key with family_id for auth validation
	3. Authorization header on client for all requests
	4. Returns client, token, player_id, and family_id for assertions

	Args:
		app_client: Test client fixture with dependency overrides
		redis_client: Test Redis fixture
		make_player_token: Token factory fixture

	Yields:
		Tuple of (client, token_str, player_id, family_id) for use
		in player endpoint tests.
	"""
	# Create player token and family_id with unique player ID
	player_id = f"PLAYER-TEST-{uuid4().hex[:8]}"
	token, family_id = make_player_token(player_id=player_id)

	# Seed session in Redis for auth validation
	sess_key = _session_key_fn(player_id)
	await redis_client.set(sess_key, json.dumps({"fid": family_id}))

	# Set Authorization header
	app_client.headers["Authorization"] = f"Bearer {token}"

	yield (app_client, token, player_id, family_id)

	# Cleanup: Remove Authorization header and session key
	if "Authorization" in app_client.headers:
		del app_client.headers["Authorization"]
	await redis_client.delete(sess_key)


@pytest.fixture
async def admin_client(
	app_client: AsyncClient,
	redis_client: redis.Redis,
	make_admin_token,
) -> AsyncGenerator[tuple[AsyncClient, str, str, str], None]:
	"""
	FastAPI test client authenticated as an admin.

	Sets up:
	1. Admin JWT token with System Manager role via make_admin_token factory
	2. Redis session key with family_id for auth validation
	3. Authorization header on client for all requests
	4. Returns client, token, email, and family_id for assertions

	Args:
		app_client: Test client fixture with dependency overrides
		redis_client: Test Redis fixture
		make_admin_token: Admin token factory fixture

	Yields:
		Tuple of (client, token_str, email, family_id) for use
		in admin endpoint tests.
	"""
	# Create admin token and family_id with unique email
	email = f"admin-test-{uuid4().hex[:8]}@test.local"
	token, family_id = make_admin_token(email=email)

	# Seed session in Redis for auth validation
	sess_key = _session_key_fn(email)
	await redis_client.set(sess_key, json.dumps({"fid": family_id}))

	# Set Authorization header
	app_client.headers["Authorization"] = f"Bearer {token}"

	yield (app_client, token, email, family_id)

	# Cleanup: Remove Authorization header and session key
	if "Authorization" in app_client.headers:
		del app_client.headers["Authorization"]
	await redis_client.delete(sess_key)


# === Redis Seeding Helpers (plain async functions, not fixtures) ===


def make_hierarchy_json(
	subject_id: str,
	has_free_content: bool = False,
	lesson_count: int = 1,
	**overrides,
) -> dict:
	"""
	Build a minimal hierarchy JSON structure for tests.

	Args:
		subject_id: Subject ID (e.g., "SUB-TEST-001")
		has_free_content: If True, mark lessons as free (free_units/free_topics non-empty)
		lesson_count: Number of lessons to generate
		**overrides: Override any top-level keys

	Returns:
		Dict matching MinimalHierarchy schema from data-model.md
	"""
	lessons = [
		{
			"lesson_id": f"LESSON-TEST-{i:03d}",
			"bit_index": i,
			"xp": 10,
			"max_hearts": 3,
			"is_reviewable": True,
		}
		for i in range(lesson_count)
	]

	data = {
		"subject_id": subject_id,
		"version": 1,
		"is_linear": False,
		"bit_range": lesson_count,
		"excluded_bits": [],
		"free_units": ["UNIT-TEST-001"] if has_free_content else [],
		"free_topics": ["TOPIC-TEST-001"] if has_free_content else [],
		"tracks": [
			{
				"track_id": "TRK-TEST-001",
				"is_linear": False,
				"units": [
					{
						"unit_id": "UNIT-TEST-001",
						"is_linear": False,
						"is_free": has_free_content,
						"topics": [
							{
								"topic_id": "TOPIC-TEST-001",
								"is_linear": False,
								"is_free": has_free_content,
								"lessons": lessons,
							}
						],
					}
				],
			}
		],
	}

	data.update(overrides)
	return data


async def seed_hierarchy(
	redis: redis.Redis,
	subject_id: str,
	hierarchy_json: dict | None = None,
	**overrides,
) -> None:
	"""
	Seed hierarchy cache in Redis.

	Args:
		redis: Redis async client
		subject_id: Subject ID
		hierarchy_json: Pre-built hierarchy dict (if None, make_hierarchy_json called)
		**overrides: Overrides for make_hierarchy_json if hierarchy_json is None
	"""
	if hierarchy_json is None:
		hierarchy_json = make_hierarchy_json(subject_id, **overrides)

	key = _hierarchy_key_fn(subject_id)
	await redis.set(key, json.dumps(hierarchy_json), ex=3600)


async def seed_game_session(
	redis: redis.Redis,
	user_id: str,
	lesson_id: str,
	subject_id: str,
	**overrides,
) -> None:
	"""
	Seed active game session hash in Redis.

	Args:
		redis: Redis async client
		user_id: User/player ID
		lesson_id: Lesson ID in active session
		subject_id: Subject ID
		**overrides: Additional fields to merge into session data
	"""
	session_data = {
		"user_id": user_id,
		"lesson_id": lesson_id,
		"subject_id": subject_id,
		"session_id": f"SESSION-{uuid4().hex[:16]}",
		"started_at": "2026-02-17T00:00:00",
	}
	session_data.update(overrides)

	key = _game_session_key_fn(user_id)
	await redis.hset(key, mapping=session_data)


async def seed_settings(redis: redis.Redis) -> None:
	"""
	Seed gamification settings cache in Redis.

	Uses defaults from data-model.md GamificationSettings.
	"""
	settings = {
		"base_lesson_xp": 10,
		"replay_xp": 3,
		"max_hearts": 3,
		"xp_per_heart": 2,
		"max_streak_multiplier_percent": 50,
		"session_timeout_days": 30,
		"max_devices_per_player": 3,
	}

	key = gamification_settings_key()
	await redis.set(key, json.dumps(settings))


async def seed_wallet(
	redis: redis.Redis,
	player_id: str,
	xp: int = 0,
	streak: int = 0,
) -> None:
	"""
	Seed wallet hash in Redis.

	Args:
		redis: Redis async client
		player_id: Player ID
		xp: XP balance (default 0)
		streak: Streak count (default 0)
	"""
	wallet_data = {
		"xp": str(xp),
		"streak": str(streak),
	}

	key = _wallet_key_fn(player_id)
	await redis.hset(key, mapping=wallet_data)


async def seed_access_grants(
	redis: redis.Redis,
	player_id: str,
	keys: list[str],
) -> None:
	"""
	Seed access grant set in Redis.

	Args:
		redis: Redis async client
		player_id: Player ID
		keys: List of content keys to grant access to (e.g., ["SUB-MATH", "SUB-SCIENCE"])
	"""
	if keys:
		key = _access_key_fn(player_id)
		await redis.sadd(key, *keys)


async def cleanup_player_keys(
	redis: redis.Redis,
	player_id: str,
) -> None:
	"""
	Delete all memora:* Redis keys for a player.

	Scans and deletes:
	- memora:session:{player_id}
	- memora:wallet:{player_id}
	- memora:access:{player_id}
	- memora:progress:{player_id}:*
	- memora:stats:{player_id}:*
	- memora:gamesession:{player_id}
	- etc.

	Args:
		redis: Redis async client
		player_id: Player ID
	"""
	pattern = f"memora:*{player_id}*"
	cursor = 0
	while True:
		cursor, keys = await redis.scan(cursor, match=pattern, count=100)
		if keys:
			await redis.delete(*keys)
		if cursor == 0:
			break
