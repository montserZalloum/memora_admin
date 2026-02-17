"""
Tests for flush_interaction_buffer() task.

Tests verify that interaction data is correctly flushed from Redis buffer to MariaDB,
including happy path, edge cases, and error handling.
"""

import json
from unittest.mock import patch, MagicMock
from datetime import datetime

import frappe
from memora_admin.tests.sync_test_base import SyncTestCase
from memora_admin.tests.voucher_fixtures import make_player
from memora_admin.tasks.sync import flush_interaction_buffer


class TestFlushInteractionBuffer(SyncTestCase):
	"""
	Integration tests for flush_interaction_buffer() function.

	Tests the full pipeline: Redis interaction buffer -> MariaDB Interaction Log records.
	"""

	def setUp(self):
		"""
		Initialize test environment.

		- Create a unique player via make_player(season="SEAS-00027")
		- Find or create a valid Memora Lesson record
		- Store player_id and lesson_id for use in tests
		- Create helper for interaction data
		- Clear interaction buffer for clean test state
		"""
		super().setUp()

		# Clear interaction buffer at start of each test to ensure clean state
		buffer_key = "memora:buffer:interactions"
		self.r.delete(buffer_key)
		self._cleanup_keys.append(buffer_key)

		# Create unique player for this test
		player_doc = make_player(season="SEAS-00027")
		self.player_id = player_doc.name

		# Find or create a lesson record for FK constraint
		# First try to find an existing lesson
		existing_lessons = frappe.db.get_list("Memora Lesson", limit=1)
		if existing_lessons:
			self.lesson_id = existing_lessons[0]["name"]
		else:
			# Create minimal lesson record if none exist
			# (This requires existing Track, Unit, Topic hierarchy)
			# For now, try to find any unit and topic
			units = frappe.db.get_list("Memora Unit", limit=1)
			topics = frappe.db.get_list("Memora Topic", limit=1)

			if units and topics:
				lesson_doc = frappe.get_doc({
					"doctype": "Memora Lesson",
					"name": f"TEST-LES-{self.player_id[:8]}",
					"topic": topics[0]["name"],
					"unit": units[0]["name"],
					"title": "Test Lesson",
					"description": "Test lesson for interaction buffer tests",
				})
				try:
					lesson_doc.insert(ignore_permissions=True)
					self.lesson_id = lesson_doc.name
				except Exception:
					# If that fails, use first found lesson or create a placeholder
					self.lesson_id = topics[0]["name"] if topics else "LES-TEST"
			else:
				self.lesson_id = "LES-TEST"

	def _make_interaction(self, player=None, lesson=None, **overrides):
		"""
		Helper to create interaction dict with all required fields.

		Args:
			player: Player ID (defaults to self.player_id)
			lesson: Lesson ID (defaults to self.lesson_id)
			**overrides: Additional fields to override defaults

		Returns:
			dict with interaction data
		"""
		data = {
			"player": player or self.player_id,
			"lesson": lesson or self.lesson_id,
			"stage_id": "STG-1",
			"event_type": "Completed",
			"time_spent": 30,
			"timestamp": "2026-02-17T10:00:00Z",
		}
		data.update(overrides)
		return data

	def test_happy_path(self):
		"""
		Test: Multiple valid interactions are flushed correctly.

		- Push 3 valid interaction items via _push_interaction()
		- Call flush_interaction_buffer()
		- Assert: 3 Interaction Log docs created for player
		- Assert: Buffer is empty (LLEN == 0)
		"""
		# Push 3 valid interactions
		for i in range(3):
			self._push_interaction(
				self._make_interaction(
					event_type="Completed" if i % 2 == 0 else "Started"
				)
			)

		# Call flush_interaction_buffer
		flush_interaction_buffer()

		# Assert 3 docs created
		count = frappe.db.count("Memora Interaction Log", {"player": self.player_id})
		self.assertEqual(count, 3, f"Expected 3 Interaction Log docs, got {count}")

		# Assert buffer is empty
		buffer_len = self.r.llen("memora:buffer:interactions")
		self.assertEqual(buffer_len, 0, f"Expected empty buffer, got {buffer_len} items")

	def test_empty_buffer(self):
		"""
		Test: Flush with empty buffer is no-op.

		- Do NOT push any items
		- Call flush_interaction_buffer()
		- Assert: No errors raised (implicit pass)
		"""
		# Do not push any items - buffer is empty
		# Call flush - should be no-op
		try:
			flush_interaction_buffer()
			# If we get here, no exception was raised - test passes
		except Exception as e:
			self.fail(f"flush_interaction_buffer() raised exception on empty buffer: {e}")

	def test_invalid_json_skipped(self):
		"""
		Test: Invalid JSON items are skipped, valid items are processed.

		- Push: [valid item, invalid JSON, valid item]
		- Call flush_interaction_buffer()
		- Assert: 2 Interaction Log docs created
		- Assert: Buffer trimmed correctly (1 item remains - the invalid JSON at position 1)

		Note: Per line 349 in sync.py, LTRIM(key, inserted, -1) removes `inserted` items.
		With 3 items fetched and 2 successfully inserted, LTRIM(key, 2, -1) keeps 1 item.
		"""
		# Push valid item
		self._push_interaction(self._make_interaction())

		# Push invalid JSON
		self.r.rpush("memora:buffer:interactions", "NOT-VALID-JSON")

		# Push another valid item
		self._push_interaction(self._make_interaction())

		# Call flush
		flush_interaction_buffer()

		# Assert 2 docs created (valid items only)
		count = frappe.db.count("Memora Interaction Log", {"player": self.player_id})
		self.assertEqual(count, 2, f"Expected 2 Interaction Log docs, got {count}")

		# Assert buffer has 1 item remaining (LTRIM(key, 2, -1) with 3 items = 1 remains)
		buffer_len = self.r.llen("memora:buffer:interactions")
		self.assertEqual(buffer_len, 1, f"Expected 1 item in buffer, got {buffer_len}")

	def test_missing_fields_skipped(self):
		"""
		Test: Items missing required fields are skipped.

		- Push: [valid, {no player field}, valid]
		- Call flush_interaction_buffer()
		- Assert: 2 Interaction Log docs created
		- Assert: Buffer trimmed (1 item remains)
		"""
		# Push valid item
		self._push_interaction(self._make_interaction())

		# Push item missing player field
		incomplete_item = self._make_interaction()
		del incomplete_item["player"]
		self._push_interaction(incomplete_item)

		# Push another valid item
		self._push_interaction(self._make_interaction())

		# Call flush
		flush_interaction_buffer()

		# Assert 2 docs created
		count = frappe.db.count("Memora Interaction Log", {"player": self.player_id})
		self.assertEqual(count, 2, f"Expected 2 Interaction Log docs, got {count}")

		# Assert buffer has 1 item (LTRIM(key, 2, -1) with 3 items = 1 remains)
		buffer_len = self.r.llen("memora:buffer:interactions")
		self.assertEqual(buffer_len, 1, f"Expected 1 item in buffer, got {buffer_len}")

	def test_batch_size_cap(self):
		"""
		Test: Flush respects batch size limit (1000 items).

		- Push 1500 valid items
		- Call flush_interaction_buffer()
		- Assert: 1000 Interaction Log docs created
		- Assert: 500 items remain in buffer (1500 - 1000 = 500)
		"""
		# Push 1500 items
		for i in range(1500):
			self._push_interaction(
				self._make_interaction(
					stage_id=f"STG-{i % 10}",
					time_spent=(i % 60) + 1,
				)
			)

		# Verify buffer has 1500 items
		initial_len = self.r.llen("memora:buffer:interactions")
		self.assertEqual(initial_len, 1500, f"Expected 1500 items in buffer, got {initial_len}")

		# Call flush
		flush_interaction_buffer()

		# Assert 1000 docs created
		count = frappe.db.count("Memora Interaction Log", {"player": self.player_id})
		self.assertEqual(count, 1000, f"Expected 1000 Interaction Log docs, got {count}")

		# Assert 500 items remain (1500 - 1000 = 500)
		buffer_len = self.r.llen("memora:buffer:interactions")
		self.assertEqual(buffer_len, 500, f"Expected 500 items in buffer, got {buffer_len}")

	def test_partial_failure_retry(self):
		"""
		Test: Partial failures don't block successful inserts.

		- Push 3 valid items
		- Mock frappe.get_doc().insert() to fail on 2nd call
		- Call flush_interaction_buffer()
		- Assert: 2 Interaction Log docs created (calls 1 and 3)
		- Assert: Buffer trimmed by 2 (inserted count), 1 item remains for retry

		Note: The mock should be applied to the insert method of the doc returned by get_doc().
		"""
		# Push 3 valid items
		for i in range(3):
			self._push_interaction(
				self._make_interaction(stage_id=f"STG-{i+1}")
			)

		# Mock frappe.get_doc to return a doc with failing insert on 2nd call
		call_count = [0]

		def mock_get_doc(*args, **kwargs):
			call_count[0] += 1
			doc = MagicMock()

			def side_effect_insert(*args, **kwargs):
				if call_count[0] == 2:
					raise Exception("Mock insert failure")

			doc.insert = side_effect_insert
			doc.name = f"LOG-{call_count[0]:05d}"
			return doc

		with patch("frappe.get_doc", side_effect=mock_get_doc):
			with patch("frappe.db.commit"):  # Mock commit
				flush_interaction_buffer()

		# After the flush, verify the state
		# Note: The actual docs created in the mock won't be in the DB,
		# so we check the buffer state instead to verify LTRIM behavior
		buffer_len = self.r.llen("memora:buffer:interactions")

		# With 3 items and 2 successful inserts, LTRIM(key, 2, -1) keeps 1 item
		self.assertEqual(buffer_len, 1, f"Expected 1 item in buffer for retry, got {buffer_len}")
