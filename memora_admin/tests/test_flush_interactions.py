"""
Tests for flush_interaction_buffer() task.

Tests verify that interaction data is correctly flushed from Redis buffer to MariaDB,
including happy path, edge cases, and error handling.

Uses bulk raw SQL INSERT (no ORM) — see sync.py for implementation details.
"""

import json
from unittest.mock import patch

import frappe
from fastapi_app.core.redis_keys import interaction_buffer_key
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
		buffer_key = interaction_buffer_key()
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
		buffer_len = self.r.llen(interaction_buffer_key())
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
		- Assert: Buffer fully trimmed (invalid items won't succeed on retry)
		"""
		# Push valid item
		self._push_interaction(self._make_interaction())

		# Push invalid JSON
		self.r.rpush(interaction_buffer_key(), "NOT-VALID-JSON")

		# Push another valid item
		self._push_interaction(self._make_interaction())

		# Call flush
		flush_interaction_buffer()

		# Assert 2 docs created (valid items only)
		count = frappe.db.count("Memora Interaction Log", {"player": self.player_id})
		self.assertEqual(count, 2, f"Expected 2 Interaction Log docs, got {count}")

		# Entire batch is trimmed — invalid JSON won't succeed on retry
		buffer_len = self.r.llen(interaction_buffer_key())
		self.assertEqual(buffer_len, 0, f"Expected empty buffer, got {buffer_len}")

	def test_missing_fields_skipped(self):
		"""
		Test: Items missing required fields are skipped.

		- Push: [valid, {no player field}, valid]
		- Call flush_interaction_buffer()
		- Assert: 2 Interaction Log docs created
		- Assert: Buffer fully trimmed
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

		# Entire batch trimmed — missing-field items won't succeed on retry
		buffer_len = self.r.llen(interaction_buffer_key())
		self.assertEqual(buffer_len, 0, f"Expected empty buffer, got {buffer_len}")

	def test_batch_size_cap(self):
		"""
		Test: Flush respects batch size limit (5000 items).

		- Push 6000 valid items
		- Call flush_interaction_buffer()
		- Assert: 5000 Interaction Log docs created
		- Assert: 1000 items remain in buffer (6000 - 5000 = 1000)
		"""
		# Push 6000 items
		for i in range(6000):
			self._push_interaction(
				self._make_interaction(
					stage_id=f"STG-{i % 10}",
					time_spent=(i % 60) + 1,
				)
			)

		# Verify buffer has 6000 items
		initial_len = self.r.llen(interaction_buffer_key())
		self.assertEqual(initial_len, 6000, f"Expected 6000 items in buffer, got {initial_len}")

		# Call flush
		flush_interaction_buffer()

		# Assert 5000 docs created
		count = frappe.db.count("Memora Interaction Log", {"player": self.player_id})
		self.assertEqual(count, 5000, f"Expected 5000 Interaction Log docs, got {count}")

		# Assert 1000 items remain (6000 - 5000 = 1000)
		buffer_len = self.r.llen(interaction_buffer_key())
		self.assertEqual(buffer_len, 1000, f"Expected 1000 items in buffer, got {buffer_len}")

	def test_sql_failure_preserves_buffer(self):
		"""
		Test: If the bulk INSERT fails, the buffer is NOT trimmed.

		- Push 3 valid items
		- Mock frappe.db.sql to raise on INSERT
		- Call flush_interaction_buffer()
		- Assert: Buffer still has 3 items (nothing trimmed)
		"""
		for i in range(3):
			self._push_interaction(
				self._make_interaction(stage_id=f"STG-{i+1}")
			)

		original_sql = frappe.db.sql

		def mock_sql(query, *args, **kwargs):
			if isinstance(query, str) and "INSERT INTO `tabMemora Interaction Log`" in query:
				raise Exception("Mock SQL failure")
			return original_sql(query, *args, **kwargs)

		with patch.object(frappe.db, "sql", side_effect=mock_sql):
			with patch.object(frappe.db, "commit"):
				flush_interaction_buffer()

		# Buffer should be intact — INSERT failed so nothing was trimmed
		buffer_len = self.r.llen(interaction_buffer_key())
		self.assertEqual(buffer_len, 3, f"Expected 3 items in buffer after failure, got {buffer_len}")
