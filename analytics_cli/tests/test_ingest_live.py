"""Tests for the ingest-live CLI command (T011 — US4).

Covers:
- Staging table creation and atomic swap to practice_log_live
- Row count verification
- JSON response schema per cli-contract.json ingest-live schema
- Error handling for missing/empty batch-dir
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from click.testing import CliRunner

from analytics_cli.__main__ import cli
from analytics_cli.tests.conftest import write_practice_log_parquet


def _invoke(runner, cfg, args):
    """Invoke CLI with standard config flags + extra args."""
    return runner.invoke(
        cli,
        [
            "--duckdb-path", cfg.duckdb_path,
            "--lake-path", cfg.lake_path,
            "--dimensions-path", cfg.dimensions_path,
            "--manifests-path", cfg.manifests_path,
            *args,
        ],
    )


# ---------------------------------------------------------------------------
# Basic functionality
# ---------------------------------------------------------------------------


class TestIngestLiveBasic:
    """Verify Parquet files are loaded into practice_log_live via atomic swap."""

    def test_loads_parquet_into_live_table(self, cfg, tmp_path):
        """ingest-live loads Parquet data into practice_log_live table."""
        batch = tmp_path / "batch"
        batch.mkdir()
        write_practice_log_parquet(batch / "part-0000.parquet", n=5)

        result = _invoke(CliRunner(), cfg, ["ingest-live", "--batch-dir", str(batch)])
        assert result.exit_code == 0

        conn = duckdb.connect(cfg.duckdb_path)
        count = conn.execute("SELECT COUNT(*) FROM practice_log_live").fetchone()[0]
        conn.close()
        assert count == 5

    def test_atomic_swap_replaces_existing(self, cfg, tmp_path):
        """Running ingest-live twice replaces the table contents, not appends."""
        runner = CliRunner()
        batch1 = tmp_path / "batch1"
        batch1.mkdir()
        write_practice_log_parquet(batch1 / "part-0000.parquet", n=3)

        batch2 = tmp_path / "batch2"
        batch2.mkdir()
        write_practice_log_parquet(batch2 / "part-0000.parquet", n=7)

        _invoke(runner, cfg, ["ingest-live", "--batch-dir", str(batch1)])
        _invoke(runner, cfg, ["ingest-live", "--batch-dir", str(batch2)])

        conn = duckdb.connect(cfg.duckdb_path)
        count = conn.execute("SELECT COUNT(*) FROM practice_log_live").fetchone()[0]
        conn.close()
        assert count == 7  # Replaced, not 3 + 7

    def test_multi_file_batch(self, cfg, tmp_path):
        """Batch directory with multiple Parquet files loads all rows."""
        batch = tmp_path / "batch"
        batch.mkdir()
        write_practice_log_parquet(batch / "part-0001.parquet", n=3)
        write_practice_log_parquet(batch / "part-0002.parquet", n=4)

        result = _invoke(CliRunner(), cfg, ["ingest-live", "--batch-dir", str(batch)])
        assert result.exit_code == 0

        conn = duckdb.connect(cfg.duckdb_path)
        count = conn.execute("SELECT COUNT(*) FROM practice_log_live").fetchone()[0]
        conn.close()
        assert count == 7


# ---------------------------------------------------------------------------
# Row count verification
# ---------------------------------------------------------------------------


class TestIngestLiveRowCount:
    """Verify row count matches source Parquet files."""

    def test_row_count_in_response_matches_table(self, cfg, tmp_path):
        batch = tmp_path / "batch"
        batch.mkdir()
        write_practice_log_parquet(batch / "part-0000.parquet", n=12)

        result = _invoke(CliRunner(), cfg, ["ingest-live", "--batch-dir", str(batch)])
        data = json.loads(result.output)
        total_rows = sum(b["rows"] for b in data["batches"])

        conn = duckdb.connect(cfg.duckdb_path)
        db_count = conn.execute("SELECT COUNT(*) FROM practice_log_live").fetchone()[0]
        conn.close()

        assert total_rows == db_count == 12


# ---------------------------------------------------------------------------
# JSON response schema
# ---------------------------------------------------------------------------


class TestIngestLiveJsonSchema:
    """Verify JSON response matches cli-contract.json ingest-live schema."""

    def test_success_response_has_required_fields(self, cfg, tmp_path):
        batch = tmp_path / "batch"
        batch.mkdir()
        write_practice_log_parquet(batch / "part-0000.parquet", n=5)

        result = _invoke(CliRunner(), cfg, ["ingest-live", "--batch-dir", str(batch)])
        data = json.loads(result.output)

        # Required fields per contract
        assert data["status"] == "ok"
        assert "batches_ok" in data
        assert "batches_error" in data
        assert isinstance(data["batches_ok"], int)
        assert isinstance(data["batches_error"], int)
        assert isinstance(data.get("duration_ms"), int)

    def test_batches_array_has_file_rows_status(self, cfg, tmp_path):
        batch = tmp_path / "batch"
        batch.mkdir()
        write_practice_log_parquet(batch / "part-0000.parquet", n=5)

        result = _invoke(CliRunner(), cfg, ["ingest-live", "--batch-dir", str(batch)])
        data = json.loads(result.output)

        assert "batches" in data
        assert len(data["batches"]) >= 1
        entry = data["batches"][0]
        assert "file" in entry
        assert "rows" in entry
        assert "status" in entry
        assert entry["status"] == "ok"
        assert entry["rows"] == 5


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestIngestLiveErrors:
    """Error handling for ingest-live command."""

    def test_error_on_missing_batch_dir(self, cfg):
        result = _invoke(
            CliRunner(), cfg, ["ingest-live", "--batch-dir", "/nonexistent/dir"]
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "error" in data

    def test_error_on_empty_batch_dir(self, cfg, tmp_path):
        batch = tmp_path / "batch"
        batch.mkdir()
        # No Parquet files

        result = _invoke(CliRunner(), cfg, ["ingest-live", "--batch-dir", str(batch)])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
