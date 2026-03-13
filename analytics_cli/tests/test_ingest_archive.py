"""Tests for ingest-archive CLI command (T007).

Covers:
- Parquet file copy to correct Hive partition path
- Manifest.json storage in manifests/archive/
- View refresh after ingest
- JSON response schema per cli-contract.json
- Error handling for missing batch-dir
- Dimension file handling
- Memory state season partitioning
- Multi-date practice log splitting
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from analytics_cli.__main__ import cli
from analytics_cli.tests.conftest import (
    write_interaction_log_parquet,
    write_memory_state_parquet,
    write_practice_log_parquet,
    write_structure_progress_parquet,
    write_task_run_log_parquet,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_manifest(batch_dir: Path, batch_id: str, files: list[dict]) -> Path:
    """Write a manifest.json into *batch_dir* and return its path."""
    manifest = {
        "manifest_version": "1.0",
        "dataset_key": "test_archive",
        "kind": "archive",
        "batch_id": batch_id,
        "schema_version": "1.0",
        "created_at": "2026-03-12T14:00:00Z",
        "source": "memora_admin",
        "files": files,
    }
    p = batch_dir / "manifest.json"
    p.write_text(json.dumps(manifest, indent=2))
    return p


def _invoke(runner, cfg, extra_args: list[str] | None = None):
    """Invoke the CLI with the cfg paths and optional extra args."""
    args = [
        "--duckdb-path", cfg.duckdb_path,
        "--lake-path", cfg.lake_path,
        "--dimensions-path", cfg.dimensions_path,
        "--manifests-path", cfg.manifests_path,
    ]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(cli, args, catch_exceptions=False)


def _invoke_ingest(runner, cfg, batch_dir: str):
    """Invoke ingest-archive with the given batch dir."""
    return _invoke(runner, cfg, ["ingest-archive", "--batch-dir", batch_dir])


# ── Tests ────────────────────────────────────────────────────────────────────


class TestIngestArchiveBasic:
    def test_practice_log_hive_partition(self, cfg, tmp_path):
        """Practice log placed in year/month/day Hive partition."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        # Create parquet with known dates
        rows = [
            {
                "player_id": "PLR-001",
                "item_id": "ITEM-001",
                "first_seen_at": datetime(2025, 6, 15, 10, 0, 0),
                "last_seen_at": datetime(2025, 6, 15, 14, 0, 0),
                "last_result": "correct",
                "attempt_count": 3,
                "correct_count": 2,
                "season_id": "S1",
                "plan_id": "P1",
            },
        ]
        write_practice_log_parquet(batch_dir / "fact_practice_log.parquet", rows=rows)
        _make_manifest(batch_dir, "ARCH-T001", [
            {"role": "fact", "entity": "practice_log", "filename": "fact_practice_log.parquet", "row_count": 1},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["status"] == "ok"
        assert resp["batches_ok"] == 1
        assert resp["batches_error"] == 0

        # Verify file in Hive partition
        expected = Path(cfg.lake_path) / "practice_log" / "year=2025" / "month=06" / "day=15" / "part-ARCH-T001.parquet"
        assert expected.exists()

    def test_multiple_dates_split(self, cfg, tmp_path):
        """Practice log spanning 2 days splits into 2 partitions."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        rows = [
            {
                "player_id": "PLR-001", "item_id": "ITEM-001",
                "first_seen_at": datetime(2025, 6, 1, 10, 0, 0),
                "last_seen_at": datetime(2025, 6, 1, 14, 0, 0),
                "last_result": "correct", "attempt_count": 1, "correct_count": 1,
                "season_id": "S1", "plan_id": "P1",
            },
            {
                "player_id": "PLR-002", "item_id": "ITEM-002",
                "first_seen_at": datetime(2025, 6, 2, 10, 0, 0),
                "last_seen_at": datetime(2025, 6, 2, 14, 0, 0),
                "last_result": "wrong", "attempt_count": 2, "correct_count": 0,
                "season_id": "S1", "plan_id": "P1",
            },
        ]
        write_practice_log_parquet(batch_dir / "fact_practice_log.parquet", rows=rows)
        _make_manifest(batch_dir, "ARCH-T002", [
            {"role": "fact", "entity": "practice_log", "filename": "fact_practice_log.parquet", "row_count": 2},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["batches"][0]["rows"] == 2

        lake = Path(cfg.lake_path)
        assert (lake / "practice_log" / "year=2025" / "month=06" / "day=01" / "part-ARCH-T002.parquet").exists()
        assert (lake / "practice_log" / "year=2025" / "month=06" / "day=02" / "part-ARCH-T002.parquet").exists()


class TestIngestMemoryState:
    def test_memory_state_season_partition(self, cfg, tmp_path):
        """Memory state partitioned by season_seq, column removed from file."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        write_memory_state_parquet(batch_dir / "fact_memory_state.parquet", n=3, season_seq=5)
        _make_manifest(batch_dir, "ARCH-T003", [
            {"role": "fact", "entity": "memory_state", "filename": "fact_memory_state.parquet", "row_count": 3},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["batches_ok"] == 1

        # Verify partition directory
        part_dir = Path(cfg.lake_path) / "memory_state" / "season_seq=5"
        assert part_dir.exists()

        # season_seq should NOT be in the Parquet file (it's in the path)
        file_schema = pq.read_schema(str(part_dir / "part-ARCH-T003.parquet"))
        assert "season_seq" not in file_schema.names


class TestIngestStructureProgress:
    def test_structure_progress_snapshot_partition(self, cfg, tmp_path):
        """Structure progress partitioned by snapshot_date."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        write_structure_progress_parquet(
            batch_dir / "fact_structure_progress.parquet",
            n=3, snapshot_date=date(2026, 3, 10),
        )
        _make_manifest(batch_dir, "ARCH-T004", [
            {"role": "fact", "entity": "structure_progress", "filename": "fact_structure_progress.parquet", "row_count": 3},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        part_dir = Path(cfg.lake_path) / "structure_progress" / "snapshot_date=2026-03-10"
        assert part_dir.exists()

        # snapshot_date removed from file
        file_schema = pq.read_schema(str(part_dir / "part-ARCH-T004.parquet"))
        assert "snapshot_date" not in file_schema.names


class TestIngestDimension:
    def test_dimension_file_copied(self, cfg, tmp_path):
        """Dimension files copied to dimensions directory."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        # Create a dimension file
        dim_schema = pa.schema([("player_id", pa.string()), ("name", pa.string())])
        table = pa.Table.from_pylist(
            [{"player_id": "PLR-001", "name": "Alice"}], schema=dim_schema,
        )
        pq.write_table(table, str(batch_dir / "dim_player.parquet"))

        _make_manifest(batch_dir, "ARCH-T005", [
            {"role": "dimension", "entity": "player", "filename": "dim_player.parquet", "row_count": 1},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["batches_ok"] == 1

        # Verify dimension file in correct location
        dest = Path(cfg.dimensions_path) / "dim_player.parquet"
        assert dest.exists()
        copied = pq.read_table(str(dest))
        assert copied.num_rows == 1


class TestIngestManifest:
    def test_manifest_stored(self, cfg, tmp_path):
        """Manifest copied to manifests/archive/{batch_id}.json."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        write_practice_log_parquet(batch_dir / "fact_practice_log.parquet", n=1)
        _make_manifest(batch_dir, "ARCH-T006", [
            {"role": "fact", "entity": "practice_log", "filename": "fact_practice_log.parquet", "row_count": 1},
        ])

        runner = CliRunner()
        _invoke_ingest(runner, cfg, str(batch_dir))

        stored = Path(cfg.manifests_path) / "archive" / "ARCH-T006.json"
        assert stored.exists()
        data = json.loads(stored.read_text())
        assert data["batch_id"] == "ARCH-T006"


class TestIngestViewRefresh:
    def test_views_refreshed_after_ingest(self, cfg, tmp_path):
        """Ingest creates DuckDB views that can query the ingested data."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        rows = [{
            "player_id": "PLR-001", "item_id": "ITEM-001",
            "first_seen_at": datetime(2025, 6, 15, 10, 0, 0),
            "last_seen_at": datetime(2025, 6, 15, 14, 0, 0),
            "last_result": "correct", "attempt_count": 1, "correct_count": 1,
            "season_id": "S1", "plan_id": "P1",
        }]
        write_practice_log_parquet(batch_dir / "fact_practice_log.parquet", rows=rows)
        _make_manifest(batch_dir, "ARCH-T007", [
            {"role": "fact", "entity": "practice_log", "filename": "fact_practice_log.parquet", "row_count": 1},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        resp = json.loads(result.output)
        assert "practice_log_archive" in resp["views_refreshed"]

        # Query the DuckDB file directly
        conn = duckdb.connect(cfg.duckdb_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM practice_log_archive").fetchone()[0]
            assert count == 1
        finally:
            conn.close()


class TestIngestJsonSchema:
    def test_response_has_required_fields(self, cfg, tmp_path):
        """Response matches cli-contract.json required fields."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        write_practice_log_parquet(batch_dir / "fact_practice_log.parquet", n=2)
        _make_manifest(batch_dir, "ARCH-T008", [
            {"role": "fact", "entity": "practice_log", "filename": "fact_practice_log.parquet", "row_count": 2},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        resp = json.loads(result.output)
        # Required fields per contract
        assert "status" in resp
        assert "batches_ok" in resp
        assert "batches_error" in resp
        assert resp["status"] in ("ok", "error")
        assert isinstance(resp["batches_ok"], int)
        assert isinstance(resp["batches_error"], int)

        # Optional fields present
        assert "batches" in resp
        assert "views_refreshed" in resp
        assert "duration_ms" in resp
        assert isinstance(resp["duration_ms"], int)

    def test_batch_entry_fields(self, cfg, tmp_path):
        """Each batch entry has file, rows, status, destination."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        write_practice_log_parquet(batch_dir / "fact_practice_log.parquet", n=3)
        _make_manifest(batch_dir, "ARCH-T009", [
            {"role": "fact", "entity": "practice_log", "filename": "fact_practice_log.parquet", "row_count": 3},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        resp = json.loads(result.output)
        batch = resp["batches"][0]
        assert batch["file"] == "fact_practice_log.parquet"
        assert batch["rows"] == 3
        assert batch["status"] == "ok"
        assert "destination" in batch


class TestIngestErrors:
    def test_missing_batch_dir(self, cfg):
        """Non-existent batch-dir returns error JSON."""
        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, "/nonexistent/batch/dir")

        assert result.exit_code != 0
        resp = json.loads(result.output)
        assert resp["status"] == "error"
        assert "does not exist" in resp["error"]

    def test_missing_manifest(self, cfg, tmp_path):
        """Batch dir without manifest.json returns error."""
        batch_dir = tmp_path / "batch_no_manifest"
        batch_dir.mkdir()

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code != 0
        resp = json.loads(result.output)
        assert resp["status"] == "error"
        assert "manifest.json" in resp["error"]

    def test_missing_parquet_file(self, cfg, tmp_path):
        """Manifest references a file that doesn't exist."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        _make_manifest(batch_dir, "ARCH-T010", [
            {"role": "fact", "entity": "practice_log", "filename": "missing.parquet", "row_count": 0},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code != 0
        resp = json.loads(result.output)
        assert resp["status"] == "error"
        assert resp["batches_error"] == 1
        assert resp["batches"][0]["status"] == "error"


class TestIngestStructureProgressSnapshots:
    """T018 — Snapshot-specific ingest validation for structure_progress.

    Verifies:
    - Multiple snapshot dates produce separate snapshot_date=YYYY-MM-DD/ partitions
    - structure_progress_snapshots DuckDB view reads them with partition pruning
    - Trend aggregation queries (AVG completion by snapshot_date) return correct results
    - snapshot_date column is removed from Parquet files (encoded in directory name)
    """

    def test_multiple_snapshot_dates_separate_partitions(self, cfg, tmp_path):
        """Batch with two snapshot dates creates two partition directories."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        rows = [
            {
                "snapshot_date": date(2026, 3, 10),
                "player_id": "PLR-001",
                "plan_id": "PLAN-001",
                "subject_id": "SUBJ-01",
                "completion_percentage": 60.0,
            },
            {
                "snapshot_date": date(2026, 3, 10),
                "player_id": "PLR-002",
                "plan_id": "PLAN-001",
                "subject_id": "SUBJ-01",
                "completion_percentage": 80.0,
            },
            {
                "snapshot_date": date(2026, 3, 11),
                "player_id": "PLR-001",
                "plan_id": "PLAN-001",
                "subject_id": "SUBJ-01",
                "completion_percentage": 65.0,
            },
        ]
        write_structure_progress_parquet(
            batch_dir / "fact_structure_progress.parquet", rows=rows,
        )
        _make_manifest(batch_dir, "ARCH-T018A", [
            {"role": "fact", "entity": "structure_progress", "filename": "fact_structure_progress.parquet", "row_count": 3},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["batches_ok"] == 1
        assert resp["batches"][0]["rows"] == 3

        lake = Path(cfg.lake_path)
        part_10 = lake / "structure_progress" / "snapshot_date=2026-03-10"
        part_11 = lake / "structure_progress" / "snapshot_date=2026-03-11"
        assert part_10.exists()
        assert part_11.exists()

        # Each partition has a parquet file
        assert (part_10 / "part-ARCH-T018A.parquet").exists()
        assert (part_11 / "part-ARCH-T018A.parquet").exists()

    def test_snapshot_date_removed_from_parquet_files(self, cfg, tmp_path):
        """snapshot_date column is NOT in the written Parquet files."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        write_structure_progress_parquet(
            batch_dir / "fact_structure_progress.parquet",
            n=2, snapshot_date=date(2026, 3, 10),
        )
        _make_manifest(batch_dir, "ARCH-T018B", [
            {"role": "fact", "entity": "structure_progress", "filename": "fact_structure_progress.parquet", "row_count": 2},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))
        assert result.exit_code == 0

        pq_file = Path(cfg.lake_path) / "structure_progress" / "snapshot_date=2026-03-10" / "part-ARCH-T018B.parquet"
        file_schema = pq.read_schema(str(pq_file))
        assert "snapshot_date" not in file_schema.names
        # Other columns are preserved
        assert "player_id" in file_schema.names
        assert "completion_percentage" in file_schema.names

    def test_view_partition_pruning_after_ingest(self, cfg, tmp_path):
        """After ingest, filtering by snapshot_date returns only that partition's rows."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        rows = [
            {
                "snapshot_date": date(2026, 3, 10),
                "player_id": "PLR-001",
                "plan_id": "PLAN-001",
                "subject_id": "SUBJ-01",
                "completion_percentage": 60.0,
            },
            {
                "snapshot_date": date(2026, 3, 10),
                "player_id": "PLR-002",
                "plan_id": "PLAN-001",
                "subject_id": "SUBJ-02",
                "completion_percentage": 80.0,
            },
            {
                "snapshot_date": date(2026, 3, 11),
                "player_id": "PLR-001",
                "plan_id": "PLAN-001",
                "subject_id": "SUBJ-01",
                "completion_percentage": 65.0,
            },
            {
                "snapshot_date": date(2026, 3, 11),
                "player_id": "PLR-003",
                "plan_id": "PLAN-001",
                "subject_id": "SUBJ-03",
                "completion_percentage": 90.0,
            },
        ]
        write_structure_progress_parquet(
            batch_dir / "fact_structure_progress.parquet", rows=rows,
        )
        _make_manifest(batch_dir, "ARCH-T018C", [
            {"role": "fact", "entity": "structure_progress", "filename": "fact_structure_progress.parquet", "row_count": 4},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))
        assert result.exit_code == 0

        resp = json.loads(result.output)
        assert "structure_progress_snapshots" in resp["views_refreshed"]

        # Query with partition pruning
        conn = duckdb.connect(cfg.duckdb_path)
        try:
            # Only snapshot_date=2026-03-10 rows
            count_10 = conn.execute(
                "SELECT COUNT(*) FROM structure_progress_snapshots "
                "WHERE snapshot_date = '2026-03-10'"
            ).fetchone()[0]
            assert count_10 == 2

            # Only snapshot_date=2026-03-11 rows
            count_11 = conn.execute(
                "SELECT COUNT(*) FROM structure_progress_snapshots "
                "WHERE snapshot_date = '2026-03-11'"
            ).fetchone()[0]
            assert count_11 == 2

            # Total across both partitions
            total = conn.execute(
                "SELECT COUNT(*) FROM structure_progress_snapshots"
            ).fetchone()[0]
            assert total == 4
        finally:
            conn.close()

    def test_snapshot_trend_query_avg_completion(self, cfg, tmp_path):
        """AVG(completion_percentage) GROUP BY snapshot_date returns correct trend."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        rows = [
            # Day 1: avg = (40 + 60) / 2 = 50.0
            {"snapshot_date": date(2026, 3, 10), "player_id": "PLR-001", "plan_id": "P1", "subject_id": "S1", "completion_percentage": 40.0},
            {"snapshot_date": date(2026, 3, 10), "player_id": "PLR-002", "plan_id": "P1", "subject_id": "S1", "completion_percentage": 60.0},
            # Day 2: avg = (70 + 90) / 2 = 80.0
            {"snapshot_date": date(2026, 3, 11), "player_id": "PLR-001", "plan_id": "P1", "subject_id": "S1", "completion_percentage": 70.0},
            {"snapshot_date": date(2026, 3, 11), "player_id": "PLR-002", "plan_id": "P1", "subject_id": "S1", "completion_percentage": 90.0},
        ]
        write_structure_progress_parquet(
            batch_dir / "fact_structure_progress.parquet", rows=rows,
        )
        _make_manifest(batch_dir, "ARCH-T018D", [
            {"role": "fact", "entity": "structure_progress", "filename": "fact_structure_progress.parquet", "row_count": 4},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))
        assert result.exit_code == 0

        conn = duckdb.connect(cfg.duckdb_path)
        try:
            trend = conn.execute(
                "SELECT snapshot_date, AVG(completion_percentage) AS avg_completion "
                "FROM structure_progress_snapshots "
                "GROUP BY snapshot_date "
                "ORDER BY snapshot_date"
            ).fetchall()

            assert len(trend) == 2
            assert str(trend[0][0]) == "2026-03-10"
            assert trend[0][1] == pytest.approx(50.0)
            assert str(trend[1][0]) == "2026-03-11"
            assert trend[1][1] == pytest.approx(80.0)
        finally:
            conn.close()

    def test_separate_batches_accumulate_in_same_partition(self, cfg, tmp_path):
        """Two separate ingests for the same snapshot_date accumulate in the partition."""
        # First batch
        batch1 = tmp_path / "batch1"
        batch1.mkdir()
        write_structure_progress_parquet(
            batch1 / "fact_structure_progress.parquet",
            n=2, snapshot_date=date(2026, 3, 10),
        )
        _make_manifest(batch1, "ARCH-T018E", [
            {"role": "fact", "entity": "structure_progress", "filename": "fact_structure_progress.parquet", "row_count": 2},
        ])

        runner = CliRunner()
        result1 = _invoke_ingest(runner, cfg, str(batch1))
        assert result1.exit_code == 0

        # Second batch — same snapshot_date, different job ID
        batch2 = tmp_path / "batch2"
        batch2.mkdir()
        write_structure_progress_parquet(
            batch2 / "fact_structure_progress.parquet",
            n=3, snapshot_date=date(2026, 3, 10),
        )
        _make_manifest(batch2, "ARCH-T018F", [
            {"role": "fact", "entity": "structure_progress", "filename": "fact_structure_progress.parquet", "row_count": 3},
        ])

        result2 = _invoke_ingest(runner, cfg, str(batch2))
        assert result2.exit_code == 0

        # Both files in the same partition directory
        part_dir = Path(cfg.lake_path) / "structure_progress" / "snapshot_date=2026-03-10"
        assert (part_dir / "part-ARCH-T018E.parquet").exists()
        assert (part_dir / "part-ARCH-T018F.parquet").exists()

        # View returns all 5 rows
        conn = duckdb.connect(cfg.duckdb_path)
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM structure_progress_snapshots "
                "WHERE snapshot_date = '2026-03-10'"
            ).fetchone()[0]
            assert total == 5
        finally:
            conn.close()


class TestIngestMultiFile:
    def test_fact_and_dimension_together(self, cfg, tmp_path):
        """Batch with both fact and dimension files."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        # Fact file
        write_practice_log_parquet(batch_dir / "fact_practice_log.parquet", n=2)

        # Dimension file
        dim_table = pa.Table.from_pylist(
            [{"player_id": "PLR-001", "name": "Alice"}],
            schema=pa.schema([("player_id", pa.string()), ("name", pa.string())]),
        )
        pq.write_table(dim_table, str(batch_dir / "dim_player.parquet"))

        _make_manifest(batch_dir, "ARCH-T011", [
            {"role": "fact", "entity": "practice_log", "filename": "fact_practice_log.parquet", "row_count": 2},
            {"role": "dimension", "entity": "player", "filename": "dim_player.parquet", "row_count": 1},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        resp = json.loads(result.output)
        assert resp["batches_ok"] == 2
        assert resp["batches_error"] == 0
        assert len(resp["batches"]) == 2

    def test_interaction_log_ingest(self, cfg, tmp_path):
        """Interaction log fact file ingested to correct partition."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        rows = [{
            "name": "ILOG-000001", "player": "PLR-001", "lesson": "L001",
            "stage_id": "STG-01", "item_id": "ITEM-001", "event_type": "answer",
            "time_spent": 12.5, "errors_count": 0,
            "timestamp": datetime(2025, 8, 20, 10, 30, 0),
            "season_id": "S1", "plan_id": "P1",
        }]
        write_interaction_log_parquet(batch_dir / "fact_interaction_log.parquet", rows=rows)
        _make_manifest(batch_dir, "ARCH-T012", [
            {"role": "fact", "entity": "interaction_log", "filename": "fact_interaction_log.parquet", "row_count": 1},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        expected = Path(cfg.lake_path) / "interaction_log" / "year=2025" / "month=08" / "day=20"
        assert expected.exists()

    def test_task_run_log_ingest(self, cfg, tmp_path):
        """Task run log fact file ingested to correct partition."""
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()

        rows = [{
            "name": "TLOG-000001", "task_name": "archive_cleanup",
            "run_date": date(2025, 7, 10),
            "started_at": datetime(2025, 7, 10, 2, 0, 0),
            "completed_at": datetime(2025, 7, 10, 2, 5, 0),
            "duration_sec": 300.0, "status": "Success",
            "triggered_by": "scheduler", "processed_count": 100,
            "failed_count": 0, "error_message": None,
        }]
        write_task_run_log_parquet(batch_dir / "fact_task_run_log.parquet", rows=rows)
        _make_manifest(batch_dir, "ARCH-T013", [
            {"role": "fact", "entity": "task_run_log", "filename": "fact_task_run_log.parquet", "row_count": 1},
        ])

        runner = CliRunner()
        result = _invoke_ingest(runner, cfg, str(batch_dir))

        assert result.exit_code == 0
        expected = Path(cfg.lake_path) / "task_run_log" / "year=2025" / "month=07" / "day=10"
        assert expected.exists()
