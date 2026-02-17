"""Test fixtures and configuration for FastAPI test suite."""

import asyncio
import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

# CRITICAL: Override settings BEFORE any app import
# This prevents lru_cache from caching the production settings
from fastapi_app.core.config import Settings, get_settings
import fastapi_app.core.config as config_module

_test_settings = Settings(
	redis_url="redis://127.0.0.1:13000",
	jwt_secret="test-secret-key-for-unit-tests",
	jwt_algorithm="HS256",
	bitmap_json_path="/tmp/test-bitmaps",
	frappe_url="http://localhost:8000",
	frappe_site="test.local",
	frappe_api_key="test-key",
	frappe_api_secret="test-secret",
	voucher_hmac_secret="test-hmac-secret",
)

get_settings.cache_clear()
config_module.get_settings = lambda: _test_settings

# NOW safe to import app and other dependencies
from fastapi_app.core.security import create_access_token
from fastapi_app.main import app
from fastapi_app.api.deps import get_redis, get_frappe_client
from httpx import ASGITransport


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
		redis.asyncio.Redis client connected to redis://127.0.0.1:13000
		with decode_responses=True for string operations.

	Raises:
		ConnectionError: If Redis is not available at the configured URL.
	"""
	client = redis.Redis.from_url(
		"redis://127.0.0.1:13000",
		decode_responses=True,
	)
	yield client
	await client.aclose()


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
	yield

	# Cleanup: Scan and delete all keys matching test prefix
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(
			cursor,
			match=f"{test_prefix}*",
			count=1000,
		)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


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
	# Override dependencies to use test instances
	app.dependency_overrides[get_redis] = lambda: redis_client
	app.dependency_overrides[get_frappe_client] = lambda: mock_frappe

	transport = ASGITransport(app=app)
	client = AsyncClient(transport=transport, base_url="http://test")

	yield client

	# Cleanup: Close client and clear dependency overrides
	await client.aclose()
	app.dependency_overrides.clear()


@pytest.fixture
async def authed_client(
	app_client: AsyncClient,
	redis_client: redis.Redis,
	make_player_token,
	test_prefix: str,
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
		test_prefix: Test-specific Redis key prefix

	Yields:
		Tuple of (client, token_str, player_id, family_id) for use
		in player endpoint tests.
	"""
	# Create player token and family_id
	token, family_id = make_player_token()
	player_id = "PLAYER-TEST-001"

	# Seed session in Redis for auth validation
	session_key = f"{test_prefix}memora:session:{player_id}"
	await redis_client.set(session_key, json.dumps({"fid": family_id}))

	# Set Authorization header
	app_client.headers["Authorization"] = f"Bearer {token}"

	yield (app_client, token, player_id, family_id)

	# Cleanup: Remove Authorization header
	if "Authorization" in app_client.headers:
		del app_client.headers["Authorization"]


@pytest.fixture
async def admin_client(
	app_client: AsyncClient,
	redis_client: redis.Redis,
	make_admin_token,
	test_prefix: str,
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
		test_prefix: Test-specific Redis key prefix

	Yields:
		Tuple of (client, token_str, email, family_id) for use
		in admin endpoint tests.
	"""
	# Create admin token and family_id
	token, family_id = make_admin_token()
	email = "admin@test.local"

	# Seed session in Redis for auth validation
	session_key = f"{test_prefix}memora:session:{email}"
	await redis_client.set(session_key, json.dumps({"fid": family_id}))

	# Set Authorization header
	app_client.headers["Authorization"] = f"Bearer {token}"

	yield (app_client, token, email, family_id)

	# Cleanup: Remove Authorization header
	if "Authorization" in app_client.headers:
		del app_client.headers["Authorization"]
