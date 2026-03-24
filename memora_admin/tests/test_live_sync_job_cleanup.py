"""Tests for live_sync_job_cleanup task.

Coverage:
- No rows exist
- Only recent Completed rows (not deleted)
- Only old Completed rows (all deleted)
- Mixed recent and old Completed rows
- Non-Completed statuses never deleted regardless of age
- Multiple batches required
- Exact boundary at 10 days
- Failure during cleanup (does not report success)
- Rerun after partial progress
- Configurable retention_days
- Configurable batch_size
- Concurrent new rows not touched
- Deterministic multi-batch deletion
- Full task wrapper logs Success
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from memora_admin.tasks.live_sync_job_cleanup import (
	_do_live_sync_job_cleanup,
	cleanup_live_sync_jobs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_live_sync_job(days_ago: float = 0, status: str = "Completed") -> str:
	"""Insert a Memora Live Sync Job row with completed_at backdated by days_ago.

	Returns name.
	"""
	completed_at = now_datetime() - timedelta(days=days_ago) if days_ago else now_datetime()

	doc = frappe.get_doc(
		{
			"doctype": "Memora Live Sync Job",
			"sync_type": "practice_log_live",
			"schema_version": "v1",
			"status": status,
			"completed_at": completed_at if status == "Completed" else None,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	# Backdate completed_at precisely (insert may adjust)
	if status == "Completed" and days_ago:
		frappe.db.sql(
			"UPDATE `tabMemora Live Sync Job` SET completed_at=%s WHERE name=%s",
			(completed_at, doc.name),
		)
		frappe.db.commit()

	return doc.name


def _exists(name: str) -> bool:
	return bool(frappe.db.exists("Memora Live Sync Job", name))


def _count_names(names: list[str]) -> int:
	if not names:
		return 0
	return sum(1 for n in names if _exists(n))


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestLiveSyncJobCleanup(FrappeTestCase):
	"""Integration tests for live_sync_job_cleanup task."""

	def setUp(self):
		super().setUp()
		self._names: list[str] = []

	def tearDown(self):
		if self._names:
			frappe.db.delete("Memora Live Sync Job", {"name": ["in", self._names]})
			frappe.db.commit()
		super().tearDown()

	def _make(self, days_ago: float = 0, status: str = "Completed") -> str:
		name = _make_live_sync_job(days_ago=days_ago, status=status)
		self._names.append(name)
		return name

	# -------------------------------------------------------------------------
	# 1. No rows exist
	# -------------------------------------------------------------------------

	def test_no_rows_exist(self):
		"""Empty table: completes without error, deletes nothing."""
		total, batches = _do_live_sync_job_cleanup(retention_days=10, batch_size=100)
		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)

	# -------------------------------------------------------------------------
	# 2. Only recent Completed rows — not deleted
	# -------------------------------------------------------------------------

	def test_only_recent_rows_not_deleted(self):
		"""Completed rows younger than retention threshold are never touched."""
		r1 = self._make(days_ago=1)
		r2 = self._make(days_ago=9)

		total, _ = _do_live_sync_job_cleanup(retention_days=10, batch_size=100)

		self.assertEqual(total, 0)
		self.assertTrue(_exists(r1))
		self.assertTrue(_exists(r2))

	# -------------------------------------------------------------------------
	# 3. Only old Completed rows — all deleted
	# -------------------------------------------------------------------------

	def test_only_old_rows_deleted(self):
		"""Completed rows older than retention threshold are all removed."""
		old1 = self._make(days_ago=11)
		old2 = self._make(days_ago=30)

		total, _ = _do_live_sync_job_cleanup(retention_days=10, batch_size=100)

		self.assertGreaterEqual(total, 2)
		self.assertFalse(_exists(old1))
		self.assertFalse(_exists(old2))

	# -------------------------------------------------------------------------
	# 4. Mixed rows — only old Completed deleted
	# -------------------------------------------------------------------------

	def test_mixed_rows_only_old_deleted(self):
		"""Old Completed rows deleted; recent Completed rows survive."""
		recent = self._make(days_ago=1)
		old = self._make(days_ago=15)

		_do_live_sync_job_cleanup(retention_days=10, batch_size=100)

		self.assertTrue(_exists(recent), "Recent row must survive")
		self.assertFalse(_exists(old), "Old row must be deleted")

	# -------------------------------------------------------------------------
	# 5. Non-Completed statuses never deleted
	# -------------------------------------------------------------------------

	def test_non_completed_statuses_never_deleted(self):
		"""Pending, Processing, Failed rows are never deleted regardless of age."""
		pending = self._make(days_ago=30, status="Pending")
		processing = self._make(days_ago=30, status="Processing")
		failed = self._make(days_ago=30, status="Failed")

		_do_live_sync_job_cleanup(retention_days=10, batch_size=100)

		self.assertTrue(_exists(pending), "Pending row must survive")
		self.assertTrue(_exists(processing), "Processing row must survive")
		self.assertTrue(_exists(failed), "Failed row must survive")

	# -------------------------------------------------------------------------
	# 6. Multiple batches required
	# -------------------------------------------------------------------------

	def test_multiple_batches_required(self):
		"""Six old rows with batch_size=2 require exactly three batches."""
		old_names = [self._make(days_ago=15) for _ in range(6)]

		total, batches = _do_live_sync_job_cleanup(retention_days=10, batch_size=2)

		self.assertEqual(total, 6)
		self.assertEqual(batches, 3)
		self.assertEqual(_count_names(old_names), 0)

	# -------------------------------------------------------------------------
	# 7. Exact boundary at 10 days
	# -------------------------------------------------------------------------

	def test_exact_boundary_10_days(self):
		"""Row at exactly the retention boundary is kept; row just past it is deleted."""
		boundary_ts = now_datetime() - timedelta(days=10) + timedelta(minutes=1)
		boundary = self._make()
		frappe.db.sql(
			"UPDATE `tabMemora Live Sync Job` SET completed_at=%s WHERE name=%s",
			(boundary_ts, boundary),
		)
		frappe.db.commit()

		over_ts = now_datetime() - timedelta(days=10) - timedelta(minutes=1)
		over = self._make()
		frappe.db.sql(
			"UPDATE `tabMemora Live Sync Job` SET completed_at=%s WHERE name=%s",
			(over_ts, over),
		)
		frappe.db.commit()

		total, _ = _do_live_sync_job_cleanup(retention_days=10, batch_size=100)

		self.assertGreaterEqual(total, 1)
		self.assertTrue(_exists(boundary), "Row inside retention window must survive")
		self.assertFalse(_exists(over), "Row outside retention window must be deleted")

	# -------------------------------------------------------------------------
	# 8. Failure does not report success
	# -------------------------------------------------------------------------

	def test_failure_does_not_report_success(self):
		"""If cleanup raises, the task logs Failed status and re-raises."""
		with patch(
			"memora_admin.tasks.live_sync_job_cleanup._do_live_sync_job_cleanup",
			side_effect=RuntimeError("injected DB failure"),
		):
			with patch("memora_admin.tasks.live_sync_job_cleanup.log_task_run") as mock_log:
				with patch("memora_admin.tasks.live_sync_job_cleanup.notify_admins"):
					with self.assertRaises(RuntimeError):
						cleanup_live_sync_jobs(triggered_by="Test")

		self.assertEqual(len(mock_log.call_args_list), 1)
		self.assertEqual(mock_log.call_args_list[0][1]["status"], "Failed")

	# -------------------------------------------------------------------------
	# 9. Rerun after partial progress
	# -------------------------------------------------------------------------

	def test_rerun_after_partial_progress(self):
		"""Multiple runs converge to full cleanup safely."""
		old_names = [self._make(days_ago=15) for _ in range(4)]

		total1, _ = _do_live_sync_job_cleanup(retention_days=10, batch_size=2)
		total2, _ = _do_live_sync_job_cleanup(retention_days=10, batch_size=2)
		total3, batches3 = _do_live_sync_job_cleanup(retention_days=10, batch_size=2)

		self.assertEqual(total1 + total2, 4)
		self.assertEqual(total3, 0)
		self.assertEqual(batches3, 0)
		self.assertEqual(_count_names(old_names), 0)

	# -------------------------------------------------------------------------
	# 10. Configurable retention_days
	# -------------------------------------------------------------------------

	def test_configurable_retention_days(self):
		"""Custom retention_days shifts the cutoff correctly."""
		old_beyond_30 = self._make(days_ago=31)
		old_within_30 = self._make(days_ago=8)

		_do_live_sync_job_cleanup(retention_days=30, batch_size=100)

		self.assertFalse(_exists(old_beyond_30), "Row beyond 30d must be deleted")
		self.assertTrue(_exists(old_within_30), "Row within 30d must survive")

	# -------------------------------------------------------------------------
	# 11. Configurable batch_size
	# -------------------------------------------------------------------------

	def test_configurable_batch_size(self):
		"""batch_size=5 fits all five rows in a single batch."""
		old_names = [self._make(days_ago=15) for _ in range(5)]

		total, batches = _do_live_sync_job_cleanup(retention_days=10, batch_size=5)

		self.assertEqual(total, 5)
		self.assertEqual(batches, 1)
		self.assertEqual(_count_names(old_names), 0)

	# -------------------------------------------------------------------------
	# 12. Concurrent new rows not touched
	# -------------------------------------------------------------------------

	def test_concurrent_new_rows_not_touched(self):
		"""Rows inserted during cleanup (completed_at = now) are never deleted."""
		old = self._make(days_ago=15)
		concurrent = self._make(days_ago=0)

		_do_live_sync_job_cleanup(retention_days=10, batch_size=100)

		self.assertFalse(_exists(old), "Old row must be deleted")
		self.assertTrue(_exists(concurrent), "Concurrent new row must survive")

	# -------------------------------------------------------------------------
	# 13. Deterministic multi-batch deletion (oldest first)
	# -------------------------------------------------------------------------

	def test_deterministic_multi_batch_deletion(self):
		"""Rows are processed in completed_at ASC order across batches."""
		names = [
			self._make(days_ago=30),  # index 0 — oldest
			self._make(days_ago=25),  # index 1
			self._make(days_ago=20),  # index 2
			self._make(days_ago=15),  # index 3 — newest of the four
		]

		total1, batches1 = _do_live_sync_job_cleanup(retention_days=10, batch_size=2)

		self.assertEqual(total1, 2)
		self.assertEqual(batches1, 1)
		self.assertFalse(_exists(names[0]), "Oldest row must be in first batch")
		self.assertFalse(_exists(names[1]), "Second-oldest must be in first batch")
		self.assertTrue(_exists(names[2]), "Third row survives first batch")
		self.assertTrue(_exists(names[3]), "Newest row survives first batch")

		total2, batches2 = _do_live_sync_job_cleanup(retention_days=10, batch_size=2)
		self.assertEqual(total2, 2)
		self.assertEqual(_count_names(names), 0)

	# -------------------------------------------------------------------------
	# 14. Full task wrapper logs Success
	# -------------------------------------------------------------------------

	def test_full_task_logs_success(self):
		"""cleanup_live_sync_jobs() calls log_task_run with Success on clean run."""
		with patch("memora_admin.tasks.live_sync_job_cleanup.log_task_run") as mock_log:
			cleanup_live_sync_jobs(triggered_by="Test", retention_days=10, batch_size=100)

		success_calls = [c for c in mock_log.call_args_list if c[1].get("status") == "Success"]
		self.assertEqual(len(success_calls), 1)
