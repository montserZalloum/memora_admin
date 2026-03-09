"""Standalone archive executor entry point.

Picks up Pending archive jobs, exports fact + dimension Parquet files,
builds manifest, and marks jobs Completed. Designed to run via cron
in a separate virtualenv — no Frappe imports.

Usage:
	/opt/memora-archive/venv/bin/python run.py

Cron:
	0 2 * * * /opt/memora-archive/venv/bin/python /opt/memora-archive/run.py
"""

import fcntl
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone

from .config import Config
from .db import atomic_update, get_connection
from .exporter import export_dimension, export_fact_data
from .logger import StructuredLogger
from .manifest import build_manifest
from .purge import cleanup_transferred_local_copies, purge_completed_jobs
from .schemas import load_archive_type, load_dimension_schema
from .validator import validate_file


def _acquire_lock(lock_file: str):
	"""Acquire an exclusive file lock. Returns the fd or None if already held."""
	os.makedirs(os.path.dirname(lock_file) or ".", exist_ok=True)
	fd = open(lock_file, "w")
	try:
		fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		fd.write(str(os.getpid()))
		fd.flush()
		return fd
	except (BlockingIOError, OSError):
		fd.close()
		return None


def _release_lock(fd):
	"""Release the file lock."""
	if fd:
		try:
			fcntl.flock(fd, fcntl.LOCK_UN)
			fd.close()
		except OSError:
			pass


def _fail_stuck_jobs(config: Config, log: StructuredLogger) -> int:
	"""Detect and fail jobs stuck in Processing beyond the timeout."""
	sql = (
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Failed', error_log = 'Stuck: exceeded timeout', "
		"    completed_at = NOW() "
		"WHERE status = 'Processing' "
		"  AND claimed_at < DATE_SUB(NOW(), INTERVAL %s HOUR)"
	)
	count = atomic_update(config, sql, (config.stuck_timeout_hours,))
	if count:
		log.warning("stuck_jobs_failed", count=count, timeout_hours=config.stuck_timeout_hours)
	return count


def _get_pending_jobs(config: Config) -> list[dict]:
	"""Query all Pending jobs ordered by priority DESC, creation ASC."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT name, source_doctype, archive_scope, schema_version, "
				"       archive_type, meta, retry_count, post_archive_action "
				"FROM `tabMemora Archive Job` "
				"WHERE status = 'Pending' "
				"ORDER BY FIELD(priority, 'High', 'Normal', 'Low'), creation ASC"
			)
			return cursor.fetchall()
	finally:
		conn.close()


def _claim_job(config: Config, job_name: str) -> bool:
	"""Atomically claim a job. Returns True if successfully claimed."""
	sql = (
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Processing', claimed_at = NOW(), "
		"    started_at = NOW(), execution_stage = 'claiming' "
		"WHERE name = %s AND status = 'Pending'"
	)
	return atomic_update(config, sql, (job_name,)) == 1


def _update_stage(config: Config, job_name: str, stage: str):
	"""Update the execution_stage field for progress tracking."""
	atomic_update(
		config,
		"UPDATE `tabMemora Archive Job` SET execution_stage = %s WHERE name = %s",
		(stage, job_name),
	)


def _complete_job(
	config: Config,
	job_name: str,
	row_count: int,
	file_path: str,
	file_checksum: str,
	file_size_bytes: int,
	snapshot_taken_at: str,
	duration_seconds: float,
):
	"""Mark a job as Completed with output metadata."""
	sql = (
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Completed', completed_at = NOW(), execution_stage = 'done', "
		"    row_count = %s, file_path = %s, file_checksum = %s, "
		"    file_size_bytes = %s, snapshot_taken_at = %s, duration_seconds = %s "
		"WHERE name = %s"
	)
	atomic_update(
		config,
		sql,
		(row_count, file_path, file_checksum, file_size_bytes, snapshot_taken_at, duration_seconds, job_name),
	)


def _fail_job(config: Config, job_name: str, error_msg: str, retry_count: int):
	"""Handle job failure with automatic retry up to 3 attempts.

	If retry_count < 3: resets to Pending with retry_count + 1 (auto-retry).
	If retry_count >= 3: permanently fails with completed_at timestamp.
	"""
	error_msg = error_msg[:60000] if error_msg else ""

	if retry_count < 3:
		# Auto-retry: reset to Pending with incremented retry_count
		sql = (
			"UPDATE `tabMemora Archive Job` "
			"SET status = 'Pending', retry_count = retry_count + 1, "
			"    error_log = %s, execution_stage = NULL "
			"WHERE name = %s AND status = 'Processing'"
		)
		atomic_update(config, sql, (error_msg, job_name))
	else:
		# Permanent failure: exhausted all retries
		sql = (
			"UPDATE `tabMemora Archive Job` "
			"SET status = 'Failed', error_log = %s, completed_at = NOW() "
			"WHERE name = %s AND status = 'Processing'"
		)
		atomic_update(config, sql, (error_msg, job_name))


def _cleanup_staging(staging_dir: str):
	"""Remove the staging directory if it exists."""
	if staging_dir and os.path.isdir(staging_dir):
		shutil.rmtree(staging_dir, ignore_errors=True)


def _set_permissions(directory: str):
	"""Set directory to 0700 and all files within to 0600."""
	os.chmod(directory, 0o700)
	for entry in os.listdir(directory):
		os.chmod(os.path.join(directory, entry), 0o600)


def _process_job(config: Config, job: dict, log: StructuredLogger):
	"""Process a single archive job end-to-end."""
	job_name = job["name"]
	start_time = time.monotonic()
	staging_dir = os.path.join(config.archive_output_path, ".staging", job_name)
	final_dir = os.path.join(config.archive_output_path, job_name)

	try:
		# Parse meta JSON
		meta = json.loads(job["meta"]) if isinstance(job["meta"], str) else job["meta"]
		archive_type_key = job.get("archive_type") or "practice_log"
		source_table = f"tab{job['source_doctype']}"

		# Create staging directory
		os.makedirs(staging_dir, exist_ok=True)

		# --- Export fact data ---
		_update_stage(config, job_name, "exporting_fact")
		log.info("exporting_fact", job=job_name)

		fact_path, fact_row_count, referenced_ids = export_fact_data(
			config=config,
			staging_dir=staging_dir,
			meta=meta,
			source_table=source_table,
			archive_type_name=archive_type_key,
		)

		# Record snapshot timestamp
		snapshot_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

		# --- Export dimension snapshots ---
		_update_stage(config, job_name, "exporting_dimensions")
		log.info("exporting_dimensions", job=job_name)

		dimension_results = []
		for rt in meta.get("related_tables", []):
			entity = rt["entity"]
			schema_version = rt["schema_version"]

			dim_schema = load_dimension_schema(config.schema_registry_path, entity, schema_version)

			# Get referenced IDs for this dimension from the fact export
			fact_col = rt["fact_column"]
			ids_for_dim = referenced_ids.get(fact_col, set())

			dim_path, dim_row_count = export_dimension(
				config=config,
				staging_dir=staging_dir,
				dim_schema=dim_schema,
				referenced_ids=ids_for_dim,
			)
			dimension_results.append(
				{
					"entity": entity,
					"schema_version": schema_version,
					"fact_column": fact_col,
					"path": dim_path,
					"row_count": dim_row_count,
				}
			)

		# --- Validate all files ---
		_update_stage(config, job_name, "verifying")
		log.info("verifying", job=job_name)

		file_entries = []

		# Validate fact file
		fact_validation = validate_file(fact_path, fact_row_count)
		if not fact_validation["valid"]:
			raise RuntimeError(f"Fact file validation failed: {fact_validation['errors']}")

		file_entries.append(
			{
				"role": "fact",
				"filename": fact_validation["filename"],
				"row_count": fact_validation["row_count"],
				"checksum": fact_validation["checksum"],
				"size_bytes": fact_validation["size_bytes"],
			}
		)

		# Validate dimension files
		for dim_result in dimension_results:
			dim_validation = validate_file(dim_result["path"], dim_result["row_count"])
			if not dim_validation["valid"]:
				raise RuntimeError(
					f"Dimension {dim_result['entity']} validation failed: {dim_validation['errors']}"
				)

			file_entries.append(
				{
					"role": "dimension",
					"entity": dim_result["entity"],
					"snapshot_schema_version": dim_result["schema_version"],
					"scope": "batch_referenced",
					"referenced_by": dim_result["fact_column"],
					"filename": dim_validation["filename"],
					"row_count": dim_validation["row_count"],
					"checksum": dim_validation["checksum"],
					"size_bytes": dim_validation["size_bytes"],
				}
			)

		# --- Build manifest ---
		build_manifest(
			staging_dir=staging_dir,
			batch_id=job_name,
			source_doctype=job["source_doctype"],
			archive_scope=job["archive_scope"],
			schema_version=job["schema_version"],
			snapshot_taken_at=snapshot_ts,
			files=file_entries,
		)

		# --- Publish: atomic rename staging → final ---
		_update_stage(config, job_name, "publishing")
		log.info("publishing", job=job_name)

		try:
			os.rename(staging_dir, final_dir)
		except OSError:
			# Cross-filesystem: copy + verify + remove staging
			shutil.copytree(staging_dir, final_dir)
			shutil.rmtree(staging_dir)

		# Set permissions
		_set_permissions(final_dir)

		# --- Mark completed ---
		duration = time.monotonic() - start_time
		fact_checksum = fact_validation["checksum"]
		fact_size = fact_validation["size_bytes"]

		_complete_job(
			config=config,
			job_name=job_name,
			row_count=fact_row_count,
			file_path=final_dir,
			file_checksum=fact_checksum,
			file_size_bytes=fact_size,
			snapshot_taken_at=snapshot_ts,
			duration_seconds=round(duration, 2),
		)

		log.info(
			"job_completed",
			job=job_name,
			rows=fact_row_count,
			duration_s=round(duration, 2),
			dimensions=len(dimension_results),
		)

	except Exception as exc:
		_cleanup_staging(staging_dir)
		error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
		retry_count = job.get("retry_count", 0) or 0
		_fail_job(config, job_name, error_msg, retry_count)
		if retry_count < 3:
			log.warning("job_retryable_failure", job=job_name, retry_count=retry_count + 1, error=str(exc))
		else:
			log.error("job_permanently_failed", job=job_name, retry_count=retry_count, error=str(exc))
		raise


def main():
	"""Main entry point for the archive executor."""
	config = Config.from_env()
	log = StructuredLogger(config.log_path)

	log.info("run_started", pid=os.getpid())

	# Step 1: Acquire file lock
	lock_fd = _acquire_lock(config.lock_file)
	if lock_fd is None:
		log.info("run_skipped", reason="lock_held")
		sys.exit(0)

	jobs_processed = 0
	jobs_failed = 0

	try:
		# Step 2: Fail stuck jobs
		_fail_stuck_jobs(config, log)

		# Step 3: Get pending jobs
		pending_jobs = _get_pending_jobs(config)
		if not pending_jobs:
			log.info("run_finished", jobs_processed=0, jobs_failed=0, reason="no_pending_jobs")
			return

		log.info("pending_jobs_found", count=len(pending_jobs))

		# Step 4: Process each job
		for job in pending_jobs:
			job_name = job["name"]

			# Atomic claim
			if not _claim_job(config, job_name):
				log.info("job_claim_skipped", job=job_name, reason="already_claimed")
				continue

			log.info(
				"job_claimed",
				job=job_name,
				source=job["source_doctype"],
				scope=job["archive_scope"],
			)

			try:
				_process_job(config, job, log)
				jobs_processed += 1
			except Exception:
				jobs_failed += 1

		# Step 5: Purge source data for eligible completed jobs
		try:
			purge_completed_jobs(config, log)
		except Exception as exc:
			log.error("purge_error", error=str(exc))

		# Step 6: Clean up local copies for transferred jobs
		try:
			cleanup_transferred_local_copies(config, log)
		except Exception as exc:
			log.error("local_cleanup_error", error=str(exc))

	finally:
		_release_lock(lock_fd)

	log.info("run_finished", jobs_processed=jobs_processed, jobs_failed=jobs_failed)


if __name__ == "__main__":
	main()
