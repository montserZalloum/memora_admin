"""Daily cleanup task for Memora Task Log Archive Batch.

Deletes old terminal archive-batch tracker rows in bounded batches, committing
after each batch so partial progress is preserved on failure.

Retention rule: delete only rows where:
- status = "Purged"
- purged_at < now() - retention_days

Rows with status Pending, Exported, Synced, Failed, or with NULL purged_at are
never deleted.

Scheduled via hooks.py: "30 4 * * *" (daily at 04:30)
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

TASK_NAME = "task_log_archive_batch_cleanup"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 500


def cleanup_task_log_archive_batches(
	triggered_by: str = "Scheduler",
	retention_days: int = DEFAULT_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
):
	"""Delete old Purged rows from Memora Task Log Archive Batch."""
	start_time = now_datetime()

	logger.info(
		f"{TASK_NAME}: starting (retention_days={retention_days}, batch_size={batch_size})"
	)

	try:
		total_deleted, batches_executed = _do_task_log_archive_batch_cleanup(
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


def _do_task_log_archive_batch_cleanup(
	retention_days: int = DEFAULT_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int]:
	"""Delete eligible archive-batch rows in small committed batches."""
	if retention_days < 0:
		raise ValueError("retention_days must be >= 0")
	if batch_size <= 0:
		raise ValueError("batch_size must be > 0")

	cutoff = add_days(now_datetime(), -retention_days)
	total_deleted = 0
	batches_executed = 0

	while True:
		rows = frappe.db.sql(
			"""
			SELECT name
			FROM `tabMemora Task Log Archive Batch`
			WHERE status = %s
			  AND purged_at IS NOT NULL
			  AND purged_at >= %s
			  AND purged_at < %s
			ORDER BY purged_at ASC, name ASC
			LIMIT %s
			""",
			("Purged", "1000-01-01 00:00:00", cutoff, batch_size),
			as_dict=True,
		)

		if not rows:
			break

		names = [row.name for row in rows]

		try:
			frappe.db.delete("Memora Task Log Archive Batch", {"name": ["in", names]})
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
