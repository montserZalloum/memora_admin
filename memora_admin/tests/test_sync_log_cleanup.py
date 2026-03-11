"""Tests for sync_log_cleanup task.

Coverage:
- No rows exist
- Only recent rows (not deleted)
- Only old rows (all deleted)
- Mixed recent and old rows
- Multiple batches required
- Exact boundary at 7 days
- Failure during cleanup (does not report success)
- Rerun after partial progress
- Configurable retention_days
- Configurable batch_size
- Concurrent new rows not touched
- Deterministic multi-batch deletion
- Full task wrapper logs Success
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from memora_admin.tasks.sync_log_cleanup import (
	DEFAULT_BATCH_SIZE,
	DEFAULT_RETENTION_DAYS,
	_do_sync_log_cleanup,
	cleanup_sync_logs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_log(days_ago: float = 0) -> str:
	"""Insert a Memora Sync Log row backdated by days_ago. Returns name."""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Sync Log",
			"job_id": f"test-{uuid.uuid4().hex[:10]}",
			"sync_type": "Wallet",
			"records_processed": 1,
			"status": "Success",
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	if days_ago:
		backdated = now_datetime() - timedelta(days=days_ago)
		frappe.db.sql(
			"UPDATE `tabMemora Sync Log` SET creation=%s WHERE name=%s",
			(backdated, doc.name),
		)
		frappe.db.commit()

	return doc.name


def _exists(name: str) -> bool:
	return bool(frappe.db.exists("Memora Sync Log", name))


def _count_names(names: list[str]) -> int:
	if not names:
		return 0
	return sum(1 for n in names if _exists(n))


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestSyncLogCleanup(FrappeTestCase):
	"""Integration tests for sync_log_cleanup task."""

	def setUp(self):
		super().setUp()
		self._names: list[str] = []

	def tearDown(self):
		if self._names:
			frappe.db.delete("Memora Sync Log", {"name": ["in", self._names]})
			frappe.db.commit()
		super().tearDown()

	def _make(self, days_ago: float = 0) -> str:
		name = _make_sync_log(days_ago=days_ago)
		self._names.append(name)
		return name

	# -------------------------------------------------------------------------
	# 1. No rows exist
	# -------------------------------------------------------------------------

	def test_no_rows_exist(self):
		"""Empty table: completes without error, deletes nothing."""
		total, batches = _do_sync_log_cleanup(retention_days=7, batch_size=100)
		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)

	# -------------------------------------------------------------------------
	# 2. Only recent rows — not deleted
	# -------------------------------------------------------------------------

	def test_only_recent_rows_not_deleted(self):
		"""Rows younger than retention threshold are never touched."""
		r1 = self._make(days_ago=1)
		r2 = self._make(days_ago=6)

		total, _ = _do_sync_log_cleanup(retention_days=7, batch_size=100)

		self.assertEqual(total, 0)
		self.assertTrue(_exists(r1))
		self.assertTrue(_exists(r2))

	# -------------------------------------------------------------------------
	# 3. Only old rows — all deleted
	# -------------------------------------------------------------------------

	def test_only_old_rows_deleted(self):
		"""Rows older than retention threshold are all removed."""
		old1 = self._make(days_ago=8)
		old2 = self._make(days_ago=30)

		total, _ = _do_sync_log_cleanup(retention_days=7, batch_size=100)

		self.assertGreaterEqual(total, 2)
		self.assertFalse(_exists(old1))
		self.assertFalse(_exists(old2))

	# -------------------------------------------------------------------------
	# 4. Mixed rows — only old deleted
	# -------------------------------------------------------------------------

	def test_mixed_rows_only_old_deleted(self):
		"""Old rows deleted; recent rows survive."""
		recent = self._make(days_ago=1)
		old = self._make(days_ago=10)

		_do_sync_log_cleanup(retention_days=7, batch_size=100)

		self.assertTrue(_exists(recent), "Recent row must survive")
		self.assertFalse(_exists(old), "Old row must be deleted")

	# -------------------------------------------------------------------------
	# 5. Multiple batches
	# -------------------------------------------------------------------------

	def test_multiple_batches_required(self):
		"""Six old rows with batch_size=2 require exactly three batches."""
		old_names = [self._make(days_ago=10) for _ in range(6)]

		total, batches = _do_sync_log_cleanup(retention_days=7, batch_size=2)

		self.assertEqual(total, 6)
		self.assertEqual(batches, 3)
		self.assertEqual(_count_names(old_names), 0)

	# -------------------------------------------------------------------------
	# 6. Exact boundary at 7 days
	# -------------------------------------------------------------------------

	def test_exact_boundary_7_days(self):
		"""Row at exactly the retention boundary is kept; row just past it is deleted."""
		# boundary row: creation = now - 7 days + 1 minute (clearly inside window)
		boundary_creation = now_datetime() - timedelta(days=7) + timedelta(minutes=1)
		boundary = self._make()
		frappe.db.sql(
			"UPDATE `tabMemora Sync Log` SET creation=%s WHERE name=%s",
			(boundary_creation, boundary),
		)
		frappe.db.commit()

		# over-boundary row: creation = now - 7 days - 1 minute (clearly outside)
		over_creation = now_datetime() - timedelta(days=7) - timedelta(minutes=1)
		over = self._make()
		frappe.db.sql(
			"UPDATE `tabMemora Sync Log` SET creation=%s WHERE name=%s",
			(over_creation, over),
		)
		frappe.db.commit()

		total, _ = _do_sync_log_cleanup(retention_days=7, batch_size=100)

		self.assertGreaterEqual(total, 1)
		self.assertTrue(_exists(boundary), "Row inside retention window must survive")
		self.assertFalse(_exists(over), "Row outside retention window must be deleted")

	# -------------------------------------------------------------------------
	# 7. Failure does not report success
	# -------------------------------------------------------------------------

	def test_failure_does_not_report_success(self):
		"""If cleanup raises, the task logs Failed status and re-raises."""
		with patch(
			"memora_admin.tasks.sync_log_cleanup._do_sync_log_cleanup",
			side_effect=RuntimeError("injected DB failure"),
		):
			with patch("memora_admin.tasks.sync_log_cleanup.log_task_run") as mock_log:
				with patch("memora_admin.tasks.sync_log_cleanup.notify_admins"):
					with self.assertRaises(RuntimeError):
						cleanup_sync_logs(triggered_by="Test")

		self.assertEqual(len(mock_log.call_args_list), 1)
		self.assertEqual(mock_log.call_args_list[0][1]["status"], "Failed")

	# -------------------------------------------------------------------------
	# 8. Rerun after partial progress
	# -------------------------------------------------------------------------

	def test_rerun_after_partial_progress(self):
		"""Multiple runs converge to full cleanup safely."""
		old_names = [self._make(days_ago=10) for _ in range(4)]

		total1, _ = _do_sync_log_cleanup(retention_days=7, batch_size=2)
		total2, _ = _do_sync_log_cleanup(retention_days=7, batch_size=2)
		total3, batches3 = _do_sync_log_cleanup(retention_days=7, batch_size=2)

		self.assertEqual(total1 + total2, 4)
		self.assertEqual(total3, 0)
		self.assertEqual(batches3, 0)
		self.assertEqual(_count_names(old_names), 0)

	# -------------------------------------------------------------------------
	# 9. Configurable retention_days
	# -------------------------------------------------------------------------

	def test_configurable_retention_days(self):
		"""Custom retention_days shifts the cutoff correctly."""
		old_beyond_30 = self._make(days_ago=31)   # deleted with 30-day retention
		old_within_30 = self._make(days_ago=8)    # kept with 30-day retention

		_do_sync_log_cleanup(retention_days=30, batch_size=100)

		self.assertFalse(_exists(old_beyond_30), "Row beyond 30d must be deleted")
		self.assertTrue(_exists(old_within_30), "Row within 30d must survive")

	# -------------------------------------------------------------------------
	# 10. Configurable batch_size
	# -------------------------------------------------------------------------

	def test_configurable_batch_size(self):
		"""batch_size=5 fits all five rows in a single batch."""
		old_names = [self._make(days_ago=10) for _ in range(5)]

		total, batches = _do_sync_log_cleanup(retention_days=7, batch_size=5)

		self.assertEqual(total, 5)
		self.assertEqual(batches, 1)
		self.assertEqual(_count_names(old_names), 0)

	# -------------------------------------------------------------------------
	# 11. Concurrent new rows not touched
	# -------------------------------------------------------------------------

	def test_concurrent_new_rows_not_touched(self):
		"""Rows inserted during cleanup (creation = now) are never deleted."""
		old = self._make(days_ago=10)
		concurrent = self._make(days_ago=0)   # inserted right now

		_do_sync_log_cleanup(retention_days=7, batch_size=100)

		self.assertFalse(_exists(old), "Old row must be deleted")
		self.assertTrue(_exists(concurrent), "Concurrent new row must survive")

	# -------------------------------------------------------------------------
	# 12. Deterministic multi-batch deletion (oldest first)
	# -------------------------------------------------------------------------

	def test_deterministic_multi_batch_deletion(self):
		"""Rows are processed in creation ASC order across batches."""
		# Four rows with clearly distinct ages (all > 7 days)
		names = [
			self._make(days_ago=20),  # index 0 — oldest
			self._make(days_ago=15),  # index 1
			self._make(days_ago=12),  # index 2
			self._make(days_ago=9),   # index 3 — newest of the four
		]

		# First batch (batch_size=2) should take the two oldest
		total1, batches1 = _do_sync_log_cleanup(retention_days=7, batch_size=2)

		self.assertEqual(total1, 2)
		self.assertEqual(batches1, 1)
		self.assertFalse(_exists(names[0]), "Oldest row must be in first batch")
		self.assertFalse(_exists(names[1]), "Second-oldest must be in first batch")
		self.assertTrue(_exists(names[2]), "Third row survives first batch")
		self.assertTrue(_exists(names[3]), "Newest row survives first batch")

		# Second batch clears the rest
		total2, batches2 = _do_sync_log_cleanup(retention_days=7, batch_size=2)
		self.assertEqual(total2, 2)
		self.assertEqual(_count_names(names), 0)

	# -------------------------------------------------------------------------
	# 13. Full task wrapper logs Success
	# -------------------------------------------------------------------------

	def test_full_task_logs_success(self):
		"""cleanup_sync_logs() calls log_task_run with Success on clean run."""
		with patch("memora_admin.tasks.sync_log_cleanup.log_task_run") as mock_log:
			cleanup_sync_logs(triggered_by="Test", retention_days=7, batch_size=100)

		success_calls = [c for c in mock_log.call_args_list if c[1].get("status") == "Success"]
		self.assertEqual(len(success_calls), 1)
