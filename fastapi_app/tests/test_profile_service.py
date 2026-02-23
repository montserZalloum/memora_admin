"""Tests for ProfileService - Player profile batch caching."""

import json
import pytest

from fastapi_app.core.redis_keys import profile_key
from fastapi_app.models.profile import PlayerProfile
from fastapi_app.services.profile import ProfileService

# Test constants
TEST_PLAYER_1 = "PLAYER-TEST-PRF-001"
TEST_PLAYER_2 = "PLAYER-TEST-PRF-002"
TEST_PLAYER_3 = "PLAYER-TEST-PRF-003"


@pytest.fixture
async def profile_svc(redis_client, test_prefix, mock_frappe):
	"""ProfileService with test dependencies."""
	return ProfileService(redis_client, frappe_client=mock_frappe)


def _make_test_profile(player_id: str, display_name: str = None) -> dict:
	"""Create a test profile dict."""
	return {
		"player_id": player_id,
		"display_name": display_name or f"Player {player_id[-3:]}",
		"avatar": "avatar_url",
	}


class TestBatchCacheHit:
	"""Batch cache hit returns cached profiles without Frappe call."""

	async def test_tc_prf_01_batch_cache_hit_no_frappe_call(self, profile_svc, redis_client, test_prefix, mock_frappe):
		"""TC-PRF-01: Batch cache hit - Frappe NOT called."""
		# Setup: pre-seed 2 profiles in Redis
		key1 = profile_key(TEST_PLAYER_1)
		key2 = profile_key(TEST_PLAYER_2)
		profile1 = PlayerProfile(player_id=TEST_PLAYER_1, display_name="Player 1", avatar="avatar1")
		profile2 = PlayerProfile(player_id=TEST_PLAYER_2, display_name="Player 2", avatar="avatar2")
		await redis_client.set(key1, profile1.model_dump_json())
		await redis_client.set(key2, profile2.model_dump_json())

		# Action: batch fetch
		result = await profile_svc.get_profiles_batch([TEST_PLAYER_1, TEST_PLAYER_2])

		# Assert: returns cached data
		assert len(result) == 2
		assert result[TEST_PLAYER_1].display_name == "Player 1"
		assert result[TEST_PLAYER_2].display_name == "Player 2"

		# Assert: Frappe NOT called
		mock_frappe.call.assert_not_called()


class TestPartialCacheMiss:
	"""Partial cache miss fetches missing from Frappe."""

	async def test_tc_prf_02_partial_miss_frappe_called_for_missing(self, profile_svc, redis_client, test_prefix, mock_frappe):
		"""TC-PRF-02: Partial miss - Frappe called for missing."""
		# Setup: pre-seed only PLAYER_1
		key1 = profile_key(TEST_PLAYER_1)
		profile1 = PlayerProfile(player_id=TEST_PLAYER_1, display_name="Player 1", avatar="avatar1")
		await redis_client.set(key1, profile1.model_dump_json())

		# Setup: configure mock for PLAYER_2 and PLAYER_3
		mock_frappe.call.return_value = [
			_make_test_profile(TEST_PLAYER_2, "Player 2"),
			_make_test_profile(TEST_PLAYER_3, "Player 3"),
		]

		# Action: batch fetch 3 players
		result = await profile_svc.get_profiles_batch([TEST_PLAYER_1, TEST_PLAYER_2, TEST_PLAYER_3])

		# Assert: all 3 returned
		assert len(result) == 3

		# Assert: Frappe called for missing (PLAYER_2, PLAYER_3)
		mock_frappe.call.assert_called_once()

		# Assert: PLAYER_2 and PLAYER_3 now cached
		key2 = profile_key(TEST_PLAYER_2)
		key3 = profile_key(TEST_PLAYER_3)
		cached2 = await redis_client.get(key2)
		cached3 = await redis_client.get(key3)
		assert cached2 is not None
		assert cached3 is not None


class TestFullMissWithFallback:
	"""Full cache miss applies fallback for still-missing profiles."""

	async def test_tc_prf_03_fallback_for_missing_profiles(self, profile_svc, redis_client, test_prefix, mock_frappe):
		"""TC-PRF-03: Fallback when Frappe returns insufficient data."""
		# Setup: Frappe returns only 1 profile for 2 requests
		mock_frappe.call.return_value = [_make_test_profile(TEST_PLAYER_1, "Player 1")]

		# Action: batch fetch, but Frappe only returns 1
		result = await profile_svc.get_profiles_batch([TEST_PLAYER_1, TEST_PLAYER_2])

		# Assert: both returned (one real, one fallback)
		assert len(result) == 2
		assert result[TEST_PLAYER_1].display_name == "Player 1"
		assert result[TEST_PLAYER_2].display_name.startswith("Anonymous")


class TestEmptyInput:
	"""Empty input returns empty dict immediately."""

	async def test_tc_prf_04_empty_input_returns_empty_dict(self, profile_svc, redis_client, mock_frappe):
		"""TC-PRF-04: Empty input [] returns {} immediately."""
		# Action: batch fetch with empty list
		result = await profile_svc.get_profiles_batch([])

		# Assert: returns empty dict
		assert result == {}

		# Assert: no Redis calls or Frappe calls
		mock_frappe.call.assert_not_called()
