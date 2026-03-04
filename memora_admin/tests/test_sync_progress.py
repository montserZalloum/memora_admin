"""
Tests for sync_dirty_progress() task.

Tests verify that progress bitmaps are correctly synced from Redis to MariaDB,
including happy path, edge cases, and error handling.
"""

from unittest.mock import MagicMock, patch

import frappe

from fastapi_app.core.redis_keys import dirty_progress_key
from fastapi_app.core.redis_keys import progress_key as _progress_key_fn
from memora_admin.tasks.sync import sync_dirty_progress
from memora_admin.tests.sync_test_base import SyncTestCase
from memora_admin.tests.voucher_fixtures import make_player


class TestSyncDirtyProgress(SyncTestCase):
	"""
	Integration tests for sync_dirty_progress() function.

	Tests the full pipeline: Redis progress bitmap -> MariaDB Structure Progress record.
	"""

	def setUp(self):
		"""
		Initialize test environment.

		- Create a unique player via make_player(season="SEAS-00027")
		- Find or create a subject for testing
		- Store player_id, subject_id for use in tests
		"""
		super().setUp()

		# Create unique player for this test
		player_doc = make_player(season="SEAS-00027")
		self.player_id = player_doc.name

		# Find an existing subject for testing
		# Try to use an existing subject to avoid partition constraints
		existing_subject = frappe.db.get_value("Memora Subject", {}, "name")
		if existing_subject:
			self.subject_id = existing_subject
		else:
			# Create a minimal subject if none exists
			subject_doc = frappe.get_doc(
				{
					"doctype": "Memora Subject",
					"subject_name": f"TestSubject-{self._test_id}",
					"subject_ar": "موضوع الاختبار",
					"is_premium": 0,
				}
			)
			subject_doc.insert(ignore_permissions=True)
			self.subject_id = subject_doc.name

	def test_bitmap_to_hex_upsert(self):
		"""
		Test: Bitmap bits are correctly converted to hex and Structure Progress is upserted.

		- Seed Redis bitmap with bits 0 and 7 set
		- Mock _batch_get_subject_lesson_counts to return 10
		- Call sync_dirty_progress()
		- Assert: Structure Progress record has correct hex string
		- Assert: completion_percentage is 20.0 (2 bits / 10 lessons * 100)
		"""
		# Seed Redis bitmap with bits 0 and 7 set
		# Bit 0 (first bit of first byte) + Bit 7 (last bit of first byte)
		# 0b10000001 = 0x81
		self._seed_redis_progress(self.player_id, self.subject_id, 1, [0, 7])

		# Verify bits are set in Redis
		bitmap_key = _progress_key_fn(self.player_id, self.subject_id)
		bitmap_bytes = self.r.get(bitmap_key)
		self.assertIsNotNone(bitmap_bytes, "Bitmap should be set in Redis")

		# Mock _get_subject_lesson_count to return 10
		with patch(
			"memora_admin.tasks.sync._batch_get_subject_lesson_counts", return_value={self.subject_id: 10}
		):
			# Run sync
			sync_dirty_progress()

		# Verify Structure Progress record was created with correct hex
		progress = frappe.db.get_value(
			"Memora Structure Progress",
			{"player": self.player_id, "subject": self.subject_id},
			["passed_lessons_bitset", "completion_percentage"],
		)

		self.assertIsNotNone(progress, "Structure Progress record should be created")
		hex_string, completion_pct = progress

		# Bits 0 and 7 set = 0b10000001 = 0x81
		self.assertEqual(hex_string, "81", f"Expected hex '81', got '{hex_string}'")

		# 2 bits set / 10 total lessons * 100 = 20.0%
		self.assertAlmostEqual(completion_pct, 20.0, places=1, msg=f"Expected 20.0%, got {completion_pct}%")

		# Verify removed from dirty set
		dirty_member = f"{self.player_id}:{self.subject_id}:v1"
		is_dirty = self.r.sismember(dirty_progress_key(), dirty_member)
		self.assertFalse(is_dirty, "Progress should be removed from dirty set")

	def test_new_record_created(self):
		"""
		Test: New Structure Progress record is created if it doesn't exist.

		- Ensure no existing Structure Progress for this player/subject
		- Seed Redis bitmap with bit 0
		- Mock _batch_get_subject_lesson_counts to return 5
		- Call sync_dirty_progress()
		- Assert: a new Structure Progress record exists
		"""
		# Ensure no existing record
		existing = frappe.db.exists(
			"Memora Structure Progress", {"player": self.player_id, "subject": self.subject_id}
		)
		if existing:
			frappe.delete_doc("Memora Structure Progress", existing)

		# Seed Redis bitmap with bit 0
		self._seed_redis_progress(self.player_id, self.subject_id, 1, [0])

		# Mock _get_subject_lesson_count to return 5
		with patch(
			"memora_admin.tasks.sync._batch_get_subject_lesson_counts", return_value={self.subject_id: 5}
		):
			# Run sync
			sync_dirty_progress()

		# Verify record was created
		progress_exists = frappe.db.exists(
			"Memora Structure Progress", {"player": self.player_id, "subject": self.subject_id}
		)
		self.assertTrue(progress_exists, "New Structure Progress record should be created")

		# Verify it has non-empty bitset
		bitset = frappe.db.get_value(
			"Memora Structure Progress",
			{"player": self.player_id, "subject": self.subject_id},
			"passed_lessons_bitset",
		)
		self.assertIsNotNone(bitset, "passed_lessons_bitset should be set")
		self.assertNotEqual(bitset, "", "passed_lessons_bitset should not be empty")

	def test_existing_record_updated(self):
		"""
		Test: Existing Structure Progress record is updated with new bitmap.

		- Create an existing Structure Progress record with passed_lessons_bitset="00"
		- Seed Redis bitmap with bits 0, 1, 2
		- Mock _batch_get_subject_lesson_counts to return 10
		- Call sync_dirty_progress()
		- Assert: the existing record's bitset is updated
		"""
		# Create existing record with initial bitset "00"
		initial_bitset = "00"
		existing_progress = frappe.get_doc(
			{
				"doctype": "Memora Structure Progress",
				"player": self.player_id,
				"subject": self.subject_id,
				"passed_lessons_bitset": initial_bitset,
				"completion_percentage": 0,
			}
		)
		existing_progress.insert(ignore_permissions=True)

		# Verify initial state
		initial_record = frappe.db.get_value(
			"Memora Structure Progress",
			{"player": self.player_id, "subject": self.subject_id},
			"passed_lessons_bitset",
		)
		self.assertEqual(initial_record, initial_bitset, f"Initial bitset should be '{initial_bitset}'")

		# Seed Redis bitmap with bits 0, 1, 2
		# 0b11100000 = 0xe0
		self._seed_redis_progress(self.player_id, self.subject_id, 1, [0, 1, 2])

		# Mock _get_subject_lesson_count to return 10
		with patch(
			"memora_admin.tasks.sync._batch_get_subject_lesson_counts", return_value={self.subject_id: 10}
		):
			# Run sync
			sync_dirty_progress()

		# Verify record was updated
		updated_bitset = frappe.db.get_value(
			"Memora Structure Progress",
			{"player": self.player_id, "subject": self.subject_id},
			"passed_lessons_bitset",
		)
		self.assertEqual(updated_bitset, "e0", f"Expected hex 'e0' (bits 0,1,2 set), got '{updated_bitset}'")

	def test_invalid_dirty_member_format(self):
		"""
		Test: Malformed dirty member is skipped with warning, valid members processed.

		- Manually SADD invalid member (missing :v{version})
		- Manually SADD valid member
		- Mock _batch_get_subject_lesson_counts
		- Call sync_dirty_progress()
		- Assert: valid member was processed, invalid was skipped (no crash)
		"""
		# Manually add invalid member to dirty set
		invalid_member = f"{self.player_id}:{self.subject_id}"  # Missing :v{version}
		valid_member = f"{self.player_id}:{self.subject_id}:v1"

		self.r.sadd(dirty_progress_key(), invalid_member)
		self.r.sadd(dirty_progress_key(), valid_member)
		if dirty_progress_key() not in self._cleanup_keys:
			self._cleanup_keys.append(dirty_progress_key())

		# Seed Redis bitmap for valid member only
		self._seed_redis_progress(self.player_id, self.subject_id, 1, [0])

		# Mock _get_subject_lesson_count
		with patch(
			"memora_admin.tasks.sync._batch_get_subject_lesson_counts", return_value={self.subject_id: 10}
		):
			# Run sync - should not crash
			try:
				sync_dirty_progress()
			except Exception as e:
				self.fail(f"sync_dirty_progress() should not crash on invalid member format: {e}")

		# Verify valid member was processed (record created)
		valid_exists = frappe.db.exists(
			"Memora Structure Progress", {"player": self.player_id, "subject": self.subject_id}
		)
		self.assertTrue(valid_exists, "Valid member should be processed and record created")

		# Verify valid member removed from dirty set
		is_valid_dirty = self.r.sismember(dirty_progress_key(), valid_member)
		self.assertFalse(is_valid_dirty, "Valid member should be removed from dirty set")

		# Invalid member should remain or be skipped (either way, no crash is success)

	def test_empty_bitmap(self):
		"""
		Test: Empty bitmap (no bits set) results in empty hex string and 0% completion.

		- Add player to dirty progress set but do NOT set any bitmap bits
		- Mock _batch_get_subject_lesson_counts to return 10
		- Call sync_dirty_progress()
		- Assert: Structure Progress record has passed_lessons_bitset=""
		- Assert: completion_percentage == 0
		"""
		# Add to dirty set but don't create bitmap (no SETBIT calls)
		dirty_member = f"{self.player_id}:{self.subject_id}:v1"
		self.r.sadd(dirty_progress_key(), dirty_member)
		if dirty_progress_key() not in self._cleanup_keys:
			self._cleanup_keys.append(dirty_progress_key())

		# Verify no bitmap exists
		bitmap_key = _progress_key_fn(self.player_id, self.subject_id)
		bitmap_bytes = self.r.get(bitmap_key)
		self.assertIsNone(bitmap_bytes, "Bitmap should not exist")

		# Mock _get_subject_lesson_count to return 10
		with patch(
			"memora_admin.tasks.sync._batch_get_subject_lesson_counts", return_value={self.subject_id: 10}
		):
			# Run sync
			sync_dirty_progress()

		# Verify Structure Progress record has empty bitset and 0% completion
		progress = frappe.db.get_value(
			"Memora Structure Progress",
			{"player": self.player_id, "subject": self.subject_id},
			["passed_lessons_bitset", "completion_percentage"],
		)

		self.assertIsNotNone(progress, "Structure Progress record should be created")
		bitset, completion_pct = progress

		self.assertEqual(bitset, "", f"Expected empty bitset, got '{bitset}'")
		self.assertAlmostEqual(completion_pct, 0.0, places=1, msg=f"Expected 0.0%, got {completion_pct}%")

		# Verify removed from dirty set
		is_dirty = self.r.sismember(dirty_progress_key(), dirty_member)
		self.assertFalse(is_dirty, "Progress should be removed from dirty set")
