"""Standalone archive executor entry point.

Picks up Pending archive jobs, exports fact + dimension Parquet files,
builds manifest, and progresses jobs through the delivery pipeline:
Pending -> Processing -> Exported -> Transferred -> Ingested -> Completed -> Purged

Designed to run via cron in a separate virtualenv — no Frappe imports.

Usage:
	/opt/memora-archive/venv/bin/python -m archive_executor.run

Cron:
	0 2 * * * /opt/memora-archive/venv/bin/python -m archive_executor.run
"""

import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from .config import Config
from .db import atomic_update, get_connection
from .exporter import export_dimension, export_fact_data
from .ingestion import (
	IngestionError,
	handoff_archive,
	handoff_season,
	ingest_archive_batch,
	refresh_aggregates,
	refresh_recent,
	verify_ingestion,
)
from .locking import acquire_lock, release_lock
from .logger import StructuredLogger
from .manifest import build_manifest
from .purge import cleanup_local_copies, purge_completed_jobs
from .schemas import load_archive_type, load_dimension_schema
from .transfer import TransferError, transfer_batch, verify_remote_checksums
from .validator import validate_fact_quality, validate_fact_quality_generic, validate_file


# ---------------------------------------------------------------------------
# Stuck job detection
# ---------------------------------------------------------------------------

# Per-state timeout hours for stuck job detection
_STUCK_TIMEOUTS = {
	"Processing": 1,
	"Exported": 24,
	"Transferred": 24,
	"Ingested": 24,
}


def _fail_stuck_jobs(config: Config, log: StructuredLogger) -> int:
	"""Detect and fail jobs stuck in active states beyond their timeouts."""
	total_failed = 0
	for status, default_hours in _STUCK_TIMEOUTS.items():
		timeout_hours = config.stuck_timeout_hours if status == "Processing" else default_hours
		sql = (
			"UPDATE `tabMemora Archive Job` "
			"SET status = 'Failed', error_log = CONCAT(COALESCE(error_log, ''), %s), "
			"    completed_at = NOW(), sync_paused = 0, sync_paused_at = NULL "
			"WHERE status = %s "
			"  AND claimed_at < DATE_SUB(NOW(), INTERVAL %s HOUR)"
		)
		count = atomic_update(
			config, sql,
			(f"\nStuck: exceeded {timeout_hours}h timeout in {status} state", status, timeout_hours),
		)
		if count:
			log.warning("stuck_jobs_failed", count=count, status=status, timeout_hours=timeout_hours)
			total_failed += count
	return total_failed


# ---------------------------------------------------------------------------
# Job queries
# ---------------------------------------------------------------------------


def _get_jobs_by_status(config: Config, status: str) -> list[dict]:
	"""Query jobs by status, ordered by priority DESC, creation ASC."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT name, source_doctype, archive_scope, schema_version, "
				"       archive_type, job_meta, retry_count, post_archive_action, "
				"       file_path, file_checksum, remote_path "
				"FROM `tabMemora Archive Job` "
				"WHERE status = %s "
				"ORDER BY FIELD(priority, 'High', 'Normal', 'Low'), creation ASC",
				(status,),
			)
			return cursor.fetchall()
	finally:
		conn.close()


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

_JOB_NAME_RE = re.compile(r"^ARCH-\d+$")


def _update_stage(config: Config, job_name: str, stage: str):
	"""Update the execution_stage field for progress tracking."""
	atomic_update(
		config,
		"UPDATE `tabMemora Archive Job` SET execution_stage = %s WHERE name = %s",
		(stage, job_name),
	)


def _read_stage(config: Config, job_name: str) -> str | None:
	"""Read the current execution_stage from the DB (used in failure handlers)."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT execution_stage FROM `tabMemora Archive Job` WHERE name = %s",
				(job_name,),
			)
			row = cursor.fetchone()
			return row["execution_stage"] if row else None
	finally:
		conn.close()


def _claim_job(config: Config, job_name: str) -> bool:
	"""Atomically claim a Pending job. Returns True if successfully claimed."""
	sql = (
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Processing', claimed_at = NOW(), "
		"    started_at = NOW(), execution_stage = 'claiming', "
		"    sync_paused = 1, sync_paused_at = NOW() "
		"WHERE name = %s AND status = 'Pending'"
	)
	return atomic_update(config, sql, (job_name,)) == 1


def _mark_exported(
	config: Config,
	job_name: str,
	row_count: int,
	file_path: str,
	file_checksum: str,
	file_size_bytes: int,
	snapshot_taken_at: str,
	duration_seconds: float,
):
	"""Mark a job as Exported with output metadata."""
	sql = (
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Exported', exported_at = NOW(), execution_stage = 'exported', "
		"    row_count = %s, file_path = %s, file_checksum = %s, "
		"    file_size_bytes = %s, snapshot_taken_at = %s, duration_seconds = %s "
		"WHERE name = %s"
	)
	atomic_update(
		config, sql,
		(row_count, file_path, file_checksum, file_size_bytes, snapshot_taken_at, duration_seconds, job_name),
	)


def _mark_transferred(config: Config, job_name: str, remote_path: str):
	"""Mark a job as Transferred with remote path."""
	sql = (
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Transferred', transferred_at = NOW(), "
		"    remote_path = %s, execution_stage = 'transferred' "
		"WHERE name = %s"
	)
	atomic_update(config, sql, (remote_path, job_name))


def _mark_ingested(config: Config, job_name: str):
	"""Mark a job as Ingested."""
	sql = (
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Ingested', ingested_at = NOW(), execution_stage = 'ingested' "
		"WHERE name = %s"
	)
	atomic_update(config, sql, (job_name,))


def _mark_completed(config: Config, job_name: str):
	"""Mark a job as Completed and clear sync pause."""
	sql = (
		"UPDATE `tabMemora Archive Job` "
		"SET status = 'Completed', completed_at = NOW(), execution_stage = 'done', "
		"    sync_paused = 0, sync_paused_at = NULL "
		"WHERE name = %s"
	)
	atomic_update(config, sql, (job_name,))


def _fail_job(
	config: Config,
	job_name: str,
	error_msg: str,
	retry_count: int,
	current_status: str = "Processing",
	stage: str | None = None,
):
	"""Handle job failure with automatic retry up to 3 attempts.

	If retry_count < 3: resets to the same state for retry with incremented retry_count.
	If retry_count >= 3: permanently fails with completed_at timestamp.

	stage: Optional execution stage at failure time (e.g. 'exporting_fact').
	       Prepended to error_log for observability since execution_stage is
	       cleared to NULL on retry reset (FR-014: failure phase logging).
	"""
	if stage:
		error_msg = f"Phase: {stage}\n{error_msg}" if error_msg else f"Phase: {stage}"
	error_msg = error_msg[:60000] if error_msg else ""

	if retry_count < 3:
		# Auto-retry: reset to Pending with incremented retry_count so
		# _process_pending_jobs() picks it up again (avoids stuck Processing state)
		sql = (
			"UPDATE `tabMemora Archive Job` "
			"SET status = 'Pending', retry_count = retry_count + 1, "
			"    error_log = %s, execution_stage = NULL "
			"WHERE name = %s AND status = %s"
		)
		atomic_update(config, sql, (error_msg, job_name, current_status))
	else:
		# Permanent failure: exhausted all retries — clear sync_paused to unblock live sync
		sql = (
			"UPDATE `tabMemora Archive Job` "
			"SET status = 'Failed', error_log = %s, completed_at = NOW(), "
			"    sync_paused = 0, sync_paused_at = NULL "
			"WHERE name = %s AND status = %s"
		)
		atomic_update(config, sql, (error_msg, job_name, current_status))


# ---------------------------------------------------------------------------
# Derived dimension helpers
# ---------------------------------------------------------------------------


def _extract_ids_from_parquet(parquet_path: str, column_name: str) -> set:
	"""Read a Parquet file and extract unique non-null values from a column."""
	table = pq.read_table(parquet_path, columns=[column_name])
	col = table.column(column_name)
	return {val.as_py() for val in col if val.as_py() is not None}


def _export_season_dimension_by_seq(
	config: Config,
	staging_dir: str,
	dim_schema: dict,
	season_seq: int,
) -> tuple[str, int]:
	"""Export season dimension by querying season_seq directly.

	Used for season-scoped archive jobs where the season IS the scope,
	rather than being derived from the player dimension.
	"""
	entity = dim_schema["entity"]
	fields = dim_schema["fields"]
	output_path = os.path.join(staging_dir, f"dim_{entity}.parquet")

	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT s.`name` AS season_id, s.`season_title`, "
				"s.`start_date`, s.`end_date` "
				"FROM `tabMemora Season` s WHERE s.`season_seq` = %s",
				(season_seq,),
			)
			rows = cursor.fetchall()
	finally:
		conn.close()

	if not rows:
		empty_data = {f: [] for f in fields}
		table = pa.table(empty_data)
		pq.write_table(table, output_path)
		return output_path, 0

	col_data = {f: [row.get(f) for row in rows] for f in fields}
	table = pa.table(col_data)
	pq.write_table(table, output_path)

	return output_path, len(rows)


def _export_derived_dimensions(
	config: Config,
	log: StructuredLogger,
	staging_dir: str,
	meta: dict,
	player_dim_path: str,
	archive_scope: str,
	query_filter: dict | None = None,
) -> list[dict]:
	"""Export derived dimensions (season, plan) based on player dimension data.

	Reads the already-exported player dimension Parquet to extract unique
	season_id and plan_id values, then exports season and plan dimensions.
	"""
	results = []

	for rt in meta.get("related_tables", []):
		if rt.get("scope_source") != "derived":
			continue

		entity = rt["entity"]
		schema_version = rt["schema_version"]
		dim_schema = load_dimension_schema(config.schema_registry_path, entity, schema_version)

		# Determine which column in the player dimension provides IDs
		if entity == "season":
			# Season-scoped jobs: query directly by season_seq
			if query_filter and query_filter.get("filter_type") == "season":
				dim_path, dim_row_count = _export_season_dimension_by_seq(
					config, staging_dir, dim_schema, query_filter["season_seq"],
				)
				results.append({
					"entity": entity,
					"schema_version": schema_version,
					"scope_source": "derived",
					"path": dim_path,
					"row_count": dim_row_count,
				})
				continue

			ids = _extract_ids_from_parquet(player_dim_path, "season_id")
			# Also include the archive_scope (which is a season ID)
			if archive_scope:
				ids.add(archive_scope)
		elif entity == "plan":
			ids = _extract_ids_from_parquet(player_dim_path, "plan_id")
		else:
			log.warning("unknown_derived_dimension", entity=entity)
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
# Pipeline phases
# ---------------------------------------------------------------------------


def _cleanup_staging(staging_dir: str):
	"""Remove the staging directory if it exists."""
	if staging_dir and os.path.isdir(staging_dir):
		shutil.rmtree(staging_dir, ignore_errors=True)


def _set_permissions(directory: str):
	"""Set directory to 0700 and all files within to 0600."""
	os.chmod(directory, 0o700)
	for entry in os.listdir(directory):
		os.chmod(os.path.join(directory, entry), 0o600)


def _process_pending_jobs(config: Config, log: StructuredLogger) -> tuple[int, int]:
	"""Process Pending jobs: claim -> export -> Exported.

	Returns (processed, failed) counts.
	"""
	pending_jobs = _get_jobs_by_status(config, "Pending")
	if not pending_jobs:
		return 0, 0

	log.info("pending_jobs_found", count=len(pending_jobs))
	processed = 0
	failed = 0

	for job in pending_jobs:
		job_name = job["name"]

		# Validate job_name to prevent path traversal
		if not _JOB_NAME_RE.match(job_name):
			log.error("invalid_job_name", job=job_name)
			continue

		# Atomic claim (also sets sync_paused=1)
		if not _claim_job(config, job_name):
			log.info("job_claim_skipped", job=job_name, reason="already_claimed")
			continue

		log.info("job_claimed", job=job_name, source=job["source_doctype"], scope=job["archive_scope"])

		start_time = time.monotonic()
		staging_dir = os.path.join(config.archive_output_path, ".staging", job_name)
		final_dir = os.path.join(config.archive_output_path, job_name)

		# Belt-and-suspenders: verify resolved paths stay under archive_output_path
		real_output = os.path.realpath(config.archive_output_path)
		if not os.path.realpath(staging_dir).startswith(real_output):
			log.error("path_traversal", job=job_name, staging_dir=staging_dir)
			continue
		if not os.path.realpath(final_dir).startswith(real_output):
			log.error("path_traversal", job=job_name, final_dir=final_dir)
			continue

		try:
			_export_job(config, job, log, staging_dir, final_dir, start_time)
			processed += 1
		except Exception as exc:
			_cleanup_staging(staging_dir)
			error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
			retry_count = job.get("retry_count", 0) or 0
			failed_stage = _read_stage(config, job_name)
			try:
				_fail_job(config, job_name, error_msg, retry_count, "Processing", stage=failed_stage)
			except Exception as fail_exc:
				log.error("fail_job_error", job=job_name, original_error=str(exc), fail_error=str(fail_exc))
			if retry_count < 3:
				log.warning("job_retryable_failure", job=job_name, retry_count=retry_count + 1, error=str(exc))
			else:
				log.error("job_permanently_failed", job=job_name, retry_count=retry_count, error=str(exc))
				failed += 1

	return processed, failed


def _export_job(config: Config, job: dict, log: StructuredLogger, staging_dir: str, final_dir: str, start_time: float):
	"""Export a single job from Processing -> Exported."""
	job_name = job["name"]

	# Parse meta JSON
	meta = json.loads(job["job_meta"]) if isinstance(job["job_meta"], str) else (job["job_meta"] or {})
	archive_type_key = job.get("archive_type") or "practice_log"
	source_table = f"tab{job['source_doctype']}"

	# Create staging directory
	os.makedirs(staging_dir, exist_ok=True)

	# Record snapshot timestamp
	snapshot_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

	# Build export_metadata dict for injection into fact rows
	export_metadata = {
		"archive_scope": job["archive_scope"] or "",
		"archive_job_id": job_name,
		"schema_version": job["schema_version"] or "",
		"exported_at": snapshot_ts,
	}

	# --- Export fact data ---
	_update_stage(config, job_name, "exporting_fact")
	log.info("exporting_fact", job=job_name)

	fact_path, fact_row_count, referenced_ids = export_fact_data(
		config=config,
		staging_dir=staging_dir,
		meta=meta,
		source_table=source_table,
		archive_type_name=archive_type_key,
		export_metadata=export_metadata,
	)

	# --- Export dimension snapshots ---
	_update_stage(config, job_name, "exporting_dimensions")
	log.info("exporting_dimensions", job=job_name)

	dimension_results = []
	player_dim_path = None

	# Pass 1: Direct dimensions (those with a join_column, no scope_source)
	for rt in meta.get("related_tables", []):
		if rt.get("scope_source") == "derived":
			continue

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

		if entity == "player":
			player_dim_path = dim_path

		dimension_results.append(
			{
				"entity": entity,
				"schema_version": schema_version,
				"fact_column": fact_col,
				"path": dim_path,
				"row_count": dim_row_count,
			}
		)

	# Pass 2: Derived dimensions (season, plan) — scoped from player dimension
	query_filter = meta.get("query_filter", {})
	if player_dim_path:
		derived_results = _export_derived_dimensions(
			config=config,
			log=log,
			staging_dir=staging_dir,
			meta=meta,
			player_dim_path=player_dim_path,
			archive_scope=job.get("archive_scope", ""),
			query_filter=query_filter,
		)
		dimension_results.extend(derived_results)

	# --- Validate fact data quality ---
	_update_stage(config, job_name, "validating_dq")
	log.info("validating_dq", job=job_name)

	# Build dimension path map for DQ validation
	dimension_path_map = {dr["entity"]: dr["path"] for dr in dimension_results}

	# Dispatch to generic DQ engine if archive type YAML defines dq_rules; else legacy
	archive_schema = None
	try:
		schema_ver = job.get("schema_version") or "v1"
		archive_schema = load_archive_type(config.schema_registry_path, archive_type_key, schema_ver)
	except FileNotFoundError:
		pass

	dq_rules = archive_schema.get("dq_rules") if archive_schema else None

	if dq_rules:
		dq_result = validate_fact_quality_generic(
			fact_path=fact_path,
			dq_rules=dq_rules,
			dimension_paths=dimension_path_map,
			scope_date_from=query_filter.get("date_from"),
			scope_date_to=query_filter.get("date_to"),
		)
	else:
		# Legacy: Practice Log (no dq_rules in YAML)
		dq_result = validate_fact_quality(
			fact_path=fact_path,
			dim_player_path=dimension_path_map.get("player"),
			dim_review_item_path=dimension_path_map.get("review_item"),
			scope_date_from=query_filter.get("date_from"),
			scope_date_to=query_filter.get("date_to"),
		)

	if not dq_result["passed"]:
		failed_rules = [r["rule"] for r in dq_result["results"] if not r["passed"]]
		raise RuntimeError(f"Data quality validation failed: {', '.join(failed_rules)}")

	if dq_result.get("warnings"):
		for w in dq_result["warnings"]:
			log.warning("dq_warning", job=job_name, warning=w)

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
			"entity": job["archive_type"],
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
		dataset_key=f"{job['archive_type']}_archive",
		kind="archive",
		schema_version="1.0",
		source="memora_admin",
		scope_key=job.get("archive_scope") or None,
		files=file_entries,
	)

	# --- Publish: atomic rename staging -> final ---
	_update_stage(config, job_name, "publishing")
	log.info("publishing", job=job_name)

	# Handle pre-existing final_dir from a previous (retried) run
	if os.path.isdir(final_dir):
		old_dir = final_dir + ".old"
		if os.path.isdir(old_dir):
			shutil.rmtree(old_dir)
		os.rename(final_dir, old_dir)
	else:
		old_dir = None

	try:
		os.rename(staging_dir, final_dir)
	except OSError:
		# Cross-filesystem: copy + verify + remove staging
		shutil.copytree(staging_dir, final_dir)
		shutil.rmtree(staging_dir)

	# Clean up .old after successful swap
	if old_dir and os.path.isdir(old_dir):
		shutil.rmtree(old_dir, ignore_errors=True)

	# Set permissions
	_set_permissions(final_dir)

	# --- Mark Exported ---
	duration = time.monotonic() - start_time
	fact_checksum = fact_validation["checksum"]
	fact_size = fact_validation["size_bytes"]

	_mark_exported(
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
		"job_exported",
		job=job_name,
		rows=fact_row_count,
		duration_s=round(duration, 2),
		dimensions=len(dimension_results),
	)

	# 0-row batches: no data to transfer/ingest — mark Completed immediately
	if fact_row_count == 0:
		_mark_ingested(config, job_name)
		_mark_completed(config, job_name)
		log.info("job_completed_empty", job=job_name, reason="fact_has_0_rows")


def _process_exported_jobs(config: Config, log: StructuredLogger) -> int:
	"""Transfer Exported jobs to analytics server. Returns count processed."""
	if not config.has_ssh_config():
		log.debug("transfer_skipped", reason="ssh_not_configured")
		return 0

	jobs = _get_jobs_by_status(config, "Exported")
	if not jobs:
		return 0

	log.info("exported_jobs_found", count=len(jobs))
	processed = 0

	for job in jobs:
		job_name = job["name"]
		local_dir = job.get("file_path")

		if not local_dir or not os.path.isdir(local_dir):
			log.warning("transfer_skipped", job=job_name, reason="local_dir_missing")
			continue

		try:
			_update_stage(config, job_name, "transferring")

			# Transfer via rsync
			remote_path = transfer_batch(config, local_dir, config.remote_archive_path, job_name, log)

			# Load manifest for checksum verification
			manifest_path = os.path.join(local_dir, "manifest.json")
			with open(manifest_path) as f:
				manifest = json.load(f)

			# Verify remote checksums
			_update_stage(config, job_name, "verifying_transfer")
			result = verify_remote_checksums(config, remote_path, manifest, log)

			if not result["valid"]:
				raise TransferError(f"Checksum verification failed: {result['errors']}")

			_mark_transferred(config, job_name, remote_path)
			processed += 1
			log.info("job_transferred", job=job_name, remote_path=remote_path)

		except Exception as exc:
			error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
			retry_count = job.get("retry_count", 0) or 0
			failed_stage = _read_stage(config, job_name)
			try:
				_fail_job(config, job_name, error_msg, retry_count, "Exported", stage=failed_stage)
			except Exception:
				log.error("fail_job_error", job=job_name, error=str(exc))
			if retry_count >= 3:
				log.error("job_permanently_failed", job=job_name, error=str(exc))

	return processed


def _process_transferred_jobs(config: Config, log: StructuredLogger) -> int:
	"""Ingest Transferred jobs into analytics DuckDB. Returns count processed."""
	if not config.has_ssh_config():
		log.debug("ingestion_skipped", reason="ssh_not_configured")
		return 0

	jobs = _get_jobs_by_status(config, "Transferred")
	if not jobs:
		return 0

	log.info("transferred_jobs_found", count=len(jobs))
	processed = 0

	for job in jobs:
		job_name = job["name"]
		remote_path = job.get("remote_path")
		local_dir = job.get("file_path")

		if not remote_path:
			log.warning("ingestion_skipped", job=job_name, reason="no_remote_path")
			continue

		try:
			_update_stage(config, job_name, "ingesting")

			# Load local manifest for metadata
			manifest_path = os.path.join(local_dir, "manifest.json") if local_dir else None
			manifest = {}
			if manifest_path and os.path.isfile(manifest_path):
				with open(manifest_path) as f:
					manifest = json.load(f)

			# Ingest into DuckDB
			ingest_result = ingest_archive_batch(config, remote_path, manifest, log)

			# Verify ingestion
			_update_stage(config, job_name, "verifying_ingestion")
			verify_result = verify_ingestion(config, manifest, remote_path, log)

			if not verify_result.get("valid", False):
				raise IngestionError(f"Ingestion verification failed: {verify_result.get('errors', [])}")

			_mark_ingested(config, job_name)
			processed += 1
			log.info("job_ingested", job=job_name)

		except Exception as exc:
			error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
			retry_count = job.get("retry_count", 0) or 0
			failed_stage = _read_stage(config, job_name)
			try:
				_fail_job(config, job_name, error_msg, retry_count, "Transferred", stage=failed_stage)
			except Exception:
				log.error("fail_job_error", job=job_name, error=str(exc))
			if retry_count >= 3:
				log.error("job_permanently_failed", job=job_name, error=str(exc))

	return processed


def _process_ingested_jobs(config: Config, log: StructuredLogger) -> int:
	"""Handoff Ingested jobs: remove overlapping live data, mark Completed. Returns count processed."""
	if not config.has_ssh_config():
		log.debug("handoff_skipped", reason="ssh_not_configured")
		return 0

	jobs = _get_jobs_by_status(config, "Ingested")
	if not jobs:
		return 0

	log.info("ingested_jobs_found", count=len(jobs))
	processed = 0

	for job in jobs:
		job_name = job["name"]
		remote_path = job.get("remote_path")

		if not remote_path:
			log.warning("handoff_skipped", job=job_name, reason="no_remote_path")
			continue

		try:
			_update_stage(config, job_name, "handoff")

			# Parse query_filter from meta for date range
			meta = json.loads(job["job_meta"]) if isinstance(job["job_meta"], str) else (job["job_meta"] or {})
			query_filter = meta.get("query_filter", {})

			# Call handoff — analytics server removes overlapping live data
			# Season-scoped jobs use season-based handoff (mirror cleanup by season_seq)
			if query_filter.get("filter_type") == "season":
				archive_type = job.get("archive_type") or "memory_state"
				handoff_season(
					config, remote_path, query_filter["season_seq"], archive_type, log,
				)
			else:
				handoff_archive(config, remote_path, query_filter, log)

			# Refresh analytics layers (best-effort — failure does not block Completed)
			archive_type = job.get("archive_type") or "practice_log"
			try:
				_update_stage(config, job_name, "refreshing_recent")
				refresh_recent(config, archive_type, log)
			except Exception as refresh_exc:
				log.warning("refresh_recent_failed", job=job_name, error=str(refresh_exc))

			try:
				_update_stage(config, job_name, "refreshing_aggregates")
				refresh_aggregates(config, archive_type, log)
			except Exception as refresh_exc:
				log.warning("refresh_aggregates_failed", job=job_name, error=str(refresh_exc))

			_mark_completed(config, job_name)
			processed += 1
			log.info("job_completed", job=job_name)

		except Exception as exc:
			error_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
			retry_count = job.get("retry_count", 0) or 0
			failed_stage = _read_stage(config, job_name)
			try:
				_fail_job(config, job_name, error_msg, retry_count, "Ingested", stage=failed_stage)
			except Exception:
				log.error("fail_job_error", job=job_name, error=str(exc))
			if retry_count >= 3:
				log.error("job_permanently_failed", job=job_name, error=str(exc))

	return processed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
	"""Main entry point for the archive executor."""
	config = Config.from_env()
	log = StructuredLogger(config.log_path)

	log.info("run_started", pid=os.getpid())

	# Step 1: Acquire file lock
	lock_fd = acquire_lock(config.lock_file)
	if lock_fd is None:
		log.info("run_skipped", reason="lock_held")
		sys.exit(0)

	# Initialize counters before try so they're defined even if an early stage throws
	exported = 0
	export_failed = 0
	transferred = 0
	ingested = 0
	completed = 0

	try:
		# Step 2: Fail stuck jobs (all active states)
		_fail_stuck_jobs(config, log)

		# Step 3: Process pipeline stages
		exported, export_failed = _process_pending_jobs(config, log)
		transferred = _process_exported_jobs(config, log)
		ingested = _process_transferred_jobs(config, log)
		completed = _process_ingested_jobs(config, log)

		# Step 4: Purge source data for eligible completed jobs
		try:
			purge_completed_jobs(config, log)
		except Exception as exc:
			log.error("purge_error", error=str(exc))

		# Step 5: Clean up local copies for completed+ jobs
		try:
			cleanup_local_copies(config, log)
		except Exception as exc:
			log.error("local_cleanup_error", error=str(exc))

	finally:
		release_lock(lock_fd)

	log.info(
		"run_finished",
		exported=exported,
		export_failed=export_failed,
		transferred=transferred,
		ingested=ingested,
		completed=completed,
	)


if __name__ == "__main__":
	main()
