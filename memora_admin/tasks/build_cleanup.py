"""Daily cleanup task for Memora Build Queue.

Removes old terminal rows to prevent unbounded table growth:
- Completed rows older than 7 days
- Failed rows older than 14 days

Active rows (Pending, Processing) are never deleted.
Deletion happens in batches of 1000 with a commit after each batch.

Scheduled via hooks.py: "0 4 * * *" (daily at 04:00)
"""

from __future__ import annotations

import logging

import frappe
from frappe.utils import add_days, now_datetime

from memora_admin.tasks.task_utils import (
	TASK_DURATION,
	TASK_RUNS,
	log_task_run,
	notify_admins,
)

logger = logging.getLogger(__name__)

TASK_NAME = "build_cleanup"

COMPLETED_RETENTION_DAYS = 7
FAILED_RETENTION_DAYS = 14
DELETE_BATCH_SIZE = 1000


def cleanup_build_queue(triggered_by: str = "Scheduler"):
	"""Delete old terminal rows from Memora Build Queue.

	Removes Completed rows older than 30 days and Failed rows older than
	90 days in batches of 1000, committing after each batch.

	Args:
		triggered_by: Source of trigger - "Scheduler", "Manual", or "Catch-up"
	"""
	start_time = now_datetime()

	try:
		completed_deleted, failed_deleted = _do_build_cleanup()

		log_task_run(
			task_name=TASK_NAME,
			status="Success",
			processed=completed_deleted + failed_deleted,
			failed_details=[
				{"deleted_completed": completed_deleted, "deleted_failed": failed_deleted}
			],
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=TASK_NAME, status="success").inc()
		logger.info(
			f"{TASK_NAME}: deleted {completed_deleted} Completed and {failed_deleted} Failed rows"
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


def _do_build_cleanup() -> tuple[int, int]:
	"""Execute the build queue cleanup.

	Returns:
		Tuple of (completed_deleted, failed_deleted)
	"""
	cutoff_completed = add_days(now_datetime(), -COMPLETED_RETENTION_DAYS)
	cutoff_failed = add_days(now_datetime(), -FAILED_RETENTION_DAYS)

	completed_deleted = _delete_in_batches("Completed", cutoff_completed)
	failed_deleted = _delete_in_batches("Failed", cutoff_failed)

	return completed_deleted, failed_deleted


def _delete_in_batches(status: str, cutoff) -> int:
	"""Fetch and delete eligible rows in batches, committing after each batch.

	Args:
		status: Row status to target ("Completed" or "Failed")
		cutoff: Delete rows with modified date before this value

	Returns:
		Total number of rows deleted
	"""
	total_deleted = 0

	while True:
		rows = frappe.get_all(
			"Memora Build Queue",
			filters={
				"status": status,
				"modified": ["<", cutoff],
			},
			fields=["name"],
			limit=DELETE_BATCH_SIZE,
		)

		if not rows:
			break

		names = [r.name for r in rows]
		frappe.db.delete("Memora Build Queue", {"name": ["in", names]})
		frappe.db.commit()
		total_deleted += len(names)

		logger.debug(f"{TASK_NAME}: deleted batch of {len(names)} {status} rows")

		if len(names) < DELETE_BATCH_SIZE:
			break

	return total_deleted
