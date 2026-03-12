"""Daily cleanup task for Memora Voucher Redemption Log.

Deletes old voucher redemption log rows in bounded batches, committing
after each batch so partial progress is preserved on failure.

Retention rule: delete only rows where:
- creation < NOW() - INTERVAL {retention_days} DAY

Scheduled via hooks.py: "30 5 * * *" (daily at 05:30)
"""

from __future__ import annotations

import logging

import frappe
from frappe.utils import add_days, now_datetime

from memora_admin.tasks.task_utils import (
	TASK_DURATION,
	TASK_RUNS,
	USERS_PROCESSED,
	log_task_run,
	notify_admins,
)

logger = logging.getLogger(__name__)

TASK_NAME = "voucher_log_cleanup"
DEFAULT_RETENTION_DAYS = 100
DEFAULT_BATCH_SIZE = 1000


def cleanup_voucher_redemption_logs(
	triggered_by: str = "Scheduler",
	retention_days: int = DEFAULT_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
	"""Delete old Memora Voucher Redemption Log rows."""
	start_time = now_datetime()

	logger.info(
		f"{TASK_NAME}: starting (retention_days={retention_days}, batch_size={batch_size})"
	)

	try:
		total_deleted, batches_executed = _do_voucher_log_cleanup(
			retention_days=retention_days,
			batch_size=batch_size,
		)
		duration = (now_datetime() - start_time).total_seconds()

		log_task_run(
			task_name=TASK_NAME,
			status="Success",
			processed=total_deleted,
			failed_details=[
				{
					"retention_days": retention_days,
					"batch_size": batch_size,
					"batches_executed": batches_executed,
					"rows_deleted": total_deleted,
				}
			],
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=TASK_NAME, status="success").inc()
		if total_deleted:
			USERS_PROCESSED.labels(task_name=TASK_NAME).inc(total_deleted)

		logger.info(
			f"{TASK_NAME}: done — {total_deleted} rows deleted"
			f" in {batches_executed} batches ({duration:.2f}s)"
		)

	except Exception as e:
		logger.critical(f"{TASK_NAME} failed: {e}")

		log_task_run(
			task_name=TASK_NAME,
			status="Failed",
			error_message=str(e),
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=TASK_NAME, status="failed").inc()
		notify_admins(TASK_NAME, str(e))
		raise

	finally:
		duration = (now_datetime() - start_time).total_seconds()
		TASK_DURATION.labels(task_name=TASK_NAME).observe(duration)


def _do_voucher_log_cleanup(
	retention_days: int = DEFAULT_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int]:
	"""Delete eligible voucher redemption log rows in small committed batches."""
	if retention_days < 0:
		raise ValueError("retention_days must be >= 0")
	if batch_size <= 0:
		raise ValueError("batch_size must be > 0")

	cutoff = add_days(now_datetime(), -retention_days)
	logger.info(
		f"{TASK_NAME}: cutoff={cutoff} (retention_days={retention_days}, batch_size={batch_size})"
	)
	total_deleted = 0
	batches_executed = 0

	while True:
		rows = frappe.db.sql(
			"""
			SELECT name
			FROM `tabMemora Voucher Redemption Log`
			WHERE creation < %s
			ORDER BY creation ASC, name ASC
			LIMIT %s
			""",
			(cutoff, batch_size),
			as_dict=True,
		)

		if not rows:
			break

		names = [row.name for row in rows]

		try:
			frappe.db.delete("Memora Voucher Redemption Log", {"name": ["in", names]})
			frappe.db.commit()
		except Exception as e:
			logger.error(
				f"{TASK_NAME}: batch {batches_executed + 1} failed after {total_deleted}"
				f" rows deleted: {e}"
			)
			raise

		batches_executed += 1
		total_deleted += len(names)
		logger.info(f"{TASK_NAME}: deleted batch {batches_executed} of {len(names)} rows")

		if len(names) < batch_size:
			break

	return total_deleted, batches_executed
