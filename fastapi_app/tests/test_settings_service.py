"""Tests for SettingsService - Gamification settings caching."""

import pytest

from fastapi_app.models.settings import GamificationSettings
from fastapi_app.services.settings import SettingsService

# Settings cache key is hardcoded
SETTINGS_CACHE_KEY = "memora:settings:gamification"
SETTINGS_SENTINEL_KEY = f"{SETTINGS_CACHE_KEY}:_hydrated"


@pytest.fixture
async def settings_svc(redis_client, mock_frappe):
	"""SettingsService with test dependencies."""
	return SettingsService(redis_client, frappe_client=mock_frappe)


@pytest.fixture(autouse=True)
async def cleanup_settings_key(redis_client):
	"""Auto-cleanup settings cache key after each test."""
	yield
	await redis_client.delete(SETTINGS_CACHE_KEY, SETTINGS_SENTINEL_KEY)


class TestCacheHit:
	"""Cache hit returns cached settings without Frappe call."""

	async def test_tc_set_01_cache_hit_no_frappe_call(self, settings_svc, redis_client, mock_frappe):
		"""TC-SET-01: Cache hit - Frappe NOT called."""
		# Setup: pre-seed settings in Redis
		settings = GamificationSettings(base_lesson_xp=100, replay_xp=50)
		await redis_client.set(SETTINGS_CACHE_KEY, settings.model_dump_json())

		# Action: get settings
		result = await settings_svc.get_gamification_settings()

		# Assert: returns cached data
		assert result.base_lesson_xp == 100
		assert result.replay_xp == 50

		# Assert: Frappe NOT called
		mock_frappe.call.assert_not_called()


class TestCacheMiss:
	"""Cache miss fetches from Frappe and caches result."""

	async def test_tc_set_02_cache_miss_fetches_and_caches(self, settings_svc, redis_client, mock_frappe):
		"""TC-SET-02: Cache miss - fetches from Frappe and caches."""
		# Setup: configure mock
		settings_dict = {
			"base_lesson_xp": 150,
			"replay_xp": 75,
			"max_streak_multiplier_percent": 150,
		}
		mock_frappe.call.return_value = settings_dict

		# Action: get settings
		result = await settings_svc.get_gamification_settings()

		# Assert: returns Frappe data
		assert result.base_lesson_xp == 150
		assert result.replay_xp == 75

		# Assert: Frappe called
		mock_frappe.call.assert_called_once_with("memora_admin.api.settings.get_gamification_settings")

		# Assert: cached in Redis with TTL
		cached = await redis_client.get(SETTINGS_CACHE_KEY)
		assert cached is not None
		cached_obj = GamificationSettings.model_validate_json(cached)
		assert cached_obj.base_lesson_xp == 150

		# Assert: no TTL (persistent — invalidated by Frappe hook on save)
		ttl = await redis_client.ttl(SETTINGS_CACHE_KEY)
		assert ttl == -1

	async def test_tc_set_02b_cache_miss_does_not_leave_sentinel_on_success(
		self,
		settings_svc,
		redis_client,
		mock_frappe,
	):
		"""Successful hydration should not leave an empty-result sentinel behind."""
		mock_frappe.call.return_value = {"base_lesson_xp": 120, "replay_xp": 60}

		await settings_svc.get_gamification_settings()

		assert await redis_client.exists(SETTINGS_CACHE_KEY) == 1
		assert await redis_client.exists(SETTINGS_SENTINEL_KEY) == 0


class TestFrappeUnavailable:
	"""Frappe unavailable returns defaults."""

	async def test_tc_set_03_frappe_unavailable_returns_defaults(
		self, settings_svc, redis_client, mock_frappe
	):
		"""TC-SET-03: Frappe unavailable returns GamificationSettings defaults."""
		# Setup: Frappe returns None
		mock_frappe.call.return_value = None

		# Action: get settings
		result = await settings_svc.get_gamification_settings()

		# Assert: returns default settings
		assert result.base_lesson_xp == 100  # Default value

		# Assert: NOT cached (no key created for None)
		cached = await redis_client.get(SETTINGS_CACHE_KEY)
		assert cached is None
