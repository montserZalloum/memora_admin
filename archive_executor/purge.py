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
from .db import atomic_update, get_connection, validate_identifier
from .logger import StructuredLogger

PURGE_BATCH_SIZE = 10_000
PURGE_SLEEP_SECONDS = 2


def _get_purgeable_jobs(config: Config) -> list[dict]:
	"""Query Completed jobs with post_archive_action='Delete' and source_deleted=0."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT name, source_doctype, archive_scope, meta, purge_progress, file_path "
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

		# Validate identifiers before SQL interpolation
		try:
			validate_identifier(source_table)
			validate_identifier(filter_column)
		except ValueError as exc:
			log.error("purge_skipped", job=job_name, reason=str(exc))
			continue

		# Verify archive files exist on disk before purging source data
		file_path = job.get("file_path") if isinstance(job, dict) else getattr(job, "file_path", None)
		if not file_path or not os.path.isdir(file_path):
			log.warning("purge_skipped", job=job_name, reason="archive_files_missing", path=file_path)
			continue

		# Load resume point from purge_progress
		progress_raw = job.get("purge_progress")
		progress = {}
		if progress_raw:
			progress = json.loads(progress_raw) if isinstance(progress_raw, str) else progress_raw

		total_deleted = progress.get("total_deleted", 0)

		log.info("purge_job_started", job=job_name, source_table=source_table, resume_from=total_deleted)

		while True:
			conn = get_connection(config)
			try:
				with conn.cursor() as cursor:
					sql = (
						f"DELETE FROM `{source_table}` "
						f"WHERE `{filter_column}` >= %s AND `{filter_column}` < %s "
						f"ORDER BY `{filter_column}` "
						f"LIMIT {PURGE_BATCH_SIZE}"
					)
					cursor.execute(sql, (date_from, date_to))
					deleted = cursor.rowcount
				conn.commit()
			finally:
				conn.close()

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

		# Mark purged
		_mark_purged(config, job_name)
		log.info("purge_job_completed", job=job_name, total_deleted=total_deleted)


def _get_cleanable_jobs(config: Config) -> list[dict]:
	"""Query Completed/Purged jobs with local file_path still present."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT name, file_path, status "
				"FROM `tabMemora Archive Job` "
				"WHERE status IN ('Completed', 'Purged') "
				"  AND file_path IS NOT NULL "
				"  AND remote_path IS NOT NULL "
				"ORDER BY creation ASC"
			)
			return cursor.fetchall()
	finally:
		conn.close()


def cleanup_local_copies(config: Config, log: StructuredLogger):
	"""Delete local batch directories for Completed/Purged jobs that have a remote_path.

	Only cleans up jobs whose data has been confirmed at the remote destination.
	"""
	jobs = _get_cleanable_jobs(config)
	if not jobs:
		return

	log.info("local_cleanup_started", eligible_jobs=len(jobs))

	for job in jobs:
		job_name = job["name"]
		file_path = job["file_path"]

		if not file_path or not os.path.isdir(file_path):
			# Directory already gone — clear the file_path
			atomic_update(
				config,
				"UPDATE `tabMemora Archive Job` SET file_path = NULL WHERE name = %s",
				(job_name,),
			)
			log.info("local_cleanup_skipped", job=job_name, reason="directory_not_found")
			continue

		try:
			shutil.rmtree(file_path)
			atomic_update(
				config,
				"UPDATE `tabMemora Archive Job` SET file_path = NULL WHERE name = %s",
				(job_name,),
			)
			log.info("local_cleanup_completed", job=job_name, path=file_path)
		except OSError as exc:
			log.error("local_cleanup_failed", job=job_name, path=file_path, error=str(exc))
