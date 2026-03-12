"""Tests for task_log_archive_batch_cleanup task."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from memora_admin.tasks.task_log_archive_batch_cleanup import (
	DEFAULT_RETENTION_DAYS,
	TASK_NAME,
	_do_task_log_archive_batch_cleanup,
	cleanup_task_log_archive_batches,
)


def _make_archive_batch(status: str = "Purged", purged_at=None) -> str:
	"""Insert a Memora Task Log Archive Batch row and return its name."""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Task Log Archive Batch",
			"source_doctype": "Memora Task Run Log",
			"date_from": "2025-01-01",
			"date_to": "2025-01-02",
			"cutoff_date": "2025-01-03",
			"status": status,
			"archive_job_id": f"ARCH-{uuid.uuid4().hex[:10]}",
			"retry_count": 0,
			"purged_at": purged_at,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def _exists(name: str) -> bool:
	return bool(frappe.db.exists("Memora Task Log Archive Batch", name))


def _count_names(names: list[str]) -> int:
	if not names:
		return 0
	return sum(1 for name in names if _exists(name))


class TestTaskLogArchiveBatchCleanup(FrappeTestCase):
	"""Integration tests for archive-batch metadata cleanup."""

	def setUp(self):
		super().setUp()
		self._names: list[str] = []

	def tearDown(self):
		if self._names:
			frappe.db.delete("Memora Task Log Archive Batch", {"name": ["in", self._names]})
			frappe.db.commit()
		super().tearDown()

	def _make(self, status: str = "Purged", purged_at=None) -> str:
		name = _make_archive_batch(status=status, purged_at=purged_at)
		self._names.append(name)
		return name

	def test_zero_row_case_exits_cleanly(self):
		total, batches = _do_task_log_archive_batch_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)
		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)

	def test_old_purged_rows_deleted(self):
		old = self._make(
			status="Purged",
			purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 10),
		)

		total, batches = _do_task_log_archive_batch_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertEqual(batches, 1)
		self.assertFalse(_exists(old))

	def test_recent_purged_rows_not_deleted(self):
		recent = self._make(
			status="Purged",
			purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS - 1),
		)

		total, batches = _do_task_log_archive_batch_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)
		self.assertTrue(_exists(recent))

	def test_non_eligible_statuses_and_missing_purged_at_survive(self):
		old_purged = self._make(
			status="Purged",
			purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 5),
		)
		pending = self._make(status="Pending")
		exported = self._make(status="Exported")
		synced = self._make(status="Synced")
		failed = self._make(status="Failed")
		missing_purged_at = self._make(status="Purged", purged_at=None)

		total, _ = _do_task_log_archive_batch_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertFalse(_exists(old_purged))
		self.assertTrue(_exists(pending))
		self.assertTrue(_exists(exported))
		self.assertTrue(_exists(synced))
		self.assertTrue(_exists(failed))
		self.assertTrue(_exists(missing_purged_at))

	def test_exact_retention_cutoff_is_respected(self):
		boundary = self._make(
			status="Purged",
			purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS) + timedelta(minutes=1),
		)
		over_boundary = self._make(
			status="Purged",
			purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS) - timedelta(minutes=1),
		)

		total, _ = _do_task_log_archive_batch_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertTrue(_exists(boundary))
		self.assertFalse(_exists(over_boundary))

	def test_multiple_batches_required(self):
		old_names = [
			self._make(
				status="Purged",
				purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 30 + i),
			)
			for i in range(5)
		]

		total, batches = _do_task_log_archive_batch_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=2,
		)

		self.assertEqual(total, 5)
		self.assertEqual(batches, 3)
		self.assertEqual(_count_names(old_names), 0)

	def test_commits_incrementally_per_batch(self):
		for i in range(5):
			self._make(
				status="Purged",
				purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 20 + i),
			)

		with patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.frappe.db.commit",
			wraps=frappe.db.commit,
		) as mock_commit:
			total, batches = _do_task_log_archive_batch_cleanup(
				retention_days=DEFAULT_RETENTION_DAYS,
				batch_size=2,
			)

		self.assertEqual(total, 5)
		self.assertEqual(batches, 3)
		self.assertEqual(mock_commit.call_count, 3)

	def test_safe_rerun_after_partial_completion(self):
		old_names = [
			self._make(
				status="Purged",
				purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 40 + i),
			)
			for i in range(4)
		]

		original_delete = frappe.db.delete
		delete_calls = {"count": 0}

		def flaky_delete(*args, **kwargs):
			delete_calls["count"] += 1
			if delete_calls["count"] == 2:
				raise RuntimeError("injected delete failure")
			return original_delete(*args, **kwargs)

		with patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.frappe.db.delete",
			side_effect=flaky_delete,
		):
			with self.assertRaises(RuntimeError):
				_do_task_log_archive_batch_cleanup(
					retention_days=DEFAULT_RETENTION_DAYS,
					batch_size=2,
				)

		self.assertEqual(_count_names(old_names), 2)

		total, batches = _do_task_log_archive_batch_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=2,
		)

		self.assertEqual(total, 2)
		self.assertEqual(batches, 1)
		self.assertEqual(_count_names(old_names), 0)

	def test_per_batch_logs_are_emitted(self):
		for i in range(5):
			self._make(
				status="Purged",
				purged_at=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 60 + i),
			)

		with patch("memora_admin.tasks.task_log_archive_batch_cleanup.logger") as mock_logger:
			_do_task_log_archive_batch_cleanup(
				retention_days=DEFAULT_RETENTION_DAYS,
				batch_size=2,
			)

		info_messages = [call.args[0] for call in mock_logger.info.call_args_list]
		batch_messages = [msg for msg in info_messages if "deleted batch" in msg]
		self.assertEqual(len(batch_messages), 3)

	def test_wrapper_emits_logs_and_metrics_on_success(self):
		with patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup._do_task_log_archive_batch_cleanup",
			return_value=(7, 2),
		), patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.log_task_run"
		) as mock_log, patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.logger"
		) as mock_logger, patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.TASK_RUNS"
		) as mock_task_runs, patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.TASK_DURATION"
		) as mock_duration, patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.USERS_PROCESSED"
		) as mock_processed:
			cleanup_task_log_archive_batches(
				triggered_by="Test",
				retention_days=45,
				batch_size=123,
			)

		self.assertEqual(mock_log.call_count, 1)
		log_kwargs = mock_log.call_args.kwargs
		self.assertEqual(log_kwargs["task_name"], TASK_NAME)
		self.assertEqual(log_kwargs["status"], "Success")
		self.assertEqual(log_kwargs["processed"], 7)
		self.assertEqual(log_kwargs["triggered_by"], "Test")
		self.assertEqual(log_kwargs["failed_details"][0]["retention_days"], 45)
		self.assertEqual(log_kwargs["failed_details"][0]["batch_size"], 123)
		self.assertEqual(log_kwargs["failed_details"][0]["batches_executed"], 2)
		self.assertEqual(log_kwargs["failed_details"][0]["rows_deleted"], 7)

		mock_task_runs.labels.assert_called_once_with(task_name=TASK_NAME, status="success")
		mock_task_runs.labels.return_value.inc.assert_called_once_with()
		mock_processed.labels.assert_called_once_with(task_name=TASK_NAME)
		mock_processed.labels.return_value.inc.assert_called_once_with(7)
		mock_duration.labels.assert_called_once_with(task_name=TASK_NAME)
		mock_duration.labels.return_value.observe.assert_called_once()

		info_messages = [call.args[0] for call in mock_logger.info.call_args_list]
		self.assertTrue(
			any("starting" in msg and "retention_days=45" in msg and "batch_size=123" in msg for msg in info_messages)
		)
		self.assertTrue(any("done" in msg and "7 rows deleted" in msg and "2 batches" in msg for msg in info_messages))

	def test_wrapper_emits_logs_and_metrics_on_failure(self):
		with patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup._do_task_log_archive_batch_cleanup",
			side_effect=RuntimeError("injected cleanup failure"),
		), patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.log_task_run"
		) as mock_log, patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.notify_admins"
		) as mock_notify, patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.logger"
		) as mock_logger, patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.TASK_RUNS"
		) as mock_task_runs, patch(
			"memora_admin.tasks.task_log_archive_batch_cleanup.TASK_DURATION"
		) as mock_duration:
			with self.assertRaises(RuntimeError):
				cleanup_task_log_archive_batches(triggered_by="Test")

		self.assertEqual(mock_log.call_count, 1)
		log_kwargs = mock_log.call_args.kwargs
		self.assertEqual(log_kwargs["task_name"], TASK_NAME)
		self.assertEqual(log_kwargs["status"], "Failed")
		self.assertEqual(log_kwargs["error_message"], "injected cleanup failure")
		self.assertEqual(log_kwargs["triggered_by"], "Test")

		mock_notify.assert_called_once_with(TASK_NAME, "injected cleanup failure")
		mock_task_runs.labels.assert_called_once_with(task_name=TASK_NAME, status="failed")
		mock_task_runs.labels.return_value.inc.assert_called_once_with()
		mock_duration.labels.assert_called_once_with(task_name=TASK_NAME)
		mock_duration.labels.return_value.observe.assert_called_once()
		mock_logger.critical.assert_called_once()
