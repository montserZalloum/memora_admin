"""Compact small Parquet files within Hive-partitioned dataset directories.

Scans leaf partition directories for small files (below a configurable
threshold) and merges them into a single Parquet file per partition.
Verifies row counts before and after to guarantee data integrity (FR-018).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import click
import duckdb

log = logging.getLogger("memora-analytics")


# ── CLI command ──────────────────────────────────────────────────────────────


@click.command("compact")
@click.option("--dataset", required=True, help="Dataset name (e.g., practice_log)")
@click.option("--partition", default=None, help="Specific partition path to compact")
@click.option("--threshold-mb", default=64, type=int, help="File size threshold in MB")
@click.option("--dry-run", is_flag=True, default=False, help="Show plan without executing")
@click.pass_obj
def compact(cfg, dataset: str, partition: str | None, threshold_mb: int, dry_run: bool) -> None:
    """Merge small Parquet files within a dataset's partitions."""
    from analytics_cli.__main__ import emit_json, emit_error

    t0 = time.monotonic()
    lake_root = Path(cfg.lake_path)
    dataset_dir = lake_root / dataset

    if not dataset_dir.is_dir():
        emit_error(
            f"Dataset directory does not exist: {dataset_dir}",
            partitions_scanned=0,
            partitions_compacted=0,
            files_merged=0,
            files_removed=0,
            total_rows_before=0,
            total_rows_after=0,
            dry_run=dry_run,
            duration_ms=0,
        )

    # Discover leaf partition directories (directories containing .parquet files)
    leaf_dirs = _find_leaf_dirs(dataset_dir, partition)

    partitions_scanned = 0
    partitions_compacted = 0
    files_merged = 0
    files_removed = 0
    total_rows_before = 0
    total_rows_after = 0

    threshold_bytes = threshold_mb * 1024 * 1024

    for leaf in leaf_dirs:
        parquet_files = sorted(leaf.glob("*.parquet"))
        partitions_scanned += 1

        # Need at least 2 files to merge
        if len(parquet_files) < 2:
            continue

        # Check if any file is below threshold
        has_small = any(f.stat().st_size < threshold_bytes for f in parquet_files)
        if not has_small:
            continue

        # Count rows before merge
        file_paths = [str(f) for f in parquet_files]
        rows_before = _count_rows(file_paths)
        total_rows_before += rows_before

        if dry_run:
            # Report plan without executing
            partitions_compacted += 1
            files_merged += len(parquet_files)
            files_removed += len(parquet_files)
            total_rows_after += rows_before  # In dry-run, assume rows match
            continue

        # Merge all files into a single temporary file
        merged_path = leaf / "merged-part-0000.parquet"
        try:
            _merge_files(file_paths, str(merged_path))
        except Exception as exc:
            log.error("Merge failed for %s: %s", leaf, exc)
            continue

        # Verify row count after merge
        rows_after = _count_rows([str(merged_path)])

        if rows_after != rows_before:
            # Row count mismatch — remove merged file and report error
            log.error(
                "Row count mismatch in %s: before=%d after=%d",
                leaf, rows_before, rows_after,
            )
            merged_path.unlink(missing_ok=True)
            continue

        total_rows_after += rows_after

        # Remove original files
        for f in parquet_files:
            f.unlink()
            files_removed += 1

        # Rename merged file to final name
        final_path = leaf / "part-0000.parquet"
        merged_path.rename(final_path)

        files_merged += len(parquet_files)
        partitions_compacted += 1

    status = "ok" if partitions_compacted > 0 else "noop"

    duration_ms = int((time.monotonic() - t0) * 1000)
    emit_json({
        "status": status,
        "partitions_scanned": partitions_scanned,
        "partitions_compacted": partitions_compacted,
        "files_merged": files_merged,
        "files_removed": files_removed,
        "total_rows_before": total_rows_before,
        "total_rows_after": total_rows_after,
        "dry_run": dry_run,
        "duration_ms": duration_ms,
    })


# ── Internal helpers ─────────────────────────────────────────────────────────


def _find_leaf_dirs(dataset_dir: Path, partition: str | None) -> list[Path]:
    """Find leaf directories that contain .parquet files.

    If *partition* is given, restrict to that specific partition path
    (relative to dataset_dir).
    """
    if partition is not None:
        target = dataset_dir / partition
        if target.is_dir() and any(target.glob("*.parquet")):
            return [target]
        return []

    leaves: list[Path] = []
    for root, dirs, files in os.walk(dataset_dir):
        if any(f.endswith(".parquet") for f in files):
            leaves.append(Path(root))
    return sorted(leaves)


def _quote_paths(file_paths: list[str]) -> str:
    """Build a DuckDB list literal from file paths, e.g. ``['a.parquet', 'b.parquet']``."""
    quoted = ", ".join(f"'{p}'" for p in file_paths)
    return f"[{quoted}]"


def _count_rows(file_paths: list[str]) -> int:
    """Count total rows across multiple Parquet files using DuckDB."""
    conn = duckdb.connect(":memory:")
    try:
        paths_lit = _quote_paths(file_paths)
        result = conn.execute(
            f"SELECT COUNT(*) FROM read_parquet({paths_lit})",
        ).fetchone()
        return result[0] if result else 0
    finally:
        conn.close()


def _merge_files(file_paths: list[str], output_path: str) -> None:
    """Merge multiple Parquet files into a single file using DuckDB."""
    conn = duckdb.connect(":memory:")
    try:
        paths_lit = _quote_paths(file_paths)
        conn.execute(
            f"COPY (SELECT * FROM read_parquet({paths_lit})) TO '{output_path}' (FORMAT PARQUET)",
        )
    finally:
        conn.close()
