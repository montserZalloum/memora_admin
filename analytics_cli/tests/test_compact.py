"""Tests for the compact CLI command (T030).

Covers:
- Merge small files in a single partition
- Row count verification (FR-018)
- Skip partitions with all files above threshold
- --dry-run returns plan without executing
- JSON response schema per cli-contract.json
- Multiple partitions — some need compaction, some don't
- Missing dataset directory → error status
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from analytics_cli.__main__ import cli
from analytics_cli.tests.conftest import (
    build_hive_dir,
    write_practice_log_parquet,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _invoke_compact(runner: CliRunner, cfg, extra_args: list[str]) -> object:
    """Invoke the compact command with cfg paths and extra CLI args."""
    args = [
        "--duckdb-path", cfg.duckdb_path,
        "--lake-path", cfg.lake_path,
        "--dimensions-path", cfg.dimensions_path,
        "--manifests-path", cfg.manifests_path,
        "compact",
    ] + extra_args
    return runner.invoke(cli, args, catch_exceptions=False)


def _create_partition_files(
    lake_dir: Path,
    dataset: str,
    partitions: dict[str, str],
    file_count: int = 3,
    rows_per_file: int = 5,
) -> list[Path]:
    """Create multiple small Parquet files in a Hive partition directory.

    Returns the list of created file paths.
    """
    created: list[Path] = []
    for i in range(file_count):
        filename = f"part-batch{i:04d}.parquet"
        dest = build_hive_dir(lake_dir, dataset, partitions, filename=filename)
        write_practice_log_parquet(dest, n=rows_per_file)
        created.append(dest)
    return created


# ── Tests ────────────────────────────────────────────────────────────────────


class TestCompactMerge:
    """Merge small files in a partition."""

    def test_merge_small_files_in_partition(self, cfg, lake_dir):
        """Three small Parquet files merged into one with same total rows."""
        partitions = {"year": "2025", "month": "06", "day": "15"}
        files = _create_partition_files(lake_dir, "practice_log", partitions, file_count=3, rows_per_file=5)
        part_dir = files[0].parent

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",  # 100 MB threshold — all test files are small
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)

        assert resp["status"] == "ok"
        assert resp["partitions_compacted"] == 1
        assert resp["files_merged"] == 3

        # Single merged file should exist
        merged_files = list(part_dir.glob("*.parquet"))
        assert len(merged_files) == 1
        assert merged_files[0].name == "part-0000.parquet"

        # Verify merged row count via pyarrow (read single file, not as dataset)
        pf = pq.ParquetFile(str(merged_files[0]))
        assert pf.metadata.num_rows == 15  # 3 files * 5 rows

    def test_row_count_verification(self, cfg, lake_dir):
        """rows_before == rows_after in response (FR-018)."""
        partitions = {"year": "2025", "month": "07", "day": "01"}
        _create_partition_files(lake_dir, "practice_log", partitions, file_count=2, rows_per_file=10)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["total_rows_before"] == 20
        assert resp["total_rows_after"] == 20
        assert resp["total_rows_before"] == resp["total_rows_after"]


class TestCompactSkip:
    """Skip partitions where all files are above threshold."""

    def test_skip_large_files(self, cfg, lake_dir):
        """Partition with all files above threshold → noop."""
        partitions = {"year": "2025", "month": "06", "day": "15"}
        _create_partition_files(lake_dir, "practice_log", partitions, file_count=2, rows_per_file=5)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "0",  # 0 MB threshold — no file is below 0 bytes
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["status"] == "noop"
        assert resp["partitions_scanned"] == 1
        assert resp["partitions_compacted"] == 0

    def test_single_file_partition_skipped(self, cfg, lake_dir):
        """Partition with only one file is skipped (nothing to merge)."""
        partitions = {"year": "2025", "month": "08", "day": "01"}
        _create_partition_files(lake_dir, "practice_log", partitions, file_count=1, rows_per_file=5)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["status"] == "noop"
        assert resp["partitions_compacted"] == 0


class TestCompactDryRun:
    """--dry-run returns plan without modifying files."""

    def test_dry_run_no_files_changed(self, cfg, lake_dir):
        """With --dry-run, original files remain untouched."""
        partitions = {"year": "2025", "month": "06", "day": "20"}
        files = _create_partition_files(lake_dir, "practice_log", partitions, file_count=3, rows_per_file=4)
        part_dir = files[0].parent

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",
            "--dry-run",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)

        assert resp["dry_run"] is True
        assert resp["partitions_compacted"] == 1
        assert resp["files_merged"] == 3

        # Original files should still exist
        remaining = list(part_dir.glob("*.parquet"))
        assert len(remaining) == 3
        # No merged file should have been created
        assert not (part_dir / "merged-part-0000.parquet").exists()
        assert not (part_dir / "part-0000.parquet").exists()


class TestCompactJsonSchema:
    """JSON response schema per cli-contract.json."""

    def test_response_has_all_required_fields(self, cfg, lake_dir):
        """Response contains all required fields with correct types."""
        partitions = {"year": "2025", "month": "06", "day": "15"}
        _create_partition_files(lake_dir, "practice_log", partitions, file_count=2, rows_per_file=3)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)

        # All required fields present
        assert "status" in resp
        assert "partitions_scanned" in resp
        assert "partitions_compacted" in resp
        assert "files_merged" in resp
        assert "files_removed" in resp
        assert "total_rows_before" in resp
        assert "total_rows_after" in resp
        assert "dry_run" in resp
        assert "duration_ms" in resp

        # Type checks
        assert resp["status"] in ("ok", "error", "noop")
        assert isinstance(resp["partitions_scanned"], int)
        assert isinstance(resp["partitions_compacted"], int)
        assert isinstance(resp["files_merged"], int)
        assert isinstance(resp["files_removed"], int)
        assert isinstance(resp["total_rows_before"], int)
        assert isinstance(resp["total_rows_after"], int)
        assert isinstance(resp["dry_run"], bool)
        assert isinstance(resp["duration_ms"], int)

    def test_noop_response_schema(self, cfg, lake_dir):
        """Noop response still has all fields."""
        partitions = {"year": "2025", "month": "06", "day": "15"}
        _create_partition_files(lake_dir, "practice_log", partitions, file_count=1, rows_per_file=3)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["status"] == "noop"
        assert resp["dry_run"] is False
        assert resp["duration_ms"] >= 0


class TestCompactMultiplePartitions:
    """Multiple partitions — some need compaction, some don't."""

    def test_mixed_partitions(self, cfg, lake_dir):
        """Two partitions: one with small files (compacted), one with single file (skipped)."""
        # Partition 1: 3 small files → should be compacted
        p1 = {"year": "2025", "month": "06", "day": "15"}
        _create_partition_files(lake_dir, "practice_log", p1, file_count=3, rows_per_file=5)

        # Partition 2: 1 file → should be skipped
        p2 = {"year": "2025", "month": "06", "day": "16"}
        _create_partition_files(lake_dir, "practice_log", p2, file_count=1, rows_per_file=10)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)

        assert resp["status"] == "ok"
        assert resp["partitions_scanned"] == 2
        assert resp["partitions_compacted"] == 1
        assert resp["files_merged"] == 3
        assert resp["files_removed"] == 3
        assert resp["total_rows_before"] == 15
        assert resp["total_rows_after"] == 15

        # Verify partition 1 has single merged file
        p1_dir = lake_dir / "practice_log" / "year=2025" / "month=06" / "day=15"
        assert len(list(p1_dir.glob("*.parquet"))) == 1

        # Verify partition 2 is unchanged
        p2_dir = lake_dir / "practice_log" / "year=2025" / "month=06" / "day=16"
        assert len(list(p2_dir.glob("*.parquet"))) == 1

    def test_multiple_partitions_all_compacted(self, cfg, lake_dir):
        """Two partitions both with small files → both compacted."""
        p1 = {"year": "2025", "month": "07", "day": "01"}
        _create_partition_files(lake_dir, "practice_log", p1, file_count=2, rows_per_file=4)

        p2 = {"year": "2025", "month": "07", "day": "02"}
        _create_partition_files(lake_dir, "practice_log", p2, file_count=3, rows_per_file=6)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)

        assert resp["status"] == "ok"
        assert resp["partitions_scanned"] == 2
        assert resp["partitions_compacted"] == 2
        assert resp["files_merged"] == 5  # 2 + 3
        assert resp["total_rows_before"] == 26  # 8 + 18
        assert resp["total_rows_after"] == 26


class TestCompactErrors:
    """Error handling for missing dataset directory and edge cases."""

    def test_missing_dataset_directory(self, cfg):
        """Non-existent dataset directory returns error JSON."""
        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "nonexistent_dataset",
            "--threshold-mb", "100",
        ])

        assert result.exit_code != 0
        resp = json.loads(result.output)
        assert resp["status"] == "error"
        assert "does not exist" in resp["error"]

    def test_empty_dataset_directory(self, cfg, lake_dir):
        """Dataset directory exists but has no partitions → noop."""
        (lake_dir / "practice_log").mkdir(parents=True, exist_ok=True)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["status"] == "noop"
        assert resp["partitions_scanned"] == 0


class TestCompactPartitionOption:
    """--partition option restricts compaction to a specific path."""

    def test_partition_option_limits_scope(self, cfg, lake_dir):
        """Only the specified partition is processed when --partition is used."""
        # Create two partitions
        p1 = {"year": "2025", "month": "06", "day": "15"}
        _create_partition_files(lake_dir, "practice_log", p1, file_count=3, rows_per_file=5)

        p2 = {"year": "2025", "month": "06", "day": "16"}
        _create_partition_files(lake_dir, "practice_log", p2, file_count=2, rows_per_file=5)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--partition", "year=2025/month=06/day=15",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)

        assert resp["partitions_scanned"] == 1
        assert resp["partitions_compacted"] == 1
        assert resp["files_merged"] == 3

        # Partition 1 should be compacted
        p1_dir = lake_dir / "practice_log" / "year=2025" / "month=06" / "day=15"
        assert len(list(p1_dir.glob("*.parquet"))) == 1

        # Partition 2 should be untouched
        p2_dir = lake_dir / "practice_log" / "year=2025" / "month=06" / "day=16"
        assert len(list(p2_dir.glob("*.parquet"))) == 2

    def test_partition_option_nonexistent(self, cfg, lake_dir):
        """--partition pointing to non-existent path → noop."""
        (lake_dir / "practice_log").mkdir(parents=True, exist_ok=True)

        runner = CliRunner()
        result = _invoke_compact(runner, cfg, [
            "--dataset", "practice_log",
            "--partition", "year=9999/month=01/day=01",
            "--threshold-mb", "100",
        ])

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["status"] == "noop"
        assert resp["partitions_scanned"] == 0
