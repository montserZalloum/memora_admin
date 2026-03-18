"""Live data sync executor — full snapshot export for analytics.

Processes Memora Live Sync Jobs through the delivery pipeline:
Pending -> Processing -> Exported -> Transferred -> Ingested -> Completed

Key differences from archive executor:
- No purge step (live sync doesn't delete source data)
- Uses full_snapshot mode (no WHERE clause)
- Uses ingest_live_snapshot() (staging -> swap) instead of ingest_archive_batch()
- Respects sync_paused — skips if any archive job has sync_paused=1 for same source
- Excludes date ranges from completed archive jobs to avoid duplication

Usage:
	/opt/memora-archive/venv/bin/python -m archive_executor.live_sync

Cron:
	5 3 * * * /opt/memora-archive/venv/bin/python -m archive_executor.live_sync
"""

import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone

import pyarrow.parquet as pq

from .config import Config
from .db import atomic_update, get_connection
from .exporter import export_dimension, export_fact_data
from .ingestion import IngestionError, ingest_live_snapshot, verify_ingestion
from .locking import acquire_lock, release_lock
from .logger import StructuredLogger
from .manifest import build_manifest
from .schemas import load_dimension_schema, load_sync_type
from .transfer import TransferError, transfer_batch, verify_remote_checksums
from .validator import validate_file

_JOB_NAME_RE = re.compile(r"^LSYNC-\d+$")
_TABLE_NAME = "tabMemora Live Sync Job"


# ---------------------------------------------------------------------------
# Check sync_paused on archive jobs
# ---------------------------------------------------------------------------


def _is_source_paused(config: Config, source_table: str) -> bool:
	"""Check if any archive job has sync_paused=1 for the same source table."""
	# Derive doctype name from table name (e.g., "tabMemora Practice Log" -> "Memora Practice Log")
	source_doctype = source_table[3:] if source_table.startswith("tab") else source_table

	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT COUNT(*) as cnt FROM `tabMemora Archive Job` "
				"WHERE sync_paused = 1 AND source_doctype = %s",
				(source_doctype,),
			)
			row = cursor.fetchone()
			return (row.get("cnt", 0) or 0) > 0
	finally:
		conn.close()


def _get_completed_archive_ranges(config: Config, source_table: str) -> list[tuple[str, str]]:
	"""Get date ranges from completed archive jobs for scope exclusion.

	Returns list of (date_from, date_to) tuples parsed from archive job meta.
	"""
	source_doctype = source_table[3:] if source_table.startswith("tab") else source_table

	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT job_meta FROM `tabMemora Archive Job` "
				"WHERE status = 'Completed' AND source_doctype = %s",
				(source_doctype,),
			)
			rows = cursor.fetchall()
	finally:
		conn.close()

	ranges = []
	for row in rows:
		meta_raw = row.get("job_meta")
		if not meta_raw:
			continue
		meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
		qf = meta.get("query_filter", {})
		date_from = qf.get("date_from")
		date_to = qf.get("date_to")
		if date_from and date_to:
			ranges.append((date_from, date_to))

	return ranges


# ---------------------------------------------------------------------------
# Derived dimension helpers
# ---------------------------------------------------------------------------


def _extract_ids_from_parquet(parquet_path: str, column_name: str) -> set:
	"""Read a Parquet file and extract unique non-null values from a column."""
	table = pq.read_table(parquet_path, columns=[column_name])
	col = table.column(column_name)
	return {val.as_py() for val in col if val.as_py() is not None}


def _export_derived_dimensions(
	config: Config,
	staging_dir: str,
	related_tables: list[dict],
	player_dim_path: str,
) -> list[dict]:
	"""Export derived dimensions (season, plan) based on player dimension data."""
	results = []

	for rt in related_tables:
		if rt.get("scope_source") != "derived":
			continue

		entity = rt["entity"]
		schema_version = rt["schema_version"]
		dim_schema = load_dimension_schema(config.schema_registry_path, entity, schema_version)

		if entity == "season":
			ids = _extract_ids_from_parquet(player_dim_path, "season_id")
		elif entity == "plan":
			ids = _extract_ids_from_parquet(player_dim_path, "plan_id")
		else:
			continue

		dim_path, dim_row_count = export_dimension(
			config=config,
			staging_dir=staging_dir,
			dim_schema=dim_schema,
			referenced_ids=ids,
		)

		results.append({
			"entity": entity,
			"schema_version": schema_version,
			"scope_source": "derived",
			"path": dim_path,
			"row_count": dim_row_count,
		})

	return results


# ---------------------------------------------------------------------------
# Job queries
# ---------------------------------------------------------------------------


def _get_jobs_by_status(config: Config, status: str) -> list[dict]:
	"""Query live sync jobs by status, ordered by creation ASC."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				f"SELECT name, sync_type, schema_version, job_meta, retry_count, "
				f"       file_path, file_checksum, remote_path "
				f"FROM `{_TABLE_NAME}` "
				f"WHERE status = %s "
				f"ORDER BY creation ASC",
				(status,),
			)
			return cursor.fetchall()
	finally:
		conn.close()


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _update_stage(config: Config, job_name: str, stage: str):
	"""Update the execution_stage field."""
	atomic_update(
		config,
		f"UPDATE `{_TABLE_NAME}` SET execution_stage = %s WHERE name = %s",
		(stage, job_name),
	)


def _claim_job(config: Config, job_name: str) -> bool:
	"""Atomically claim a Pending live sync job."""
	sql = (
		f"UPDATE `{_TABLE_NAME}` "
		f"SET status = 'Processing', started_at = NOW(), execution_stage = 'claiming' "
		f"WHERE name = %s AND status = 'Pending'"
	)
	return atomic_update(config, sql, (job_name,)) == 1


def _mark_exported(config: Config, job_name: str, row_count: int, file_path: str,
                   file_checksum: str, file_size_bytes: int, duration_seconds: float):
	"""Mark a live sync job as Exported."""
	sql = (
		f"UPDATE `{_TABLE_NAME}` "
		f"SET status = 'Exported', exported_at = NOW(), execution_stage = 'exported', "
		f"    row_count = %s, file_path = %s, file_checksum = %s, "
		f"    file_size_bytes = %s, duration_seconds = %s "
		f"WHERE name = %s"
	)
	atomic_update(config, sql, (row_count, file_path, file_checksum, file_size_bytes, duration_seconds, job_name))


def _mark_transferred(config: Config, job_name: str, remote_path: str):
	"""Mark a live sync job as Transferred."""
	sql = (
		f"UPDATE `{_TABLE_NAME}` "
		f"SET status = 'Transferred', transferred_at = NOW(), "
		f"    remote_path = %s, execution_stage = 'transferred' "
		f"WHERE name = %s"
	)
	atomic_update(config, sql, (remote_path, job_name))


def _mark_ingested(config: Config, job_name: str):
	"""Mark a live sync job as Ingested."""
	sql = (
		f"UPDATE `{_TABLE_NAME}` "
		f"SET status = 'Ingested', ingested_at = NOW(), execution_stage = 'ingested' "
		f"WHERE name = %s"
	)
	atomic_update(config, sql, (job_name,))


def _mark_completed(config: Config, job_name: str):
	"""Mark a live sync job as Completed."""
	sql = (
		f"UPDATE `{_TABLE_NAME}` "
		f"SET status = 'Completed', completed_at = NOW(), execution_stage = 'done' "
		f"WHERE name = %s"
	)
	atomic_update(config, sql, (job_name,))


def _mark_completed_empty(
	config: Config,
	job_name: str,
	duration_seconds: float,
):
	"""Mark a live sync job as Completed when no eligible rows remain to sync."""
	sql = (
		f"UPDATE `{_TABLE_NAME}` "
		f"SET status = 'Completed', completed_at = NOW(), execution_stage = 'done', "
		f"    row_count = 0, duration_seconds = %s, error_log = NULL, "
		f"    file_path = NULL, file_checksum = NULL, file_size_bytes = 0, remote_path = NULL "
		f"WHERE name = %s"
	)
	atomic_update(config, sql, (duration_seconds, job_name))


def _fail_job(config: Config, job_name: str, error_msg: str, retry_count: int, current_status: str = "Processing"):
	"""Handle job failure with automatic retry up to 3 attempts."""
	error_msg = error_msg[:60000] if error_msg else ""

	if retry_count < 3:
		# Auto-retry: reset to Pending so _process_pending_live_jobs() picks it up
		sql = (
			f"UPDATE `{_TABLE_NAME}` "
			f"SET status = 'Pending', retry_count = retry_count + 1, "
			f"    error_log = %s, execution_stage = NULL "
			f"WHERE name = %s AND status = %s"
		)
		atomic_update(config, sql, (error_msg, job_name, current_status))
	else:
		sql = (
			f"UPDATE `{_TABLE_NAME}` "
			f"SET status = 'Failed', error_log = %s, completed_at = NOW() "
			f"WHERE name = %s AND status = %s"
		)
		atomic_update(config, sql, (error_msg, job_name, current_status))


def _fail_stuck_live_jobs(config: Config, log: StructuredLogger) -> int:
	"""Detect and fail live sync jobs stuck in active states."""
	total_failed = 0
	for status, timeout_hours in [("Processing", 2), ("Exported", 24), ("Transferred", 24), ("Ingested", 24)]:
		sql = (
			f"UPDATE `{_TABLE_NAME}` "
			f"SET status = 'Failed', error_log = CONCAT(COALESCE(error_log, ''), %s), "
			f"    completed_at = NOW() "
			f"WHERE status = %s "
			f"  AND started_at < DATE_SUB(NOW(), INTERVAL %s HOUR)"
		)
		count = atomic_update(
			config, sql,
			(f"\nStuck: exceeded {timeout_hours}h timeout in {status} state", status, timeout_hours),
		)
		if count:
			log.warning("stuck_live_jobs_failed", count=count, status=status, timeout_hours=timeout_hours)
			total_failed += count
	return total_failed


# ---------------------------------------------------------------------------
# Pipeline phases
# ---------------------------------------------------------------------------


def _process_pending_live_jobs(config: Config, log: StructuredLogger) -> int:
	"""Process Pending live sync jobs: full snapshot export."""
	jobs = _get_jobs_by_status(config, "Pending")
	if not jobs:
		return 0

	log.info("pending_live_jobs_found", count=len(jobs))
	processed = 0

	for job in jobs:
		job_name = job["name"]

		if not _JOB_NAME_RE.match(job_name):
			log.error("invalid_job_name", job=job_name)
			continue

		# Parse meta
		meta = json.loads(job["job_meta"]) if isinstance(job["job_meta"], str) else (job["job_meta"] or {})
		source_table = meta.get("source_table", "")

		# Check if source is paused by archive
		if source_table and _is_source_paused(config, source_table):
			log.info("live_sync_skipped", job=job_name, reason="source_paused_by_archive")
			continue

		if not _claim_job(config, job_name):
			log.info("live_job_claim_skipped", job=job_name, reason="already_claimed")
			continue

		start_time = time.monotonic()
		staging_dir = os.path.join(config.live_output_path, ".staging", job_name)
		final_dir = os.path.join(config.live_output_path, job_name)

		# Path safety
		real_output = os.path.realpath(config.live_output_path)
		if not os.path.realpath(staging_dir).startswith(real_output):
			log.error("path_traversal", job=job_name, path="staging_dir")
			continue
		if not os.path.realpath(final_dir).startswith(real_output):
			log.error("path_traversal", job=job_name, path="final_dir")
			continue

		try:
			os.makedirs(staging_dir, exist_ok=True)

			sync_type_name = job.get("sync_type") or meta.get("sync_type", "practice_log_live")

			# Load sync type schema
			sync_schema = load_sync_type(config.schema_registry_path, sync_type_name, job["schema_version"])

			# Build related_tables list from sync schema dimensions
			related_tables = [
				{
					"entity": d["entity"],
					"schema_version": d["schema_version"],
					"fact_column": d.get("join_column"),
					"scope_source": d.get("scope_source"),
				}
				for d in sync_schema.get("dimensions", [])
			]

			# Build export meta from sync schema
			export_meta = {
				"export_columns": sync_schema.get("fact_columns", []),
				"schema_snapshot": sync_schema.get("schema_snapshot", {}),
				"related_tables": related_tables,
				"scope_column": sync_schema.get("scope_column"),
				"fact_sql": sync_schema.get("fact_sql", {}),
			}

			export_source_table = sync_schema.get("source_table", source_table)

			# Get exclusion ranges from completed archive jobs
			exclusion_ranges = _get_completed_archive_ranges(config, export_source_table)
			if exclusion_ranges:
				log.info("applying_scope_exclusion", job=job_name, exclusion_count=len(exclusion_ranges))

			# Record snapshot timestamp
			snapshot_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

			# Build sync_metadata for injection into fact rows
			sync_metadata = {
				"scope_type": "live",
				"sync_batch_id": job_name,
				"schema_version": job["schema_version"] or "",
				"synced_at": snapshot_ts,
			}

			# --- Export fact data (full snapshot with exclusions) ---
			_update_stage(config, job_name, "exporting_fact")
			log.info("exporting_live_fact", job=job_name)

			fact_path, fact_row_count, referenced_ids = export_fact_data(
				config=config,
				staging_dir=staging_dir,
				meta=export_meta,
				source_table=export_source_table,
				archive_type_name=sync_type_name,
				mode="full_snapshot",
				export_metadata=sync_metadata,
				exclusion_ranges=exclusion_ranges if exclusion_ranges else None,
			)

			if fact_row_count == 0:
				# No rows to sync — skip transfer/ingest and complete immediately.
				# The analytics server manifest validation rejects row_count=0,
				# so there is nothing useful to transfer.
				shutil.rmtree(staging_dir, ignore_errors=True)
				duration = time.monotonic() - start_time
				_mark_completed_empty(config, job_name, round(duration, 2))
				processed += 1
				log.info(
					"live_job_completed_empty",
					job=job_name,
					reason="fact_has_0_rows_after_exclusions",
					duration_s=round(duration, 2),
				)
				continue

			# --- Export dimensions ---
			_update_stage(config, job_name, "exporting_dimensions")
			dimension_results = []
			player_dim_path = None

			# Pass 1: Direct dimensions (with join_column, no scope_source)
			for rt in related_tables:
				if rt.get("scope_source") == "derived":
					continue

				entity = rt["entity"]
				schema_version = rt["schema_version"]
				dim_schema = load_dimension_schema(config.schema_registry_path, entity, schema_version)
				fact_col = rt["fact_column"]
				ids_for_dim = referenced_ids.get(fact_col, set())
				dim_path, dim_row_count = export_dimension(
					config=config,
					staging_dir=staging_dir,
					dim_schema=dim_schema,
					referenced_ids=ids_for_dim,
				)

				if entity == "player":
					player_dim_path = dim_path

				dimension_results.append({
					"entity": entity,
					"schema_version": schema_version,
					"fact_column": fact_col,
					"path": dim_path,
					"row_count": dim_row_count,
				})

			# Pass 2: Derived dimensions (season, plan) — scoped from player dimension
			if player_dim_path:
				derived_results = _export_derived_dimensions(
					config=config,
					staging_dir=staging_dir,
					related_tables=related_tables,
					player_dim_path=player_dim_path,
				)
				dimension_results.extend(derived_results)

			# --- Validate ---
			_update_stage(config, job_name, "verifying")
			file_entries = []

			fact_validation = validate_file(fact_path, fact_row_count)
			if not fact_validation["valid"]:
				raise RuntimeError(f"Fact file validation failed: {fact_validation['errors']}")

			file_entries.append({
				"role": "fact",
				"entity": job.get("sync_type", "practice_log"),
				"filename": fact_validation["filename"],
				"row_count": fact_validation["row_count"],
				"checksum": fact_validation["checksum"],
				"size_bytes": fact_validation["size_bytes"],
			})

			for dim_result in dimension_results:
				dim_validation = validate_file(dim_result["path"], dim_result["row_count"])
				if not dim_validation["valid"]:
					raise RuntimeError(f"Dimension {dim_result['entity']} validation failed: {dim_validation['errors']}")

				entry = {
					"role": "dimension",
					"entity": dim_result["entity"],
					"snapshot_schema_version": dim_result["schema_version"],
					"filename": dim_validation["filename"],
					"row_count": dim_validation["row_count"],
					"checksum": dim_validation["checksum"],
					"size_bytes": dim_validation["size_bytes"],
				}
				file_entries.append(entry)

			# --- Build manifest ---
			build_manifest(
				staging_dir=staging_dir,
				batch_id=job_name,
				dataset_key=f"{job.get('sync_type', 'practice_log')}_live",
				kind="live",
				schema_version="1.0",
				source="memora_admin",
				files=file_entries,
			)

			# --- Publish ---
			_update_stage(config, job_name, "publishing")
			if os.path.isdir(final_dir):
				# Rename old dir to .old first for crash safety — if we crash between
				# rmtree and rename, both dirs would be lost without this pattern
				old_dir = final_dir + ".old"
				if os.path.isdir(old_dir):
					shutil.rmtree(old_dir)
				os.rename(final_dir, old_dir)
			else:
				old_dir = None
			try:
				os.rename(staging_dir, final_dir)
			except OSError:
				shutil.copytree(staging_dir, final_dir)
				shutil.rmtree(staging_dir)
			# Clean up .old after successful swap
			if old_dir and os.path.isdir(old_dir):
				shutil.rmtree(old_dir, ignore_errors=True)

			os.chmod(final_dir, 0o700)
			for entry in os.listdir(final_dir):
				os.chmod(os.path.join(final_dir, entry), 0o600)

			duration = time.monotonic() - start_time
			_mark_exported(
				config, job_name, fact_row_count, final_dir,
				fact_validation["checksum"], fact_validation["size_bytes"],
				round(duration, 2),
			)
			processed += 1
			log.info("live_job_exported", job=job_name, rows=fact_row_count, duration_s=round(duration, 2))

		except Exception as exc:
			if os.path.isdir(staging_dir):
				shutil.rmtree(staging_dir, ignore_errors=True)
			error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
			retry_count = job.get("retry_count", 0) or 0
			try:
				_fail_job(config, job_name, error_msg, retry_count, "Processing")
			except Exception:
				log.error("fail_live_job_error", job=job_name, error=str(exc))
			if retry_count >= 3:
				log.error("live_job_permanently_failed", job=job_name, error=str(exc))

	return processed


def _process_exported_live_jobs(config: Config, log: StructuredLogger) -> int:
	"""Transfer Exported live sync jobs to analytics server."""
	if not config.has_ssh_config():
		log.debug("live_transfer_skipped", reason="ssh_not_configured")
		return 0

	jobs = _get_jobs_by_status(config, "Exported")
	if not jobs:
		return 0

	processed = 0
	for job in jobs:
		job_name = job["name"]
		local_dir = job.get("file_path")
		if not local_dir or not os.path.isdir(local_dir):
			continue

		try:
			_update_stage(config, job_name, "transferring")
			remote_path = transfer_batch(config, local_dir, config.remote_live_path, job_name, log)

			manifest_path = os.path.join(local_dir, "manifest.json")
			with open(manifest_path) as f:
				manifest = json.load(f)

			_update_stage(config, job_name, "verifying_transfer")
			result = verify_remote_checksums(config, remote_path, manifest, log)
			if not result["valid"]:
				raise TransferError(f"Checksum verification failed: {result['errors']}")

			_mark_transferred(config, job_name, remote_path)
			processed += 1
			log.info("live_job_transferred", job=job_name)

		except Exception as exc:
			error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
			retry_count = job.get("retry_count", 0) or 0
			try:
				_fail_job(config, job_name, error_msg, retry_count, "Exported")
			except Exception:
				log.error("fail_live_job_error", job=job_name, error=str(exc))

	return processed


def _process_transferred_live_jobs(config: Config, log: StructuredLogger) -> int:
	"""Ingest Transferred live sync jobs (staging -> swap)."""
	if not config.has_ssh_config():
		log.debug("live_ingestion_skipped", reason="ssh_not_configured")
		return 0

	jobs = _get_jobs_by_status(config, "Transferred")
	if not jobs:
		return 0

	processed = 0
	for job in jobs:
		job_name = job["name"]
		remote_path = job.get("remote_path")
		local_dir = job.get("file_path")
		if not remote_path:
			continue

		try:
			_update_stage(config, job_name, "ingesting")

			manifest = {}
			manifest_path = os.path.join(local_dir, "manifest.json") if local_dir else None
			if manifest_path and os.path.isfile(manifest_path):
				with open(manifest_path) as f:
					manifest = json.load(f)

			ingest_live_snapshot(config, remote_path, manifest, log)

			_update_stage(config, job_name, "verifying_ingestion")
			verify_result = verify_ingestion(config, manifest, remote_path, log)
			if not verify_result.get("valid", False):
				raise IngestionError(f"Live ingestion verification failed: {verify_result.get('errors', [])}")

			_mark_ingested(config, job_name)
			processed += 1
			log.info("live_job_ingested", job=job_name)

		except Exception as exc:
			error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
			retry_count = job.get("retry_count", 0) or 0
			try:
				_fail_job(config, job_name, error_msg, retry_count, "Transferred")
			except Exception:
				log.error("fail_live_job_error", job=job_name, error=str(exc))

	return processed


def _process_ingested_live_jobs(config: Config, log: StructuredLogger) -> int:
	"""Mark Ingested live sync jobs as Completed (no handoff needed for live)."""
	jobs = _get_jobs_by_status(config, "Ingested")
	if not jobs:
		return 0

	processed = 0
	for job in jobs:
		job_name = job["name"]
		try:
			_mark_completed(config, job_name)
			processed += 1
			log.info("live_job_completed", job=job_name)
		except Exception as exc:
			error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
			retry_count = job.get("retry_count", 0) or 0
			try:
				_fail_job(config, job_name, error_msg, retry_count, "Ingested")
			except Exception:
				log.error("fail_live_job_error", job=job_name, error=str(exc))

	return processed


def _cleanup_local_live_copies(config: Config, log: StructuredLogger):
	"""Delete local batch directories for Completed live sync jobs with a remote_path.

	Mirrors cleanup_local_copies() from the archive pipeline but targets
	the live sync job table instead.
	"""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				f"SELECT name, file_path "
				f"FROM `{_TABLE_NAME}` "
				f"WHERE status = 'Completed' "
				f"  AND file_path IS NOT NULL "
				f"  AND remote_path IS NOT NULL "
				f"ORDER BY creation ASC"
			)
			jobs = cursor.fetchall()
	finally:
		conn.close()

	if not jobs:
		return

	log.info("live_local_cleanup_started", eligible_jobs=len(jobs))

	for job in jobs:
		job_name = job["name"]
		file_path = job["file_path"]

		if not file_path or not os.path.isdir(file_path):
			atomic_update(
				config,
				f"UPDATE `{_TABLE_NAME}` SET file_path = NULL WHERE name = %s",
				(job_name,),
			)
			log.info("live_local_cleanup_skipped", job=job_name, reason="directory_not_found")
			continue

		try:
			shutil.rmtree(file_path)
			atomic_update(
				config,
				f"UPDATE `{_TABLE_NAME}` SET file_path = NULL WHERE name = %s",
				(job_name,),
			)
			log.info("live_local_cleanup_completed", job=job_name, path=file_path)
		except OSError as exc:
			log.error("live_local_cleanup_failed", job=job_name, path=file_path, error=str(exc))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
	"""Main entry point for the live sync executor."""
	config = Config.from_env()
	log = StructuredLogger(config.log_path)

	log.info("live_sync_started", pid=os.getpid())

	lock_fd = acquire_lock(config.live_lock_file)
	if lock_fd is None:
		log.info("live_sync_skipped", reason="lock_held")
		sys.exit(0)

	# Initialize counters before try so they're defined even if an early stage throws
	exported = 0
	transferred = 0
	ingested = 0
	completed = 0

	try:
		_fail_stuck_live_jobs(config, log)
		exported = _process_pending_live_jobs(config, log)
		transferred = _process_exported_live_jobs(config, log)
		ingested = _process_transferred_live_jobs(config, log)
		completed = _process_ingested_live_jobs(config, log)

		try:
			_cleanup_local_live_copies(config, log)
		except Exception as exc:
			log.error("live_local_cleanup_error", error=str(exc))
	finally:
		release_lock(lock_fd)

	log.info(
		"live_sync_finished",
		exported=exported,
		transferred=transferred,
		ingested=ingested,
		completed=completed,
	)


if __name__ == "__main__":
	main()
