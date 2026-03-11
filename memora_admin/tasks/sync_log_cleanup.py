"""Daily cleanup task for Memora Sync Log.

Deletes rows older than DEFAULT_RETENTION_DAYS (7 days) in batches of
DEFAULT_BATCH_SIZE, committing after each batch for safe partial progress.

Retention rule: delete rows where creation < now() - retention_days.
Rows at the exact boundary are kept (strictly older only).

Scheduled via hooks.py: "0 5 * * *" (daily at 05:00)
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

TASK_NAME = "sync_log_cleanup"
DEFAULT_RETENTION_DAYS = 7
DEFAULT_BATCH_SIZE = 1000


def cleanup_sync_logs(
	triggered_by: str = "Scheduler",
	retention_days: int = DEFAULT_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
):
	"""Delete old rows from Memora Sync Log.

	Rows with creation strictly older than retention_days are deleted in
	batches of batch_size. Commits after each batch. Safe to rerun after
	partial completion.

	Args:
		triggered_by: Source of trigger - "Scheduler", "Manual", or "Catch-up"
		retention_days: Delete rows strictly older than this many days (default 7)
		batch_size: Number of rows to delete per batch (default 1000)
	"""
	start_time = now_datetime()

	logger.info(
		f"{TASK_NAME}: starting (retention_days={retention_days}, batch_size={batch_size})"
	)

	try:
		total_deleted, batches_executed = _do_sync_log_cleanup(retention_days, batch_size)

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


def _do_sync_log_cleanup(
	retention_days: int = DEFAULT_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int]:
	"""Delete Memora Sync Log rows older than retention_days.

	Selects rows with creation < cutoff, ordered by creation ASC then name ASC
	(oldest first), deletes them in batches, and commits after each batch.

	Args:
		retention_days: Rows with creation strictly older than this are deleted
		batch_size: Max rows to select and delete per batch

	Returns:
		Tuple of (total_deleted, batches_executed)
	"""
	cutoff = add_days(now_datetime(), -retention_days)
	total_deleted = 0
	batches_executed = 0

	while True:
		rows = frappe.get_all(
			"Memora Sync Log",
			filters={"creation": ["<", cutoff]},
			fields=["name"],
			order_by="creation asc, name asc",
			limit=batch_size,
		)

		if not rows:
			break

		names = [r.name for r in rows]
		frappe.db.delete("Memora Sync Log", {"name": ["in", names]})
		frappe.db.commit()
		total_deleted += len(names)
		batches_executed += 1

		logger.debug(f"{TASK_NAME}: deleted batch of {len(names)} rows")

		if len(names) < batch_size:
			break

	return total_deleted, batches_executed
