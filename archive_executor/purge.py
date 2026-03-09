"""Post-archive source data purge and local copy cleanup.

Deletes archived source data in small batches to avoid locking the production
database. Tracks progress via purge_progress JSON for resumption after
interruptions.
"""

import json
import os
import shutil
import time
from datetime import datetime, timezone

from .config import Config
from .db import atomic_update, get_connection
from .logger import StructuredLogger

PURGE_BATCH_SIZE = 10_000
PURGE_SLEEP_SECONDS = 2


def _get_purgeable_jobs(config: Config) -> list[dict]:
	"""Query Completed jobs with post_archive_action='Delete' and source_deleted=0."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT name, source_doctype, archive_scope, meta, purge_progress "
				"FROM `tabMemora Archive Job` "
				"WHERE status = 'Completed' "
				"  AND post_archive_action = 'Delete' "
				"  AND source_deleted = 0 "
				"ORDER BY creation ASC"
			)
			return cursor.fetchall()
	finally:
		conn.close()


def _update_purge_progress(config: Config, job_name: str, progress: dict):
	"""Update the purge_progress JSON field on the job."""
	atomic_update(
		config,
		"UPDATE `tabMemora Archive Job` SET purge_progress = %s WHERE name = %s",
		(json.dumps(progress), job_name),
	)


def _mark_purged(config: Config, job_name: str):
	"""Transition job to Purged with source_deleted=1."""
	atomic_update(
		config,
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Purged', source_deleted = 1 "
		"WHERE name = %s AND status = 'Completed'",
		(job_name,),
	)


def purge_completed_jobs(config: Config, log: StructuredLogger):
	"""Purge source data for all eligible Completed archive jobs.

	For each eligible job:
	1. Read query_filter from meta for date range
	2. Read purge_progress for resume point (if any)
	3. DELETE in batches of 10,000 with 2-second pauses
	4. Update purge_progress after each batch
	5. Set status='Purged' and source_deleted=1 when done
	"""
	jobs = _get_purgeable_jobs(config)
	if not jobs:
		return

	log.info("purge_started", eligible_jobs=len(jobs))

	for job in jobs:
		job_name = job["name"]
		meta = json.loads(job["meta"]) if isinstance(job["meta"], str) else (job["meta"] or {})
		query_filter = meta.get("query_filter", {})

		date_from = query_filter.get("date_from")
		date_to = query_filter.get("date_to")
		filter_column = query_filter.get("filter_column", "last_seen_at")
		source_table = f"tab{job['source_doctype']}"

		if not date_from or not date_to:
			log.warning("purge_skipped", job=job_name, reason="missing_date_range")
			continue

		# Load resume point from purge_progress
		progress_raw = job.get("purge_progress")
		progress = {}
		if progress_raw:
			progress = json.loads(progress_raw) if isinstance(progress_raw, str) else progress_raw

		total_deleted = progress.get("total_deleted", 0)

		log.info("purge_job_started", job=job_name, source_table=source_table, resume_from=total_deleted)

		conn = get_connection(config)
		try:
			while True:
				with conn.cursor() as cursor:
					sql = (
						f"DELETE FROM `{source_table}` "
						f"WHERE `{filter_column}` >= %s AND `{filter_column}` < %s "
						f"ORDER BY `{filter_column}` "
						f"LIMIT {PURGE_BATCH_SIZE}"
					)
					cursor.execute(sql, (date_from, date_to))
				conn.commit()

				deleted = cursor.rowcount
				if deleted == 0:
					break

				total_deleted += deleted
				progress = {
					"total_deleted": total_deleted,
					"last_batch_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
				}
				_update_purge_progress(config, job_name, progress)

				log.info("purge_batch", job=job_name, batch_deleted=deleted, total_deleted=total_deleted)

				time.sleep(PURGE_SLEEP_SECONDS)
		finally:
			conn.close()

		# Mark purged
		_mark_purged(config, job_name)
		log.info("purge_job_completed", job=job_name, total_deleted=total_deleted)


def _get_transferable_jobs(config: Config) -> list[dict]:
	"""Query jobs with transfer_status='Transferred' and local copy still present."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT name, file_path "
				"FROM `tabMemora Archive Job` "
				"WHERE transfer_status = 'Transferred' "
				"  AND local_deleted_at IS NULL "
				"  AND file_path IS NOT NULL "
				"ORDER BY creation ASC"
			)
			return cursor.fetchall()
	finally:
		conn.close()


def cleanup_transferred_local_copies(config: Config, log: StructuredLogger):
	"""Delete local batch directories for jobs whose transfer has been verified.

	Only processes jobs with transfer_status='Transferred'.
	Records local_deleted_at timestamp after successful deletion.
	"""
	jobs = _get_transferable_jobs(config)
	if not jobs:
		return

	log.info("local_cleanup_started", eligible_jobs=len(jobs))

	for job in jobs:
		job_name = job["name"]
		file_path = job["file_path"]

		if not file_path or not os.path.isdir(file_path):
			# Directory already gone or path invalid — just record the timestamp
			atomic_update(
				config,
				"UPDATE `tabMemora Archive Job` SET local_deleted_at = NOW() WHERE name = %s",
				(job_name,),
			)
			log.info("local_cleanup_skipped", job=job_name, reason="directory_not_found")
			continue

		try:
			shutil.rmtree(file_path)
			atomic_update(
				config,
				"UPDATE `tabMemora Archive Job` SET local_deleted_at = NOW() WHERE name = %s",
				(job_name,),
			)
			log.info("local_cleanup_completed", job=job_name, path=file_path)
		except OSError as exc:
			log.error("local_cleanup_failed", job=job_name, path=file_path, error=str(exc))
