"""Daily cleanup task for Memora Archive Job.

Deletes old terminal archive-job rows in bounded batches, committing
after each batch so partial progress is preserved on failure.

Retention rules:
- Purged jobs: delete where modified < now() - purged_retention_days (default 30)
- Failed jobs: delete where modified < now() - failed_retention_days (default 90)

Jobs in non-terminal statuses (Pending, Processing, Exported, Transferred,
Ingested, Completed) are never deleted. Jobs with active child batch rows
in Memora Task Log Archive Batch are preserved.

Scheduled via hooks.py: "30 6 * * *" (daily at 06:30)
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

TASK_NAME = "archive_job_cleanup"
DEFAULT_PURGED_RETENTION_DAYS = 30
DEFAULT_FAILED_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 500


def cleanup_archive_jobs(
	triggered_by: str = "Scheduler",
	purged_retention_days: int = DEFAULT_PURGED_RETENTION_DAYS,
	failed_retention_days: int = DEFAULT_FAILED_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
):
	"""Delete old terminal rows from Memora Archive Job."""
	start_time = now_datetime()

	logger.info(
		f"{TASK_NAME}: starting (purged_retention_days={purged_retention_days}, "
		f"failed_retention_days={failed_retention_days}, batch_size={batch_size})"
	)

	try:
		total_deleted, batches_executed = _do_archive_job_cleanup(
			purged_retention_days=purged_retention_days,
			failed_retention_days=failed_retention_days,
			batch_size=batch_size,
		)
		duration = (now_datetime() - start_time).total_seconds()

		log_task_run(
			task_name=TASK_NAME,
			status="Success",
			processed=total_deleted,
			failed_details=[
				{
					"purged_retention_days": purged_retention_days,
					"failed_retention_days": failed_retention_days,
					"batch_size": batch_size,
					"batches_executed": batches_executed,
					"rows_deleted": total_deleted,
				}
			],
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=TASK_NAME, status="success").inc()

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


def _do_archive_job_cleanup(
	purged_retention_days: int = DEFAULT_PURGED_RETENTION_DAYS,
	failed_retention_days: int = DEFAULT_FAILED_RETENTION_DAYS,
	batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int]:
	"""Delete eligible archive-job rows in small committed batches.

	Returns (total_deleted, batches_executed).
	"""
	if purged_retention_days < 0:
		raise ValueError("purged_retention_days must be >= 0")
	if failed_retention_days < 0:
		raise ValueError("failed_retention_days must be >= 0")
	if batch_size <= 0:
		raise ValueError("batch_size must be > 0")

	total_deleted = 0
	batches_executed = 0

	# Pass 1: Purged jobs (30-day retention)
	cutoff_purged = add_days(now_datetime(), -purged_retention_days)
	purged_deleted, purged_batches = _cleanup_pass(
		status="Purged",
		cutoff=cutoff_purged,
		batch_size=batch_size,
	)
	total_deleted += purged_deleted
	batches_executed += purged_batches

	# Pass 2: Failed jobs (90-day retention)
	cutoff_failed = add_days(now_datetime(), -failed_retention_days)
	failed_deleted, failed_batches = _cleanup_pass(
		status="Failed",
		cutoff=cutoff_failed,
		batch_size=batch_size,
	)
	total_deleted += failed_deleted
	batches_executed += failed_batches

	return total_deleted, batches_executed


def _cleanup_pass(
	status: str,
	cutoff,
	batch_size: int,
) -> tuple[int, int]:
	"""Run one cleanup pass for a given status. Returns (deleted, batches)."""
	deleted = 0
	batches = 0

	while True:
		rows = frappe.db.sql(
			"""
			SELECT aj.name
			FROM `tabMemora Archive Job` aj
			WHERE aj.status = %s
			  AND aj.modified < %s
			  AND NOT EXISTS (
			    SELECT 1
			    FROM `tabMemora Task Log Archive Batch` tlab
			    WHERE tlab.archive_job_id = aj.name
			      AND tlab.status NOT IN ('Purged', 'Failed')
			  )
			ORDER BY aj.modified ASC, aj.name ASC
			LIMIT %s
			""",
			(status, cutoff, batch_size),
			as_dict=True,
		)

		if not rows:
			break

		names = [row.name for row in rows]

		try:
			frappe.db.delete("Memora Archive Job", {"name": ["in", names]})
			frappe.db.commit()
		except Exception as e:
			logger.error(
				f"{TASK_NAME}: batch {batches + 1} failed after {deleted}"
				f" rows deleted: {e}"
			)
			raise

		batches += 1
		deleted += len(names)
		logger.info(f"{TASK_NAME}: deleted batch {batches} of {len(names)} rows")

		if len(names) < batch_size:
			break

	return deleted, batches
