"""Ingest archive Parquet files into the Hive-partitioned data lake.

Reads a batch directory containing manifest.json and Parquet files, copies
fact files to the correct Hive partition paths, copies dimension files to
the dimensions directory, stores the manifest, and refreshes DuckDB views.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from pathlib import Path

import click
import pyarrow.compute as pc
import pyarrow.parquet as pq

from analytics_cli.db import connect
from analytics_cli.views.semantic import refresh_all_views

log = logging.getLogger("memora-analytics")

# ── Partition config per entity ──────────────────────────────────────────────
# Date-partitioned: derive year/month/day from a timestamp column.

_DATE_PARTITION_COL: dict[str, str] = {
    "practice_log": "last_seen_at",
    "interaction_log": "timestamp",
    "task_run_log": "completed_at",
}

# Value-partitioned: use the column value directly as the partition key.
# The column is REMOVED from the Parquet file (encoded in directory name).

_VALUE_PARTITION_COL: dict[str, str] = {
    "memory_state": "season_seq",
    "structure_progress": "snapshot_date",
}


# ── CLI command ──────────────────────────────────────────────────────────────


@click.command("ingest-archive")
@click.option("--batch-dir", required=True, help="Path to batch directory with manifest.json and Parquet files.")
@click.pass_obj
def ingest_archive(cfg, batch_dir: str) -> None:
    """Load archive Parquet files into Hive-partitioned lake directory."""
    t0 = time.monotonic()
    batch_path = Path(batch_dir)

    if not batch_path.is_dir():
        _emit_error(f"batch-dir does not exist: {batch_dir}")

    manifest_file = batch_path / "manifest.json"
    if not manifest_file.exists():
        _emit_error(f"manifest.json not found in {batch_dir}")

    manifest = json.loads(manifest_file.read_text())
    batch_id = manifest.get("batch_id", batch_path.name)

    batches: list[dict] = []
    ok_count = 0
    err_count = 0

    for entry in manifest.get("files", []):
        src = batch_path / entry["filename"]
        result: dict = {"file": entry["filename"], "status": "ok"}
        try:
            if not src.exists():
                raise FileNotFoundError(f"File not found: {src}")

            if entry["role"] == "dimension":
                dest = Path(cfg.dimensions_path) / entry["filename"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dest))
                result["rows"] = entry.get("row_count", 0)
                result["destination"] = str(dest)

            elif entry["role"] == "fact":
                rows, dest = _ingest_fact(src, entry["entity"], cfg, batch_id)
                result["rows"] = rows
                result["destination"] = str(dest)

            else:
                raise ValueError(f"Unknown file role: {entry['role']}")

            ok_count += 1
        except Exception as exc:
            log.error("Ingest failed for %s: %s", entry["filename"], exc)
            result["status"] = "error"
            result["error"] = str(exc)
            err_count += 1

        batches.append(result)

    # Store manifest
    archive_manifest_dir = Path(cfg.manifests_path) / "archive"
    archive_manifest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(manifest_file), str(archive_manifest_dir / f"{batch_id}.json"))

    # Refresh views
    views_refreshed: list[str] = []
    try:
        with connect(cfg) as conn:
            views_refreshed = refresh_all_views(conn, cfg)
    except Exception as exc:
        log.error("View refresh failed: %s", exc)

    duration_ms = int((time.monotonic() - t0) * 1000)
    click.echo(json.dumps({
        "status": "ok" if err_count == 0 else "error",
        "batches_ok": ok_count,
        "batches_error": err_count,
        "batches": batches,
        "views_refreshed": views_refreshed,
        "duration_ms": duration_ms,
    }))

    if err_count > 0:
        sys.exit(1)


# ── Fact ingestion ───────────────────────────────────────────────────────────


def _ingest_fact(
    src: Path,
    entity: str,
    cfg,
    batch_id: str,
) -> tuple[int, Path]:
    """Read a fact Parquet, partition-write to the lake. Returns (row_count, dest_root)."""
    table = pq.read_table(str(src))
    row_count = table.num_rows
    lake_root = Path(cfg.lake_path)

    if entity in _DATE_PARTITION_COL:
        dest = _write_date_partitioned(
            table, lake_root / entity, _DATE_PARTITION_COL[entity], batch_id,
        )
    elif entity in _VALUE_PARTITION_COL:
        dest = _write_value_partitioned(
            table, lake_root / entity, _VALUE_PARTITION_COL[entity], batch_id,
        )
    else:
        # Unknown entity — store flat
        dest = lake_root / entity
        dest.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(dest / f"part-{batch_id}.parquet"))

    return row_count, dest


def _write_date_partitioned(
    table,
    dest_root: Path,
    date_column: str,
    batch_id: str,
) -> Path:
    """Write *table* partitioned by year/month/day derived from *date_column*."""
    dates = pc.cast(table[date_column], "date32")
    unique_dates = dates.unique()
    first_dest: Path | None = None

    for date_val in unique_dates:
        d = date_val.as_py()
        mask = pc.equal(dates, date_val)
        partition = table.filter(mask)
        part_dir = dest_root / f"year={d.year}" / f"month={d.month:02d}" / f"day={d.day:02d}"
        part_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(partition, str(part_dir / f"part-{batch_id}.parquet"))
        if first_dest is None:
            first_dest = part_dir

    return first_dest or dest_root


def _write_value_partitioned(
    table,
    dest_root: Path,
    partition_column: str,
    batch_id: str,
) -> Path:
    """Write *table* partitioned by *partition_column*, removing it from files."""
    col = table[partition_column]
    unique_vals = col.unique()
    first_dest: Path | None = None

    for val in unique_vals:
        py_val = val.as_py()
        mask = pc.equal(col, val)
        filtered = table.filter(mask)
        keep = [c for c in filtered.column_names if c != partition_column]
        partition = filtered.select(keep)
        part_dir = dest_root / f"{partition_column}={py_val}"
        part_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(partition, str(part_dir / f"part-{batch_id}.parquet"))
        if first_dest is None:
            first_dest = part_dir

    return first_dest or dest_root


# ── Helpers ──────────────────────────────────────────────────────────────────


def _emit_error(msg: str) -> None:
    """Emit a JSON error and exit."""
    click.echo(json.dumps({
        "status": "error",
        "error": msg,
        "batches_ok": 0,
        "batches_error": 0,
    }))
    sys.exit(1)
