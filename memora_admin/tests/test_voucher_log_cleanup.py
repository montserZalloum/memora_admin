"""Tests for voucher_log_cleanup task.

Integration tests following the same pattern as test_task_log_archive_batch_cleanup.py.
Uses Frappe FrappeTestCase with raw SQL insertion and tearDown cleanup.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from memora_admin.tasks.voucher_log_cleanup import (
	DEFAULT_BATCH_SIZE,
	DEFAULT_RETENTION_DAYS,
	TASK_NAME,
	_do_voucher_log_cleanup,
	cleanup_voucher_redemption_logs,
)

DOCTYPE = "Memora Voucher Redemption Log"
TABLE = f"tab{DOCTYPE}"
_PREFIX = "VRLOG-VLC-"


def _insert_row(creation=None, name_suffix=None) -> str:
	"""Insert a voucher redemption log row via raw SQL and return its name."""
	name = f"{_PREFIX}{name_suffix or uuid.uuid4().hex[:10]}"
	ts = creation or now_datetime()
	now = now_datetime()
	frappe.db.sql(
		f"""
		INSERT INTO `{TABLE}`
			(name, creation, modified, owner, modified_by, docstatus,
			 player, pin_masked, status, timestamp)
		VALUES (%s, %s, %s, 'Administrator', 'Administrator', 0,
				'VLC-TEST-PLAYER', '****0000', 'Success', %s)
		""",
		(name, ts, now, now),
	)
	return name


def _bulk_insert_rows(count: int, creation) -> list[str]:
	"""Insert many rows via batched SQL for performance."""
	names = []
	now = now_datetime()
	chunk_size = 500

	for start in range(0, count, chunk_size):
		end = min(start + chunk_size, count)
		n = end - start
		chunk_names = [f"{_PREFIX}BLK-{start + i:05d}" for i in range(n)]
		names.extend(chunk_names)

		values = []
		for cname in chunk_names:
			values.extend([cname, creation, now, now])

		placeholders = ",".join(
			[
				"(%s, %s, %s, 'Administrator', 'Administrator', 0,"
				" 'VLC-TEST-PLAYER', '****0000', 'Success', %s)"
			]
			* n
		)
		frappe.db.sql(
			f"""
			INSERT INTO `{TABLE}`
				(name, creation, modified, owner, modified_by, docstatus,
				 player, pin_masked, status, timestamp)
			VALUES {placeholders}
			""",
			tuple(values),
		)

	return names


def _exists(name: str) -> bool:
	return bool(frappe.db.sql(f"SELECT 1 FROM `{TABLE}` WHERE name = %s", (name,)))


class TestVoucherLogCleanup(FrappeTestCase):
	"""Integration tests for voucher redemption log cleanup."""

	def setUp(self):
		super().setUp()
		self._names: list[str] = []
		# Clean up any leftover test rows from prior failed runs
		frappe.db.sql(f"DELETE FROM `{TABLE}` WHERE name LIKE %s", (f"{_PREFIX}%",))
		frappe.db.commit()

	def tearDown(self):
		if self._names:
			for i in range(0, len(self._names), 500):
				batch = self._names[i : i + 500]
				ph = ",".join(["%s"] * len(batch))
				frappe.db.sql(
					f"DELETE FROM `{TABLE}` WHERE name IN ({ph})", tuple(batch)
				)
			frappe.db.commit()
		super().tearDown()

	def _make(self, creation=None, name_suffix=None) -> str:
		name = _insert_row(creation=creation, name_suffix=name_suffix)
		self._names.append(name)
		return name

	def _bulk_make(self, count: int, creation) -> list[str]:
		names = _bulk_insert_rows(count, creation)
		self._names.extend(names)
		frappe.db.commit()
		return names

	# ---- T004: Zero eligible rows → zero deletions ----

	def test_zero_eligible_rows_exits_cleanly(self):
		total, batches = _do_voucher_log_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)
		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)

	# ---- T005: Old rows deleted ----

	def test_old_rows_are_deleted(self):
		old = self._make(
			creation=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 10),
		)
		frappe.db.commit()

		total, batches = _do_voucher_log_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertEqual(batches, 1)
		self.assertFalse(_exists(old))

	# ---- T006: Recent rows preserved ----

	def test_recent_rows_not_deleted(self):
		recent = self._make(
			creation=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS - 1),
		)
		frappe.db.commit()

		total, batches = _do_voucher_log_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 0)
		self.assertEqual(batches, 0)
		self.assertTrue(_exists(recent))

	# ---- T007: Boundary (exactly 100 days = NOT deleted) ----

	def test_exact_retention_boundary_not_deleted(self):
		# Just inside boundary (not eligible — creation > cutoff)
		boundary = self._make(
			creation=now_datetime()
			- timedelta(days=DEFAULT_RETENTION_DAYS)
			+ timedelta(minutes=1),
		)
		# Just over boundary (eligible — creation < cutoff)
		over = self._make(
			creation=now_datetime()
			- timedelta(days=DEFAULT_RETENTION_DAYS)
			- timedelta(minutes=1),
		)
		frappe.db.commit()

		total, _ = _do_voucher_log_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)

		self.assertEqual(total, 1)
		self.assertTrue(_exists(boundary))
		self.assertFalse(_exists(over))

	# ---- T008: Only Voucher Redemption Log rows affected ----

	def test_only_voucher_log_doctype_affected(self):
		self._make(
			creation=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 10),
		)
		frappe.db.commit()

		with patch(
			"memora_admin.tasks.voucher_log_cleanup.frappe.db.delete",
			wraps=frappe.db.delete,
		) as mock_delete:
			_do_voucher_log_cleanup(
				retention_days=DEFAULT_RETENTION_DAYS,
				batch_size=100,
			)

		self.assertTrue(mock_delete.called)
		for call in mock_delete.call_args_list:
			self.assertEqual(call.args[0], DOCTYPE)

	# ---- T009: Idempotency ----

	def test_idempotent_rerun_returns_zero(self):
		old = self._make(
			creation=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 10),
		)
		frappe.db.commit()

		total1, _ = _do_voucher_log_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)
		self.assertEqual(total1, 1)
		self.assertFalse(_exists(old))

		total2, batches2 = _do_voucher_log_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=100,
		)
		self.assertEqual(total2, 0)
		self.assertEqual(batches2, 0)

	# ---- T010: 2500 rows → 3 batches (1000+1000+500) ----

	def test_2500_rows_produce_3_batches(self):
		old_creation = now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 30)
		self._bulk_make(2500, creation=old_creation)

		total, batches = _do_voucher_log_cleanup(
			retention_days=DEFAULT_RETENTION_DAYS,
			batch_size=DEFAULT_BATCH_SIZE,
		)

		self.assertEqual(total, 2500)
		self.assertEqual(batches, 3)

	# ---- T011: frappe.db.commit() per batch ----

	def test_commit_called_after_each_batch(self):
		for i in range(5):
			self._make(
				creation=now_datetime()
				- timedelta(days=DEFAULT_RETENTION_DAYS + 20 + i),
			)
		frappe.db.commit()

		with patch(
			"memora_admin.tasks.voucher_log_cleanup.frappe.db.commit",
			wraps=frappe.db.commit,
		) as mock_commit:
			total, batches = _do_voucher_log_cleanup(
				retention_days=DEFAULT_RETENTION_DAYS,
				batch_size=2,
			)

		self.assertEqual(total, 5)
		self.assertEqual(batches, 3)
		self.assertEqual(mock_commit.call_count, 3)

	# ---- T012: Deletion order creation ASC, name ASC ----

	# ---- T015: Successful run logs start, cutoff, batch size, per-batch count, total, duration ----

	def test_per_batch_logs_are_emitted_with_cutoff(self):
		for i in range(5):
			self._make(
				creation=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 60 + i),
			)
		frappe.db.commit()

		with patch("memora_admin.tasks.voucher_log_cleanup.logger") as mock_logger:
			_do_voucher_log_cleanup(
				retention_days=DEFAULT_RETENTION_DAYS,
				batch_size=2,
			)

		info_messages = [call.args[0] for call in mock_logger.info.call_args_list]
		batch_messages = [msg for msg in info_messages if "deleted batch" in msg]
		self.assertEqual(len(batch_messages), 3)
		self.assertTrue(any("cutoff" in msg for msg in info_messages))

	def test_wrapper_emits_logs_and_metrics_on_success(self):
		with patch(
			"memora_admin.tasks.voucher_log_cleanup._do_voucher_log_cleanup",
			return_value=(7, 2),
		), patch(
			"memora_admin.tasks.voucher_log_cleanup.log_task_run"
		) as mock_log, patch(
			"memora_admin.tasks.voucher_log_cleanup.logger"
		) as mock_logger, patch(
			"memora_admin.tasks.voucher_log_cleanup.TASK_RUNS"
		) as mock_task_runs, patch(
			"memora_admin.tasks.voucher_log_cleanup.TASK_DURATION"
		) as mock_duration, patch(
			"memora_admin.tasks.voucher_log_cleanup.USERS_PROCESSED"
		) as mock_processed:
			cleanup_voucher_redemption_logs(
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
			any(
				"starting" in msg and "retention_days=45" in msg and "batch_size=123" in msg
				for msg in info_messages
			)
		)
		self.assertTrue(
			any("done" in msg and "7 rows deleted" in msg and "2 batches" in msg for msg in info_messages)
		)

	# ---- T016: Error during batch is logged with details before re-raise ----

	def test_batch_error_is_logged_before_reraise(self):
		self._make(
			creation=now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 10),
		)
		frappe.db.commit()

		with patch(
			"memora_admin.tasks.voucher_log_cleanup.frappe.db.delete",
			side_effect=RuntimeError("injected batch failure"),
		), patch(
			"memora_admin.tasks.voucher_log_cleanup.logger"
		) as mock_logger:
			with self.assertRaises(RuntimeError):
				_do_voucher_log_cleanup(
					retention_days=DEFAULT_RETENTION_DAYS,
					batch_size=100,
				)

		mock_logger.error.assert_called_once()
		error_msg = mock_logger.error.call_args.args[0]
		self.assertIn("batch", error_msg)
		self.assertIn("injected batch failure", error_msg)

	def test_wrapper_emits_logs_and_metrics_on_failure(self):
		with patch(
			"memora_admin.tasks.voucher_log_cleanup._do_voucher_log_cleanup",
			side_effect=RuntimeError("injected cleanup failure"),
		), patch(
			"memora_admin.tasks.voucher_log_cleanup.log_task_run"
		) as mock_log, patch(
			"memora_admin.tasks.voucher_log_cleanup.notify_admins"
		) as mock_notify, patch(
			"memora_admin.tasks.voucher_log_cleanup.logger"
		) as mock_logger, patch(
			"memora_admin.tasks.voucher_log_cleanup.TASK_RUNS"
		) as mock_task_runs, patch(
			"memora_admin.tasks.voucher_log_cleanup.TASK_DURATION"
		) as mock_duration:
			with self.assertRaises(RuntimeError):
				cleanup_voucher_redemption_logs(triggered_by="Test")

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

	def test_deletion_order_creation_asc_name_asc(self):
		base = now_datetime() - timedelta(days=DEFAULT_RETENTION_DAYS + 50)
		names_by_age = []
		for i in range(4):
			n = self._make(
				creation=base + timedelta(days=i),
				name_suffix=f"ORD-{i:03d}",
			)
			names_by_age.append(n)
		frappe.db.commit()

		deleted_batches: list[list[str]] = []
		original_delete = frappe.db.delete

		def tracking_delete(doctype, filters):
			if doctype == DOCTYPE:
				batch_names = filters.get("name", [None, []])[1]
				deleted_batches.append(list(batch_names))
			return original_delete(doctype, filters)

		with patch(
			"memora_admin.tasks.voucher_log_cleanup.frappe.db.delete",
			side_effect=tracking_delete,
		):
			_do_voucher_log_cleanup(
				retention_days=DEFAULT_RETENTION_DAYS,
				batch_size=2,
			)

		self.assertEqual(len(deleted_batches), 2)
		self.assertEqual(deleted_batches[0], names_by_age[:2])
		self.assertEqual(deleted_batches[1], names_by_age[2:4])
