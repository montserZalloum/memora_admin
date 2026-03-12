"""Tests for archive_job_cleanup task."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from memora_admin.tasks.archive_job_cleanup import (
	DEFAULT_BATCH_SIZE,
	DEFAULT_FAILED_RETENTION_DAYS,
	DEFAULT_PURGED_RETENTION_DAYS,
	TASK_NAME,
	_do_archive_job_cleanup,
	cleanup_archive_jobs,
)


def _make_archive_job(
	status: str = "Purged",
	modified_dt=None,
	source_doctype: str = "Memora Task Run Log",
	archive_scope: str | None = None,
) -> str:
	"""Insert a Memora Archive Job row and return its name.

	Because Frappe auto-sets ``modified`` on insert, we do a direct SQL
	UPDATE afterwards to set the desired ``modified`` value.
	"""
	if archive_scope is None:
		archive_scope = f"test-cleanup-{uuid.uuid4().hex[:12]}"

	doc = frappe.get_doc(
		{
			"doctype": "Memora Archive Job",
			"source_doctype": source_doctype,
			"archive_scope": archive_scope,
			"schema_version": 1,
			"status": status,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	if modified_dt is not None:
		frappe.db.sql(
			"UPDATE `tabMemora Archive Job` SET modified = %s WHERE name = %s",
			(modified_dt, doc.name),
		)
		frappe.db.commit()

	return doc.name


def _exists(name: str) -> bool:
	return bool(frappe.db.exists("Memora Archive Job", name))


def _count_names(names: list[str]) -> int:
	if not names:
		return 0
	return sum(1 for name in names if _exists(name))


def _make_batch_row(archive_job_id: str, status: str = "Pending") -> str:
	"""Insert a Memora Task Log Archive Batch row linked to an archive job."""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Task Log Archive Batch",
			"source_doctype": "Memora Task Run Log",
			"date_from": "2025-01-01",
			"date_to": "2025-01-02",
			"cutoff_date": "2025-01-03",
			"status": status,
			"archive_job_id": archive_job_id,
			"retry_count": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


class TestArchiveJobCleanup(FrappeTestCase):
	"""Integration tests for archive-job metadata cleanup."""

	def setUp(self):
		super().setUp()
		self._names: list[str] = []
		self._batch_names: list[str] = []

	def tearDown(self):
		if self._batch_names:
			frappe.db.delete(
				"Memora Task Log Archive Batch", {"name": ["in", self._batch_names]}
			)
			frappe.db.commit()
		if self._names:
			frappe.db.delete("Memora Archive Job", {"name": ["in", self._names]})
			frappe.db.commit()
		super().tearDown()

	def _make(
		self,
		status: str = "Purged",
		modified_dt=None,
		source_doctype: str = "Memora Task Run Log",
		archive_scope: str | None = None,
	) -> str:
		name = _make_archive_job(
			status=status,
			modified_dt=modified_dt,
			source_doctype=source_doctype,
			archive_scope=archive_scope,
		)
		self._names.append(name)
		return name

	def _make_batch(self, archive_job_id: str, status: str = "Pending") -> str:
		name = _make_batch_row(archive_job_id=archive_job_id, status=status)
		self._batch_names.append(name)
		return name

	# ------------------------------------------------------------------
	# US1: Automatic cleanup of old successful archive jobs
	# ------------------------------------------------------------------

	def test_zero_row_case_exits_cleanly(self):
		"""T003: Empty table returns (0, 0)."""
		total, batches = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)
		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)

	def test_old_purged_rows_deleted(self):
		"""T004: Purged job older than 30 days is deleted."""
		old = self._make(
			status="Purged",
			modified_dt=now_datetime() - timedelta(days=DEFAULT_PURGED_RETENTION_DAYS + 10),
		)

		total, batches = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertEqual(batches, 1)
		self.assertFalse(_exists(old))

	def test_recent_purged_rows_not_deleted(self):
		"""T005: Purged job within 30 days is preserved."""
		recent = self._make(
			status="Purged",
			modified_dt=now_datetime() - timedelta(days=DEFAULT_PURGED_RETENTION_DAYS - 1),
		)

		total, batches = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)
		self.assertTrue(_exists(recent))

	def test_exact_purged_retention_cutoff(self):
		"""T006: Boundary behavior at the 30-day cutoff."""
		just_inside = self._make(
			status="Purged",
			modified_dt=now_datetime()
			- timedelta(days=DEFAULT_PURGED_RETENTION_DAYS)
			+ timedelta(minutes=1),
		)
		just_outside = self._make(
			status="Purged",
			modified_dt=now_datetime()
			- timedelta(days=DEFAULT_PURGED_RETENTION_DAYS)
			- timedelta(minutes=1),
		)

		total, _ = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertTrue(_exists(just_inside))
		self.assertFalse(_exists(just_outside))

	# ------------------------------------------------------------------
	# US2: Cleanup of old failed archive jobs with extended retention
	# ------------------------------------------------------------------

	def test_old_failed_rows_deleted(self):
		"""T008: Failed job older than 90 days is deleted."""
		old = self._make(
			status="Failed",
			modified_dt=now_datetime() - timedelta(days=DEFAULT_FAILED_RETENTION_DAYS + 10),
		)

		total, batches = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertEqual(batches, 1)
		self.assertFalse(_exists(old))

	def test_recent_failed_rows_not_deleted(self):
		"""T009: Failed job within 90 days is preserved."""
		recent = self._make(
			status="Failed",
			modified_dt=now_datetime() - timedelta(days=DEFAULT_FAILED_RETENTION_DAYS - 1),
		)

		total, batches = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)
		self.assertTrue(_exists(recent))

	# ------------------------------------------------------------------
	# US3: Active jobs are never deleted
	# ------------------------------------------------------------------

	def test_non_terminal_statuses_survive(self):
		"""T011: Jobs in non-terminal statuses survive regardless of age."""
		old_dt = now_datetime() - timedelta(days=DEFAULT_FAILED_RETENTION_DAYS + 30)

		non_terminal = []
		for status in (
			"Pending",
			"Processing",
			"Exported",
			"Transferred",
			"Ingested",
			"Completed",
		):
			non_terminal.append(self._make(status=status, modified_dt=old_dt))

		eligible_purged = self._make(status="Purged", modified_dt=old_dt)
		eligible_failed = self._make(status="Failed", modified_dt=old_dt)

		total, _ = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 2)
		for name in non_terminal:
			self.assertTrue(_exists(name), f"Non-terminal job {name} should survive")
		self.assertFalse(_exists(eligible_purged))
		self.assertFalse(_exists(eligible_failed))

	# ------------------------------------------------------------------
	# US4: Dependency safety with related archive batch rows
	# ------------------------------------------------------------------

	def test_purged_job_with_active_batch_rows_preserved(self):
		"""T012: Purged job with active child batch row is preserved."""
		old_dt = now_datetime() - timedelta(days=DEFAULT_PURGED_RETENTION_DAYS + 10)
		job = self._make(status="Purged", modified_dt=old_dt)
		self._make_batch(archive_job_id=job, status="Pending")

		total, _ = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 0)
		self.assertTrue(_exists(job))

	def test_purged_job_with_terminal_batch_rows_deleted(self):
		"""T013: Purged job with only terminal child batch rows is deleted."""
		old_dt = now_datetime() - timedelta(days=DEFAULT_PURGED_RETENTION_DAYS + 10)
		job = self._make(status="Purged", modified_dt=old_dt)
		self._make_batch(archive_job_id=job, status="Purged")

		total, _ = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertFalse(_exists(job))

	def test_purged_job_with_no_batch_rows_deleted(self):
		"""T014: Purged job with no child batch rows is deleted."""
		old_dt = now_datetime() - timedelta(days=DEFAULT_PURGED_RETENTION_DAYS + 10)
		job = self._make(status="Purged", modified_dt=old_dt)

		total, _ = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertFalse(_exists(job))

	# ------------------------------------------------------------------
	# US5: Batched deletion with per-batch commits
	# ------------------------------------------------------------------

	def test_multiple_batches_required(self):
		"""T016: 5 eligible rows with batch_size=2 produces 3 batches."""
		for i in range(5):
			self._make(
				status="Purged",
				modified_dt=now_datetime()
				- timedelta(days=DEFAULT_PURGED_RETENTION_DAYS + 30 + i),
			)

		total, batches = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=2,
		)

		self.assertEqual(total, 5)
		self.assertEqual(batches, 3)

	def test_commits_incrementally_per_batch(self):
		"""T017: Each batch triggers a separate commit."""
		for i in range(5):
			self._make(
				status="Purged",
				modified_dt=now_datetime()
				- timedelta(days=DEFAULT_PURGED_RETENTION_DAYS + 20 + i),
			)

		with patch(
			"memora_admin.tasks.archive_job_cleanup.frappe.db.commit",
			wraps=frappe.db.commit,
		) as mock_commit:
			total, batches = _do_archive_job_cleanup(
				purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
				failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
				batch_size=2,
			)

		self.assertEqual(total, 5)
		self.assertEqual(batches, 3)
		self.assertEqual(mock_commit.call_count, 3)

	def test_safe_rerun_after_partial_completion(self):
		"""T018: First batch committed before crash; rerun cleans remainder."""
		old_names = [
			self._make(
				status="Purged",
				modified_dt=now_datetime()
				- timedelta(days=DEFAULT_PURGED_RETENTION_DAYS + 40 + i),
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
			"memora_admin.tasks.archive_job_cleanup.frappe.db.delete",
			side_effect=flaky_delete,
		):
			with self.assertRaises(RuntimeError):
				_do_archive_job_cleanup(
					purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
					failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
					batch_size=2,
				)

		self.assertEqual(_count_names(old_names), 2)

		total, batches = _do_archive_job_cleanup(
			purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
			failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
			batch_size=2,
		)

		self.assertEqual(total, 2)
		self.assertEqual(batches, 1)
		self.assertEqual(_count_names(old_names), 0)

	# ------------------------------------------------------------------
	# Polish: Wrapper observability tests
	# ------------------------------------------------------------------

	def test_wrapper_emits_logs_and_metrics_on_success(self):
		"""T020: Wrapper logs, metrics, and task run on success."""
		with patch(
			"memora_admin.tasks.archive_job_cleanup._do_archive_job_cleanup",
			return_value=(7, 2),
		), patch(
			"memora_admin.tasks.archive_job_cleanup.log_task_run"
		) as mock_log, patch(
			"memora_admin.tasks.archive_job_cleanup.logger"
		) as mock_logger, patch(
			"memora_admin.tasks.archive_job_cleanup.TASK_RUNS"
		) as mock_task_runs, patch(
			"memora_admin.tasks.archive_job_cleanup.TASK_DURATION"
		) as mock_duration:
			cleanup_archive_jobs(
				triggered_by="Test",
				purged_retention_days=30,
				failed_retention_days=90,
				batch_size=123,
			)

		self.assertEqual(mock_log.call_count, 1)
		log_kwargs = mock_log.call_args.kwargs
		self.assertEqual(log_kwargs["task_name"], TASK_NAME)
		self.assertEqual(log_kwargs["status"], "Success")
		self.assertEqual(log_kwargs["processed"], 7)
		self.assertEqual(log_kwargs["triggered_by"], "Test")
		self.assertEqual(log_kwargs["failed_details"][0]["purged_retention_days"], 30)
		self.assertEqual(log_kwargs["failed_details"][0]["failed_retention_days"], 90)
		self.assertEqual(log_kwargs["failed_details"][0]["batch_size"], 123)
		self.assertEqual(log_kwargs["failed_details"][0]["batches_executed"], 2)
		self.assertEqual(log_kwargs["failed_details"][0]["rows_deleted"], 7)

		mock_task_runs.labels.assert_called_once_with(task_name=TASK_NAME, status="success")
		mock_task_runs.labels.return_value.inc.assert_called_once_with()
		mock_duration.labels.assert_called_once_with(task_name=TASK_NAME)
		mock_duration.labels.return_value.observe.assert_called_once()

		info_messages = [call.args[0] for call in mock_logger.info.call_args_list]
		self.assertTrue(
			any(
				"starting" in msg
				and "purged_retention_days=30" in msg
				and "failed_retention_days=90" in msg
				and "batch_size=123" in msg
				for msg in info_messages
			)
		)
		self.assertTrue(
			any("done" in msg and "7 rows deleted" in msg and "2 batches" in msg for msg in info_messages)
		)

	def test_wrapper_emits_logs_and_metrics_on_failure(self):
		"""T021: Wrapper logs, metrics, notify, and re-raises on failure."""
		with patch(
			"memora_admin.tasks.archive_job_cleanup._do_archive_job_cleanup",
			side_effect=RuntimeError("injected cleanup failure"),
		), patch(
			"memora_admin.tasks.archive_job_cleanup.log_task_run"
		) as mock_log, patch(
			"memora_admin.tasks.archive_job_cleanup.notify_admins"
		) as mock_notify, patch(
			"memora_admin.tasks.archive_job_cleanup.logger"
		) as mock_logger, patch(
			"memora_admin.tasks.archive_job_cleanup.TASK_RUNS"
		) as mock_task_runs, patch(
			"memora_admin.tasks.archive_job_cleanup.TASK_DURATION"
		) as mock_duration:
			with self.assertRaises(RuntimeError):
				cleanup_archive_jobs(triggered_by="Test")

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

	def test_per_batch_logs_are_emitted(self):
		"""T022: Each batch emits an info log message."""
		for i in range(5):
			self._make(
				status="Purged",
				modified_dt=now_datetime()
				- timedelta(days=DEFAULT_PURGED_RETENTION_DAYS + 60 + i),
			)

		with patch("memora_admin.tasks.archive_job_cleanup.logger") as mock_logger:
			_do_archive_job_cleanup(
				purged_retention_days=DEFAULT_PURGED_RETENTION_DAYS,
				failed_retention_days=DEFAULT_FAILED_RETENTION_DAYS,
				batch_size=2,
			)

		info_messages = [call.args[0] for call in mock_logger.info.call_args_list]
		batch_messages = [msg for msg in info_messages if "deleted batch" in msg]
		self.assertEqual(len(batch_messages), 3)
