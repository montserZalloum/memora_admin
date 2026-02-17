"""Tests for ProgressService - lesson completion tracking via Redis bitmaps."""

import pytest

from fastapi_app.services.progress import ProgressService
from fastapi_app.core.constants import DIRTY_PROGRESS_KEY

# Test constants
TEST_USER = "USER-TEST-001"
TEST_SUBJECT = "MATH-G5"
TEST_VERSION = 1


@pytest.fixture
def progress_service(redis_client, test_prefix, mock_frappe):
	"""ProgressService with all dependencies."""
	return ProgressService(redis_client, key_prefix=test_prefix, frappe_client=mock_frappe)


@pytest.fixture
def progress_service_no_frappe(redis_client, test_prefix):
	"""ProgressService without FrappeClient (for hydration skip tests)."""
	return ProgressService(redis_client, key_prefix=test_prefix, frappe_client=None)


@pytest.fixture(autouse=True)
async def cleanup_dirty_progress(redis_client):
	"""Auto-cleanup dirty progress keys after each test."""
	yield
	await redis_client.srem(DIRTY_PROGRESS_KEY, f"{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}")


class TestLessonCompletion:
	"""Tests for complete_lesson method."""

	async def test_complete_first_time(self, redis_client, test_prefix, progress_service):
		"""First completion returns False (not replay)."""
		result = await progress_service.complete_lesson(TEST_USER, TEST_SUBJECT, bit_index=5)
		assert result is False

		# Verify SETBIT actually set the bit
		key = f"{test_prefix}progress:{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		bit_value = await redis_client.getbit(key, 5)
		assert bit_value == 1

	async def test_complete_replay(self, redis_client, test_prefix, progress_service):
		"""Replay (bit already set) returns True."""
		key = f"{test_prefix}progress:{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"

		# Pre-set the bit (first completion already done)
		await redis_client.setbit(key, 5, 1)

		# Attempt completion again
		result = await progress_service.complete_lesson(TEST_USER, TEST_SUBJECT, bit_index=5)
		assert result is True

	async def test_complete_marks_dirty(self, redis_client, test_prefix, progress_service):
		"""Completion marks dirty for background sync."""
		await progress_service.complete_lesson(TEST_USER, TEST_SUBJECT, bit_index=5)

		# Verify dirty set membership
		dirty_member = f"{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		is_member = await redis_client.sismember(DIRTY_PROGRESS_KEY, dirty_member)
		assert bool(is_member) is True


class TestReadOperations:
	"""Tests for is_complete and get_completed_count methods."""

	async def test_is_complete_true(self, redis_client, test_prefix, progress_service):
		"""is_complete returns True when bit is set."""
		# Setup: complete the lesson
		await progress_service.complete_lesson(TEST_USER, TEST_SUBJECT, bit_index=5)

		# Check
		result = await progress_service.is_complete(TEST_USER, TEST_SUBJECT, bit_index=5)
		assert result is True

	async def test_is_complete_false(self, redis_client, test_prefix, progress_service):
		"""is_complete returns False for empty bitmap."""
		# No setup - bitmap is empty
		result = await progress_service.is_complete(TEST_USER, TEST_SUBJECT, bit_index=5)
		assert result is False

	async def test_get_completed_count(self, redis_client, test_prefix, progress_service):
		"""get_completed_count counts set bits."""
		# Complete three lessons at different bit positions
		await progress_service.complete_lesson(TEST_USER, TEST_SUBJECT, bit_index=0)
		await progress_service.complete_lesson(TEST_USER, TEST_SUBJECT, bit_index=5)
		await progress_service.complete_lesson(TEST_USER, TEST_SUBJECT, bit_index=10)

		# Count
		result = await progress_service.get_completed_count(TEST_USER, TEST_SUBJECT)
		assert result == 3


class TestHydration:
	"""Tests for ensure_hydrated method with hex bitmap conversion."""

	async def test_hydration_from_hex(self, redis_client, test_prefix, mock_frappe, progress_service):
		"""Hydration converts hex bitset and restores to Redis."""
		# Setup: mock returns hex bitset "8001" (bits 0 and 15 set, MSB-first)
		mock_frappe.call.return_value = {"passed_lessons_bitset": "8001", "completion_percentage": 25}

		# Hydrate
		await progress_service.ensure_hydrated(TEST_USER, TEST_SUBJECT)

		# Verify frappe was called with correct args
		mock_frappe.call.assert_called_once_with(
			"memora_admin.api.subscriptions.get_player_progress",
			{"player_id": TEST_USER, "subject_id": TEST_SUBJECT},
		)

		# Verify bits are set: hex "8001" = bytes \x80\x01
		# \x80 = 10000000 (bit 0 set)
		# \x01 = 00000001 (bit 15 set in MSB-first ordering within the byte)
		key = f"{test_prefix}progress:{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		bit_0 = await redis_client.getbit(key, 0)
		bit_15 = await redis_client.getbit(key, 15)
		assert bit_0 == 1
		assert bit_15 == 1

		# Verify BITCOUNT
		count = await redis_client.bitcount(key)
		assert count == 2

	async def test_hydration_no_client_skips(self, redis_client, test_prefix, progress_service_no_frappe):
		"""Hydration skips gracefully when no FrappeClient."""
		# Hydrate with no frappe client
		await progress_service_no_frappe.ensure_hydrated(TEST_USER, TEST_SUBJECT)

		# Verify bitmap remains empty
		key = f"{test_prefix}progress:{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		bit_0 = await redis_client.getbit(key, 0)
		assert bit_0 == 0
