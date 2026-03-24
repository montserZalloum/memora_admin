"""Daily cleanup task for Memora Live Sync Job.

Deletes Completed rows older than DEFAULT_RETENTION_DAYS (10 days) in batches
of DEFAULT_BATCH_SIZE, committing after each batch for safe partial progress.

Retention rule: delete rows where status = 'Completed' AND completed_at < now() - retention_days.
Active rows (Pending, Processing, Exported, Transferred, Ingested) and Failed
rows are never deleted.

Scheduled via hooks.py: "0 6 * * *" (daily at 06:00)
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

TASK_NAME = "live_sync_job_cleanup"
DEFAULT_RETENTION_DAYS = 10
DEFAULT_BATCH_SIZE = 500


def cleanup_live_sync_jobs(
	triggered_by: str = "Scheduler",
	retention_days: int = DEFAULT_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
):
	"""Delete old Completed rows from Memora Live Sync Job.

	Rows with status 'Completed' and completed_at strictly older than
	retention_days are deleted in batches of batch_size. Commits after each
	batch. Safe to rerun after partial completion.

	Args:
		triggered_by: Source of trigger - "Scheduler", "Manual", or "Catch-up"
		retention_days: Delete rows strictly older than this many days (default 10)
		batch_size: Number of rows to delete per batch (default 500)
	"""
	start_time = now_datetime()

	logger.info(
		f"{TASK_NAME}: starting (retention_days={retention_days}, batch_size={batch_size})"
	)

	try:
		total_deleted, batches_executed = _do_live_sync_job_cleanup(retention_days, batch_size)

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
			# USERS_PROCESSED is reused as a generic "items processed" counter
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


def _do_live_sync_job_cleanup(
	retention_days: int = DEFAULT_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int]:
	"""Delete Completed Memora Live Sync Job rows older than retention_days.

	Selects rows with status = 'Completed' and completed_at < cutoff, ordered
	by completed_at ASC then name ASC (oldest first), deletes them in batches,
	and commits after each batch.

	Args:
		retention_days: Rows with completed_at strictly older than this are deleted
		batch_size: Max rows to select and delete per batch

	Returns:
		Tuple of (total_deleted, batches_executed)
	"""
	if retention_days < 0:
		raise ValueError("retention_days must be >= 0")
	if batch_size <= 0:
		raise ValueError("batch_size must be > 0")

	cutoff = add_days(now_datetime(), -retention_days)
	total_deleted = 0
	batches_executed = 0

	while True:
		rows = frappe.get_all(
			"Memora Live Sync Job",
			filters={
				"status": "Completed",
				"completed_at": ["<", cutoff],
			},
			fields=["name"],
			order_by="completed_at asc, name asc",
			limit=batch_size,
		)

		if not rows:
			break

		names = [r.name for r in rows]
		frappe.db.delete("Memora Live Sync Job", {"name": ["in", names]})
		frappe.db.commit()
		total_deleted += len(names)
		batches_executed += 1

		logger.debug(f"{TASK_NAME}: deleted batch of {len(names)} rows")

		if len(names) < batch_size:
			break

	return total_deleted, batches_executed
