"""Incremental sync engine for Memory State active seasons.

Discovers active seasons, extracts rows modified since the last checkpoint
(with a safety overlap), exports to Parquet, transfers to the analytics
server, and triggers ingest-live for upsert into the current mirror.

Usage:
    python -m archive_executor.sync --archive-type memory_state

Cron (every 15 minutes):
    */15 * * * * /opt/memora-archive/venv/bin/python -m archive_executor.sync \
                    --archive-type memory_state
"""

import argparse
import json
import os
import shutil
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from .config import Config
from .db import get_connection, streaming_cursor
from .exporter import _build_arrow_schema, _coerce_value, _rows_to_batch
from .ingestion import ingest_live_snapshot
from .logger import StructuredLogger
from .schemas import load_archive_type
from .transfer import transfer_batch


# ---------------------------------------------------------------------------
# Season discovery (T005)
# ---------------------------------------------------------------------------


def _discover_active_seasons(config: Config) -> list[dict]:
	"""Query tabMemora Season for published seasons where end_date >= today."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT s.name AS season_name, s.season_seq "
				"FROM `tabMemora Season` s "
				"WHERE s.end_date >= CURDATE() "
				"  AND s.is_published = 1 "
				"ORDER BY s.season_seq"
			)
			return cursor.fetchall()
	finally:
		conn.close()


# ---------------------------------------------------------------------------
# Checkpoint management (T005)
# ---------------------------------------------------------------------------


def _checkpoint_path(config: Config, archive_type: str, season_seq: int) -> str:
	"""Return the path to the checkpoint JSON file for a season."""
	return os.path.join(
		config.sync_state_path, archive_type, f"season_{season_seq}.json"
	)


def _load_checkpoint(config: Config, archive_type: str, season_seq: int) -> dict:
	"""Load the sync checkpoint for a season.

	Returns the checkpoint dict, or a default with epoch timestamp if none exists.
	"""
	path = _checkpoint_path(config, archive_type, season_seq)
	if os.path.isfile(path):
		with open(path) as f:
			return json.load(f)
	return {
		"season_seq": season_seq,
		"season_name": "",
		"last_checkpoint": "1970-01-01T00:00:00",
		"last_sync_rows": 0,
		"total_rows_synced": 0,
		"last_sync_at": None,
	}


def _save_checkpoint(config: Config, archive_type: str, checkpoint: dict) -> None:
	"""Save the sync checkpoint for a season atomically via tmp+replace."""
	path = _checkpoint_path(config, archive_type, checkpoint["season_seq"])
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp_path = path + ".tmp"
	with open(tmp_path, "w") as f:
		json.dump(checkpoint, f, indent=2)
	os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Incremental extraction (T006)
# ---------------------------------------------------------------------------


def _extract_incremental(
	config: Config,
	archive_schema: dict,
	season_seq: int,
	extract_from: str,
) -> tuple[list[dict], str | None]:
	"""Extract rows modified since extract_from for a given season.

	Uses the incremental fact SQL from the archive type schema with a
	streaming cursor for memory efficiency.

	Returns:
		Tuple of (rows, max_modified) where max_modified is the ISO string
		of the latest modified timestamp, or None if no rows.
	"""
	fact_sql = archive_schema.get("fact_sql", {})
	sql = fact_sql.get("incremental")
	if not sql:
		raise ValueError("Archive schema missing 'fact_sql.incremental' template")

	sql = sql.strip()
	params = (season_seq, extract_from)

	rows = []
	max_modified = None

	try:
		with streaming_cursor(config) as cursor:
			cursor.execute(sql, params)
			while True:
				batch = cursor.fetchmany(config.chunk_size)
				if not batch:
					break
				for row in batch:
					rows.append(row)
					mod = row.get("modified")
					if mod is not None:
						mod_str = mod.isoformat() if hasattr(mod, "isoformat") else str(mod)
						if max_modified is None or mod_str > max_modified:
							max_modified = mod_str
	except Exception as exc:
		raise type(exc)(
			f"Extraction failed for season_seq={season_seq}, "
			f"extract_from={extract_from}: {exc}"
		) from exc

	return rows, max_modified


# ---------------------------------------------------------------------------
# Parquet export (T007)
# ---------------------------------------------------------------------------


def _export_sync_parquet(
	config: Config,
	archive_schema: dict,
	rows: list[dict],
	season_seq: int,
	archive_type: str,
) -> str:
	"""Write extracted rows to a sync Parquet file with metadata columns.

	Output path: {sync_output_path}/{archive_type}/season_{N}/sync_{timestamp}.parquet
	Injects synced_at and archive_scope metadata columns.

	Returns the path to the output directory containing the Parquet file.
	"""
	now = datetime.now(timezone.utc)
	timestamp_str = now.strftime("%Y%m%dT%H%M%S")
	synced_at = now.strftime("%Y-%m-%dT%H:%M:%S")

	output_dir = os.path.join(
		config.sync_output_path, archive_type, f"season_{season_seq}"
	)
	os.makedirs(output_dir, exist_ok=True)
	output_path = os.path.join(output_dir, f"sync_{timestamp_str}.parquet")

	# Build arrow schema from the archive schema snapshot
	schema_snapshot = archive_schema.get("schema_snapshot", {})
	arrow_schema = _build_arrow_schema(schema_snapshot) if schema_snapshot.get("columns") else None

	# Add metadata columns to schema
	if arrow_schema:
		extra_fields = [
			pa.field("archive_scope", pa.string()),
			pa.field("synced_at", pa.timestamp("us")),
		]
		arrow_schema = pa.schema(list(arrow_schema) + extra_fields)

	# Inject metadata into rows
	for row in rows:
		row["archive_scope"] = f"season_{season_seq}"
		row["synced_at"] = synced_at

	# Build columns list
	export_columns = archive_schema.get("fact_columns", [])
	all_columns = list(export_columns) + ["archive_scope", "synced_at"]

	if arrow_schema:
		batch = _rows_to_batch(rows, all_columns, arrow_schema)
	else:
		col_data = {col: [_coerce_value(row.get(col)) for row in rows] for col in all_columns}
		batch = pa.RecordBatch.from_pydict(col_data)
		arrow_schema = batch.schema

	writer = pq.ParquetWriter(output_path, arrow_schema)
	writer.write_batch(batch)
	writer.close()

	return output_dir


# ---------------------------------------------------------------------------
# Streaming extract + export (used by the orchestrator to bound peak memory)
# ---------------------------------------------------------------------------


def _stream_extract_export(
	config: Config,
	archive_schema: dict,
	season_seq: int,
	extract_from: str,
	archive_type: str,
) -> tuple[str | None, int, str | None]:
	"""Stream-extract changed rows and write to Parquet one chunk at a time.

	Each DB fetch chunk is written as a separate Parquet row group so that
	peak memory is bounded to O(chunk_size) regardless of season size, unlike
	the batch _extract_incremental + _export_sync_parquet pair which
	accumulates the full result set in memory.

	Returns:
		(output_dir, row_count, max_modified) where output_dir is None when
		no rows were extracted.
	"""
	fact_sql = archive_schema.get("fact_sql", {})
	sql = fact_sql.get("incremental")
	if not sql:
		raise ValueError("Archive schema missing 'fact_sql.incremental' template")

	now = datetime.now(timezone.utc)
	timestamp_str = now.strftime("%Y%m%dT%H%M%S")
	synced_at = now.strftime("%Y-%m-%dT%H:%M:%S")
	archive_scope = f"season_{season_seq}"

	output_dir = os.path.join(
		config.sync_output_path, archive_type, f"season_{season_seq}"
	)
	os.makedirs(output_dir, exist_ok=True)
	output_path = os.path.join(output_dir, f"sync_{timestamp_str}.parquet")

	schema_snapshot = archive_schema.get("schema_snapshot", {})
	arrow_schema = _build_arrow_schema(schema_snapshot) if schema_snapshot.get("columns") else None
	if arrow_schema:
		extra_fields = [
			pa.field("archive_scope", pa.string()),
			pa.field("synced_at", pa.timestamp("us")),
		]
		arrow_schema = pa.schema(list(arrow_schema) + extra_fields)

	export_columns = archive_schema.get("fact_columns", [])
	all_columns = list(export_columns) + ["archive_scope", "synced_at"]

	max_modified = None
	row_count = 0
	writer = None

	try:
		with streaming_cursor(config) as cursor:
			cursor.execute(sql.strip(), (season_seq, extract_from))
			while True:
				chunk = cursor.fetchmany(config.chunk_size)
				if not chunk:
					break
				for row in chunk:
					row["archive_scope"] = archive_scope
					row["synced_at"] = synced_at
					mod = row.get("modified")
					if mod is not None:
						mod_str = mod.isoformat() if hasattr(mod, "isoformat") else str(mod)
						if max_modified is None or mod_str > max_modified:
							max_modified = mod_str

				if arrow_schema:
					record_batch = _rows_to_batch(chunk, all_columns, arrow_schema)
				else:
					col_data = {col: [_coerce_value(row.get(col)) for row in chunk] for col in all_columns}
					record_batch = pa.RecordBatch.from_pydict(col_data)
					arrow_schema = record_batch.schema

				if writer is None:
					writer = pq.ParquetWriter(output_path, arrow_schema)
				writer.write_batch(record_batch)
				row_count += len(chunk)
	except Exception as exc:
		raise type(exc)(
			f"Extraction failed for season_seq={season_seq}, "
			f"extract_from={extract_from}: {exc}"
		) from exc
	finally:
		if writer is not None:
			writer.close()

	if row_count == 0:
		return None, 0, None

	return output_dir, row_count, max_modified


# ---------------------------------------------------------------------------
# Transfer and ingest (T008)
# ---------------------------------------------------------------------------


def _transfer_and_ingest(
	config: Config,
	log: StructuredLogger,
	local_dir: str,
	season_seq: int,
	archive_type: str,
) -> None:
	"""Transfer sync Parquet to analytics server and trigger ingestion.

	1. rsync local_dir to {sync_remote_path}/{archive_type}/season_{N}/
	2. Call memora-analytics ingest-live --batch-dir {remote_path}
	3. Clean up local Parquet on success.
	"""
	if not config.sync_remote_path:
		raise ValueError(
			"SYNC_REMOTE_PATH is not configured. "
			"Set SYNC_REMOTE_PATH to the remote directory for sync output "
			"(e.g. /data/analytics/sync)."
		)

	season_key = f"season_{season_seq}"
	remote_base = f"{config.sync_remote_path.rstrip('/')}/{archive_type}"

	# Transfer via rsync
	remote_path = transfer_batch(
		config=config,
		local_dir=local_dir,
		remote_base_path=remote_base,
		job_name=season_key,
		log=log,
	)

	# Ingest into analytics current mirror
	ingest_live_snapshot(
		config=config,
		remote_path=remote_path,
		manifest={},
		log=log,
	)

	# Cleanup local Parquet files on success
	shutil.rmtree(local_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Archive check (T009)
# ---------------------------------------------------------------------------


def _is_season_archived(config: Config, season_seq: int) -> bool:
	"""Check if a non-Failed archive job exists for this season.

	When True, sync should be skipped — the archive pipeline handles the
	final export for this season.
	"""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT 1 FROM `tabMemora Archive Job` "
				"WHERE source_doctype = 'Memora Memory State' "
				"  AND archive_scope = CONCAT('season_', %s) "
				"  AND schema_version = 'v1' "
				"  AND status NOT IN ('Failed') "
				"LIMIT 1",
				(season_seq,),
			)
			return cursor.fetchone() is not None
	finally:
		conn.close()


# ---------------------------------------------------------------------------
# Orchestrator (T010)
# ---------------------------------------------------------------------------


def run_incremental_sync(
	config: Config,
	archive_type: str = "memory_state",
) -> dict:
	"""Run one incremental sync cycle for all active seasons.

	For each active season:
	1. Check if season is archived (skip if so)
	2. Load checkpoint
	3. Extract changed rows with safety overlap
	4. Export to Parquet
	5. Transfer + ingest
	6. Update checkpoint (only after successful ingestion)

	Returns a JSON-serializable summary dict.
	"""
	log = StructuredLogger(config.log_path)
	log.info("sync_started", archive_type=archive_type)

	archive_schema = load_archive_type(config.schema_registry_path, archive_type, "v1")
	overlap = timedelta(seconds=config.sync_overlap_seconds)

	active_seasons = _discover_active_seasons(config)
	log.info("active_seasons_found", count=len(active_seasons))

	results = []
	seasons_synced = 0
	seasons_skipped = 0
	total_rows = 0

	for season in active_seasons:
		season_seq = season["season_seq"]
		season_name = season["season_name"]

		# Skip if an archive job exists for this season
		if _is_season_archived(config, season_seq):
			log.info("sync_skipped_archived", season_seq=season_seq)
			seasons_skipped += 1
			results.append({
				"season_seq": season_seq,
				"season_name": season_name,
				"rows_extracted": 0,
				"checkpoint": None,
				"status": "skipped_archived",
			})
			continue

		# Load checkpoint and compute extraction window
		checkpoint = _load_checkpoint(config, archive_type, season_seq)
		last_cp = datetime.fromisoformat(checkpoint["last_checkpoint"])
		extract_from = (last_cp - overlap).strftime("%Y-%m-%dT%H:%M:%S")

		try:
			# Stream-extract rows directly into Parquet, chunk by chunk.
			# Peak memory is O(chunk_size) regardless of season size.
			local_dir, row_count, max_modified = _stream_extract_export(
				config, archive_schema, season_seq, extract_from, archive_type
			)

			if row_count == 0:
				log.info("sync_no_rows", season_seq=season_seq)
				results.append({
					"season_seq": season_seq,
					"season_name": season_name,
					"rows_extracted": 0,
					"checkpoint": checkpoint["last_checkpoint"],
					"status": "ok",
				})
				continue

			# All rows fall within the already-ingested overlap window — no new data.
			# Discard the local Parquet written during extraction and skip transfer.
			if max_modified is not None and max_modified <= checkpoint["last_checkpoint"]:
				if local_dir:
					shutil.rmtree(local_dir, ignore_errors=True)
				log.info("sync_no_progress", season_seq=season_seq, max_modified=max_modified)
				results.append({
					"season_seq": season_seq,
					"season_name": season_name,
					"rows_extracted": row_count,
					"checkpoint": checkpoint["last_checkpoint"],
					"status": "ok",
				})
				continue

			# Transfer and ingest
			_transfer_and_ingest(config, log, local_dir, season_seq, archive_type)

			# Update checkpoint only after successful ingestion
			now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
			checkpoint.update({
				"season_name": season_name,
				"last_checkpoint": max_modified,
				"last_sync_rows": row_count,
				"total_rows_synced": checkpoint.get("total_rows_synced", 0) + row_count,
				"last_sync_at": now_str,
			})
			_save_checkpoint(config, archive_type, checkpoint)

			seasons_synced += 1
			total_rows += row_count
			results.append({
				"season_seq": season_seq,
				"season_name": season_name,
				"rows_extracted": row_count,
				"checkpoint": max_modified,
				"status": "ok",
			})

			log.info(
				"season_synced",
				season_seq=season_seq,
				rows=row_count,
				checkpoint=max_modified,
			)

		except Exception as exc:
			# Log error but continue with other seasons — checkpoint is NOT advanced.
			# Categorize the failure phase for actionable diagnostics.
			error_type = exc.__class__.__name__
			log.error(
				"sync_season_failed",
				season_seq=season_seq,
				season_name=season_name,
				error_type=error_type,
				error=str(exc),
				checkpoint_preserved=checkpoint["last_checkpoint"],
			)
			results.append({
				"season_seq": season_seq,
				"season_name": season_name,
				"rows_extracted": 0,
				"checkpoint": checkpoint["last_checkpoint"],
				"status": f"error: {error_type}: {exc}",
			})

	summary = {
		"archive_type": archive_type,
		"seasons_synced": seasons_synced,
		"seasons_skipped": seasons_skipped,
		"total_rows_synced": total_rows,
		"results": results,
	}

	log.info("sync_finished", **{k: v for k, v in summary.items() if k != "results"})
	return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
	"""CLI entry point for the incremental sync."""
	parser = argparse.ArgumentParser(description="Incremental sync for active seasons")
	parser.add_argument(
		"--archive-type",
		default="memory_state",
		help="Archive type (default: memory_state)",
	)
	args = parser.parse_args()

	config = Config.from_env()
	result = run_incremental_sync(config, args.archive_type)
	print(json.dumps(result, indent=2))


if __name__ == "__main__":
	main()
