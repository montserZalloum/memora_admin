"""Tests for AccessService - player access grant management via Redis."""

from unittest.mock import AsyncMock

import pytest

from fastapi_app.core.redis_keys import access_key, plan_free_subjects_key
from fastapi_app.services.access import AccessService

# Test constants
TEST_PLAYER = "PLAYER-TEST-001"
TEST_SUBJECT_KEY = "SUB-MATH-G5"
TEST_TRACK_KEY = "TRK-TRACK-001"
TEST_PLAN_ID = "PLAN-TEST-001"


@pytest.fixture
def access_service(redis_client, test_prefix, mock_frappe):
	"""AccessService with all dependencies."""
	return AccessService(redis_client, frappe_client=mock_frappe)


@pytest.fixture
def access_service_no_frappe(redis_client, test_prefix):
	"""AccessService without FrappeClient (for hydration skip tests)."""
	return AccessService(redis_client, frappe_client=None)


class TestGrantRevoke:
	"""Tests for grant_access and revoke_access methods."""

	async def test_grant_access_sadd(self, redis_client, test_prefix, access_service):
		"""Grant multiple keys - returns count of new grants."""
		result = await access_service.grant_access(TEST_PLAYER, [TEST_SUBJECT_KEY, TEST_TRACK_KEY])
		assert result == 2

		# Verify SMEMBERS
		key = access_key(TEST_PLAYER)
		members = await redis_client.smembers(key)
		assert members == {TEST_SUBJECT_KEY, TEST_TRACK_KEY}

	async def test_revoke_access_srem(self, redis_client, test_prefix, access_service):
		"""Grant then revoke - returns count of removed items."""
		# Setup: grant both keys
		await access_service.grant_access(TEST_PLAYER, [TEST_SUBJECT_KEY, TEST_TRACK_KEY])

		# Revoke one
		result = await access_service.revoke_access(TEST_PLAYER, [TEST_SUBJECT_KEY])
		assert result == 1

		# Verify remaining
		key = access_key(TEST_PLAYER)
		members = await redis_client.smembers(key)
		assert members == {TEST_TRACK_KEY}


class TestCheckAccess:
	"""Tests for check_access method."""

	async def test_check_access_granted_true(self, redis_client, test_prefix, access_service):
		"""Check access for granted key returns True."""
		# Grant the key
		await access_service.grant_access(TEST_PLAYER, [TEST_SUBJECT_KEY])

		# Check
		result = await access_service.check_access(TEST_PLAYER, TEST_SUBJECT_KEY)
		assert result is True

	async def test_check_access_ungranted_false(self, redis_client, test_prefix, mock_frappe, access_service):
		"""Check ungranted key attempts hydration and returns False."""
		# Empty cache - no grant
		result = await access_service.check_access(TEST_PLAYER, TEST_SUBJECT_KEY)
		assert result is False

		# Verify hydration was called
		mock_frappe.call.assert_called_once()
		call_args = mock_frappe.call.call_args
		assert call_args[0][0] == "memora_admin.api.subscriptions.get_player_access_keys"
		assert call_args[0][1]["player_id"] == TEST_PLAYER

	async def test_grant_idempotent(self, redis_client, test_prefix, access_service):
		"""Grant same key twice - second returns 0 (no new grants)."""
		# First grant
		result1 = await access_service.grant_access(TEST_PLAYER, [TEST_SUBJECT_KEY])
		assert result1 == 1

		# Second grant (idempotent)
		result2 = await access_service.grant_access(TEST_PLAYER, [TEST_SUBJECT_KEY])
		assert result2 == 0


class TestPlanAccess:
	"""Tests for check_access_with_plan method."""

	async def test_check_with_plan_explicit_first(self, redis_client, test_prefix, access_service):
		"""Explicit grant takes priority over plan."""
		# Grant the key
		await access_service.grant_access(TEST_PLAYER, [TEST_SUBJECT_KEY])

		# Check with plan (plan should not be consulted)
		result = await access_service.check_access_with_plan(TEST_PLAYER, TEST_SUBJECT_KEY, TEST_PLAN_ID)
		assert result is True

	async def test_check_with_plan_fallback(self, redis_client, test_prefix, access_service):
		"""Plan free subjects provide access fallback."""
		# Setup: plan has free subjects (no explicit grant)
		plan_free_key = plan_free_subjects_key(TEST_PLAN_ID)
		subject_id = TEST_SUBJECT_KEY.replace("SUB-", "")
		await redis_client.sadd(plan_free_key, subject_id)

		# Check with plan
		result = await access_service.check_access_with_plan(TEST_PLAYER, TEST_SUBJECT_KEY, TEST_PLAN_ID)
		assert result is True

	async def test_check_with_plan_track_key_no_plan(self, redis_client, test_prefix, access_service):
		"""Track keys skip plan check."""
		# Setup: plan has free subjects but we're checking a track
		plan_free_key = plan_free_subjects_key(TEST_PLAN_ID)
		await redis_client.sadd(plan_free_key, "SOME-SUBJECT")

		# Check track key with plan (should skip plan check for TRK- keys)
		result = await access_service.check_access_with_plan(TEST_PLAYER, TEST_TRACK_KEY, TEST_PLAN_ID)
		assert result is False


class TestHydration:
	"""Tests for ensure_hydrated method."""

	async def test_hydration_skips_when_exists(self, redis_client, test_prefix, mock_frappe, access_service):
		"""Hydration skips if access set already exists."""
		# Pre-seed access set
		key = access_key(TEST_PLAYER)
		await redis_client.sadd(key, "SUB-X")

		# Call hydration
		await access_service.ensure_hydrated(TEST_PLAYER)

		# Verify frappe was NOT called
		mock_frappe.call.assert_not_called()

	async def test_hydration_calls_frappe(self, redis_client, test_prefix, mock_frappe, access_service):
		"""Hydration calls Frappe and seeds Redis on cache miss."""
		# Setup mock return value
		mock_frappe.call.return_value = [TEST_SUBJECT_KEY, TEST_TRACK_KEY]

		# Call hydration on empty cache
		await access_service.ensure_hydrated(TEST_PLAYER)

		# Verify frappe was called with correct args
		mock_frappe.call.assert_called_once_with(
			"memora_admin.api.subscriptions.get_player_access_keys",
			{"player_id": TEST_PLAYER},
		)

		# Verify Redis was seeded
		key = access_key(TEST_PLAYER)
		members = await redis_client.smembers(key)
		assert members == {TEST_SUBJECT_KEY, TEST_TRACK_KEY}

	async def test_hydration_no_client_logs_warning(
		self, redis_client, test_prefix, access_service_no_frappe
	):
		"""Hydration gracefully skips when no FrappeClient."""
		# Call hydration with no frappe client
		await access_service_no_frappe.ensure_hydrated(TEST_PLAYER)

		# Verify no crash - access set remains empty
		key = access_key(TEST_PLAYER)
		members = await redis_client.smembers(key)
		assert members == set()
