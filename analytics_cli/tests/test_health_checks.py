"""Tests for data lake health checks (US7 — T024).

Covers:
- Duplicate detection (T025)
- Checksum verification (T026)
- Dimension coverage (T027)
- Partition size analysis (T028)
- Verify command integration (T029)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from analytics_cli.__main__ import cli
from analytics_cli.health.checksum_check import check_checksums
from analytics_cli.health.dimension_coverage import check_dimension_coverage
from analytics_cli.health.duplicate_check import check_duplicates
from analytics_cli.health.partition_analysis import check_partition_sizes
from analytics_cli.views.semantic import (
    create_archive_views,
    create_combined_views,
    create_dimension_views,
    ensure_live_tables,
)
from analytics_cli.tests.conftest import (
    build_hive_dir,
    write_dimension_parquet,
    write_practice_log_parquet,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_combined_view_with_data(
    conn: duckdb.DuckDBPyConnection,
    rows: list[dict],
) -> None:
    """Create practice_log_live + practice_log_combined with given rows."""
    ensure_live_tables(conn)
    for r in rows:
        conn.execute(
            "INSERT INTO practice_log_live "
            "(player_id, item_id, first_seen_at, last_seen_at, "
            " last_result, attempt_count, correct_count, season_id, plan_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                r["player_id"], r["item_id"],
                r["first_seen_at"], r["last_seen_at"],
                r["last_result"], r["attempt_count"], r["correct_count"],
                r["season_id"], r["plan_id"],
            ],
        )
    conn.execute("""\
CREATE OR REPLACE VIEW practice_log_combined AS
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'live' AS source
FROM practice_log_live""")


def _make_row(
    player_id: str = "PLR-0001",
    item_id: str = "ITEM-0001",
    last_seen_at: datetime | None = None,
) -> dict:
    """Build a single practice_log row dict."""
    return {
        "player_id": player_id,
        "item_id": item_id,
        "first_seen_at": datetime(2025, 6, 1, 10, 0, 0),
        "last_seen_at": last_seen_at or datetime(2025, 6, 15, 14, 30, 0),
        "last_result": "correct",
        "attempt_count": 1,
        "correct_count": 1,
        "season_id": "S1",
        "plan_id": "PL1",
    }


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _invoke(runner, cfg, args):
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


# ===================================================================
# T025 — Duplicate Detection
# ===================================================================


class TestDuplicateCheck:
    """Verify check_duplicates detects duplicate rows."""

    def test_known_duplicate_found(self, db):
        """Duplicate (player_id, item_id, last_seen_at) → fail."""
        ts = datetime(2025, 6, 15, 14, 30, 0)
        rows = [
            _make_row("PLR-A", "ITEM-X", ts),
            _make_row("PLR-A", "ITEM-X", ts),  # duplicate
            _make_row("PLR-B", "ITEM-Y", ts),
        ]
        _setup_combined_view_with_data(db, rows)

        result = check_duplicates(db)

        assert result["status"] == "fail"
        assert result["duplicate_count"] > 0
        assert len(result["sample_rows"]) > 0
        # The duplicate group should show count >= 2
        dup = result["sample_rows"][0]
        assert dup["player_id"] == "PLR-A"
        assert dup["item_id"] == "ITEM-X"
        assert dup["count"] >= 2

    def test_no_duplicates(self, db):
        """All unique rows → pass."""
        rows = [
            _make_row("PLR-A", "ITEM-X", datetime(2025, 6, 15, 10, 0, 0)),
            _make_row("PLR-B", "ITEM-Y", datetime(2025, 6, 15, 11, 0, 0)),
            _make_row("PLR-C", "ITEM-Z", datetime(2025, 6, 15, 12, 0, 0)),
        ]
        _setup_combined_view_with_data(db, rows)

        result = check_duplicates(db)

        assert result["status"] == "pass"
        assert result["duplicate_count"] == 0
        assert result["sample_rows"] == []

    def test_empty_table(self, db):
        """Empty practice_log_combined → pass."""
        _setup_combined_view_with_data(db, [])

        result = check_duplicates(db)

        assert result["status"] == "pass"
        assert result["duplicate_count"] == 0

    def test_view_does_not_exist(self, db):
        """Missing practice_log_combined view → pass (graceful skip)."""
        result = check_duplicates(db)

        assert result["status"] == "pass"
        assert result["duplicate_count"] == 0
        assert result["sample_rows"] == []


# ===================================================================
# T026 — Checksum Verification
# ===================================================================


class TestChecksumCheck:
    """Verify check_checksums validates SHA-256 of Parquet files."""

    def test_matching_sha256(self, tmp_path):
        """All checksums match → pass, files_checked > 0."""
        # Create a Parquet file
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pq_path = data_dir / "part-0000.parquet"
        write_practice_log_parquet(pq_path, n=3)

        # Compute its real SHA-256
        real_sha = _sha256_of_file(pq_path)

        # Write manifest
        manifests = tmp_path / "manifests"
        archive_dir = manifests / "archive"
        archive_dir.mkdir(parents=True)
        manifest = {
            "files": [
                {"path": str(pq_path), "sha256": real_sha, "rows": 3},
            ]
        }
        (archive_dir / "batch_001.json").write_text(json.dumps(manifest))

        result = check_checksums(str(manifests))

        assert result["status"] == "pass"
        assert result["files_checked"] == 1
        assert result["mismatches"] == []

    def test_mismatching_sha256(self, tmp_path):
        """Wrong SHA-256 → fail, mismatches populated."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pq_path = data_dir / "part-0000.parquet"
        write_practice_log_parquet(pq_path, n=3)

        # Write manifest with incorrect SHA
        manifests = tmp_path / "manifests"
        archive_dir = manifests / "archive"
        archive_dir.mkdir(parents=True)
        manifest = {
            "files": [
                {"path": str(pq_path), "sha256": "deadbeef" * 8, "rows": 3},
            ]
        }
        (archive_dir / "batch_001.json").write_text(json.dumps(manifest))

        result = check_checksums(str(manifests))

        assert result["status"] == "fail"
        assert result["files_checked"] == 1
        assert len(result["mismatches"]) == 1
        mismatch = result["mismatches"][0]
        assert mismatch["file"] == str(pq_path)
        assert mismatch["expected"] == "deadbeef" * 8
        assert mismatch["actual"] != "deadbeef" * 8

    def test_no_manifests(self, tmp_path):
        """No manifest files → pass, files_checked=0."""
        manifests = tmp_path / "manifests"
        (manifests / "archive").mkdir(parents=True)

        result = check_checksums(str(manifests))

        assert result["status"] == "pass"
        assert result["files_checked"] == 0
        assert result["mismatches"] == []

    def test_no_archive_dir(self, tmp_path):
        """manifests/archive directory missing → pass."""
        manifests = tmp_path / "manifests"
        manifests.mkdir()

        result = check_checksums(str(manifests))

        assert result["status"] == "pass"
        assert result["files_checked"] == 0


# ===================================================================
# T027 — Dimension Coverage
# ===================================================================


class TestDimensionCoverage:
    """Verify check_dimension_coverage detects missing player dimensions."""

    def test_missing_player(self, db):
        """Player in practice_log but not in dim_player_history → fail."""
        # Set up practice_log_combined with a player
        rows = [_make_row("PLR-ORPHAN", "ITEM-001")]
        _setup_combined_view_with_data(db, rows)

        # Create an empty dim_player_history
        db.execute(
            "CREATE TABLE dim_player_history (player_id VARCHAR, name VARCHAR)"
        )

        result = check_dimension_coverage(db)

        assert result["status"] == "fail"
        assert result["missing_players"] > 0
        assert "PLR-ORPHAN" in result["sample_ids"]

    def test_all_players_covered(self, db):
        """All players have dimension rows → pass."""
        rows = [
            _make_row("PLR-A", "ITEM-001"),
            _make_row("PLR-B", "ITEM-002"),
        ]
        _setup_combined_view_with_data(db, rows)

        # Create dim_player_history with matching players
        db.execute(
            "CREATE TABLE dim_player_history "
            "(player_id VARCHAR, name VARCHAR)"
        )
        db.execute(
            "INSERT INTO dim_player_history VALUES ('PLR-A', 'Alice'), ('PLR-B', 'Bob')"
        )

        result = check_dimension_coverage(db)

        assert result["status"] == "pass"
        assert result["missing_players"] == 0
        assert result["sample_ids"] == []

    def test_empty_data(self, db):
        """Empty practice_log_combined → pass."""
        _setup_combined_view_with_data(db, [])

        db.execute(
            "CREATE TABLE dim_player_history (player_id VARCHAR, name VARCHAR)"
        )

        result = check_dimension_coverage(db)

        assert result["status"] == "pass"
        assert result["missing_players"] == 0

    def test_missing_views(self, db):
        """Neither view exists → pass (graceful skip)."""
        result = check_dimension_coverage(db)

        assert result["status"] == "pass"
        assert result["missing_players"] == 0
        assert result["sample_ids"] == []


# ===================================================================
# T028 — Partition Size Analysis
# ===================================================================


class TestPartitionSizes:
    """Verify check_partition_sizes detects undersized partitions."""

    def test_undersized_partition(self, lake_dir):
        """Small Parquet file (< threshold) → warning."""
        # Write a tiny Parquet file (well under 64 MB)
        dest = build_hive_dir(
            lake_dir, "practice_log",
            {"year": "2025", "month": "06", "day": "15"},
        )
        write_practice_log_parquet(dest, n=2)

        result = check_partition_sizes(str(lake_dir), threshold_mb=64)

        assert result["status"] == "warning"
        assert result["total_partitions"] >= 1
        assert result["undersized_partitions"] > 0
        assert len(result["details"]) > 0
        detail = result["details"][0]
        assert "partition" in detail
        assert "file" in detail
        assert "size_mb" in detail
        assert detail["size_mb"] < 64

    def test_all_partitions_ok(self, lake_dir):
        """Files above threshold → pass."""
        dest = build_hive_dir(
            lake_dir, "practice_log",
            {"year": "2025", "month": "06", "day": "15"},
        )
        write_practice_log_parquet(dest, n=2)

        # Use a very small threshold so the file is considered OK
        result = check_partition_sizes(str(lake_dir), threshold_mb=0)

        assert result["status"] == "pass"
        assert result["total_partitions"] >= 1
        assert result["undersized_partitions"] == 0
        assert result["details"] == []

    def test_empty_lake(self, lake_dir):
        """No Parquet files → pass."""
        result = check_partition_sizes(str(lake_dir))

        assert result["status"] == "pass"
        assert result["total_partitions"] == 0
        assert result["undersized_partitions"] == 0
        assert result["details"] == []

    def test_nonexistent_lake_path(self, tmp_path):
        """Lake path that doesn't exist → pass."""
        result = check_partition_sizes(str(tmp_path / "nonexistent"))

        assert result["status"] == "pass"
        assert result["total_partitions"] == 0


# ===================================================================
# T029 — Verify Command Integration
# ===================================================================


class TestVerifyCommand:
    """Verify the CLI verify command orchestrates all health checks."""

    def test_verify_json_structure(self, cfg, lake_dir, dimensions_dir):
        """Verify command returns well-formed JSON with expected keys."""
        # Set up minimal data so views can be created
        conn = duckdb.connect(cfg.duckdb_path)
        ensure_live_tables(conn)
        conn.execute("""\
CREATE OR REPLACE VIEW practice_log_combined AS
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'live' AS source
FROM practice_log_live""")
        conn.close()

        runner = CliRunner()
        result = _invoke(runner, cfg, ["verify"])

        assert result.exit_code == 0, f"stderr: {result.output}"
        data = json.loads(result.output)

        assert "status" in data
        assert data["status"] in ("ok", "warning", "error")
        assert "checks" in data
        assert isinstance(data["checks"], dict)
        assert "duration_ms" in data
        assert isinstance(data["duration_ms"], int)

        # All four checks should be present
        assert "duplicates" in data["checks"]
        assert "checksums" in data["checks"]
        assert "dimension_coverage" in data["checks"]
        assert "partition_sizes" in data["checks"]

    def test_verify_all_pass(self, cfg, lake_dir, dimensions_dir):
        """When no issues found, overall status is 'ok'."""
        conn = duckdb.connect(cfg.duckdb_path)
        ensure_live_tables(conn)
        conn.execute("""\
CREATE OR REPLACE VIEW practice_log_combined AS
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'live' AS source
FROM practice_log_live""")
        conn.close()

        runner = CliRunner()
        result = _invoke(runner, cfg, ["verify"])

        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_verify_reports_warning_for_undersized(
        self, cfg, lake_dir, dimensions_dir
    ):
        """Undersized partition → overall status 'warning'."""
        # Write a tiny Parquet file in the lake
        dest = build_hive_dir(
            lake_dir, "practice_log",
            {"year": "2025", "month": "06", "day": "15"},
        )
        write_practice_log_parquet(dest, n=2)

        conn = duckdb.connect(cfg.duckdb_path)
        ensure_live_tables(conn)
        create_archive_views(conn, str(lake_dir))
        create_combined_views(conn)
        conn.close()

        runner = CliRunner()
        result = _invoke(runner, cfg, ["verify"])

        data = json.loads(result.output)
        # Partition is undersized → at least "warning"
        assert data["status"] in ("warning", "error")
        assert data["checks"]["partition_sizes"]["status"] == "warning"

    def test_verify_reports_error_for_duplicates(
        self, cfg, lake_dir, dimensions_dir
    ):
        """Duplicate rows → overall status 'error'."""
        ts = datetime(2025, 6, 15, 14, 30, 0)
        conn = duckdb.connect(cfg.duckdb_path)
        ensure_live_tables(conn)
        # Insert duplicate rows
        for _ in range(2):
            conn.execute(
                "INSERT INTO practice_log_live "
                "(player_id, item_id, first_seen_at, last_seen_at, "
                " last_result, attempt_count, correct_count, season_id, plan_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ["PLR-DUP", "ITEM-DUP", ts, ts, "correct", 1, 1, "S1", "PL1"],
            )
        conn.execute("""\
CREATE OR REPLACE VIEW practice_log_combined AS
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'live' AS source
FROM practice_log_live""")
        conn.close()

        runner = CliRunner()
        result = _invoke(runner, cfg, ["verify"])

        data = json.loads(result.output)
        assert data["status"] == "error"
        assert data["checks"]["duplicates"]["status"] == "fail"
