"""Tests for DuckDB semantic views (T008).

Covers:
- Archive views for all 5 fact datasets with hive_partitioning=true, union_by_name=true
- Dimension views reading from dimensions/ path
- Combined views (practice_log_combined, memory_state_combined) UNION ALL
- structure_progress_snapshots view with snapshot_date partition
- Graceful handling of missing data
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from analytics_cli.config import Config
from analytics_cli.tests.conftest import (
    build_hive_dir,
    write_interaction_log_parquet,
    write_practice_log_parquet,
    write_task_run_log_parquet,
)
from analytics_cli.views.semantic import (
    create_archive_views,
    create_combined_views,
    create_dimension_views,
    ensure_live_tables,
    refresh_all_views,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_memory_state_no_season(path: Path, n: int = 3) -> None:
    """Write memory_state Parquet WITHOUT season_seq (for Hive partition dirs)."""
    schema = pa.schema([
        ("name", pa.int64()),
        ("subject", pa.string()),
        ("player", pa.string()),
        ("item_id", pa.string()),
        ("stage_id", pa.string()),
        ("stability", pa.float64()),
        ("difficulty", pa.float64()),
        ("next_review", pa.timestamp("us")),
        ("lesson", pa.string()),
        ("state", pa.int8()),
        ("step", pa.int8()),
        ("last_review", pa.timestamp("us")),
        ("modified", pa.timestamp("us")),
    ])
    rows = [
        {
            "name": 1000 + i,
            "subject": "math",
            "player": f"PLR-{i:04d}",
            "item_id": f"ITEM-{i:04d}",
            "stage_id": "STAGE-01",
            "stability": 2.5 + i * 0.1,
            "difficulty": 0.3 + i * 0.05,
            "next_review": datetime(2025, 7, 1, 10, 0, 0),
            "lesson": "LESSON-001",
            "state": 2,
            "step": 1,
            "last_review": datetime(2025, 6, 15, 10, 0, 0),
            "modified": datetime(2025, 6, 15, 14, 30, 0),
        }
        for i in range(n)
    ]
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, str(path))


def _write_structure_progress_no_date(path: Path, n: int = 3) -> None:
    """Write structure_progress Parquet WITHOUT snapshot_date (for Hive dirs)."""
    schema = pa.schema([
        ("player_id", pa.string()),
        ("plan_id", pa.string()),
        ("subject_id", pa.string()),
        ("completion_percentage", pa.float64()),
    ])
    rows = [
        {
            "player_id": f"PLR-{i:04d}",
            "plan_id": "PLAN-001",
            "subject_id": f"SUBJ-{i:02d}",
            "completion_percentage": 50.0 + i * 10,
        }
        for i in range(n)
    ]
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, str(path))


def _write_dim_parquet(path: Path, rows: list[dict]) -> None:
    """Write a simple dimension Parquet file."""
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, str(path))


# ── Archive view tests ───────────────────────────────────────────────────────


class TestArchiveViews:
    def test_practice_log_archive(self, db, lake_dir):
        pf = build_hive_dir(lake_dir, "practice_log", {"year": "2025", "month": "06", "day": "15"})
        write_practice_log_parquet(pf, n=3)

        created = create_archive_views(db, str(lake_dir))

        assert "practice_log_archive" in created
        rows = db.execute("SELECT COUNT(*) FROM practice_log_archive").fetchone()
        assert rows[0] == 3

    def test_practice_log_hive_partition_columns(self, db, lake_dir):
        """Verify year/month/day are available via hive_partitioning."""
        pf = build_hive_dir(lake_dir, "practice_log", {"year": "2025", "month": "06", "day": "15"})
        write_practice_log_parquet(pf, n=1)

        create_archive_views(db, str(lake_dir))

        row = db.execute("SELECT year, month, day FROM practice_log_archive").fetchone()
        assert int(row[0]) == 2025
        assert int(row[1]) == 6
        assert int(row[2]) == 15

    def test_practice_log_partition_pruning(self, db, lake_dir):
        """Filtering on partition column returns correct subset."""
        pf1 = build_hive_dir(lake_dir, "practice_log", {"year": "2025", "month": "06", "day": "01"})
        write_practice_log_parquet(pf1, n=2)
        pf2 = build_hive_dir(lake_dir, "practice_log", {"year": "2025", "month": "06", "day": "02"})
        write_practice_log_parquet(pf2, n=3)

        create_archive_views(db, str(lake_dir))

        count = db.execute(
            "SELECT COUNT(*) FROM practice_log_archive WHERE year=2025 AND month=6 AND day=1"
        ).fetchone()[0]
        assert count == 2

    def test_union_by_name_handles_extra_column(self, db, lake_dir):
        """Parquet files with different schemas are unioned — missing cols become NULL."""
        # Partition 1: standard schema
        pf1 = build_hive_dir(lake_dir, "practice_log", {"year": "2025", "month": "06", "day": "01"})
        write_practice_log_parquet(pf1, n=2)

        # Partition 2: extra column
        pf2 = build_hive_dir(lake_dir, "practice_log", {"year": "2025", "month": "06", "day": "02"})
        extra_schema = pa.schema([
            ("player_id", pa.string()),
            ("item_id", pa.string()),
            ("first_seen_at", pa.timestamp("us")),
            ("last_seen_at", pa.timestamp("us")),
            ("last_result", pa.string()),
            ("attempt_count", pa.int32()),
            ("correct_count", pa.int32()),
            ("season_id", pa.string()),
            ("plan_id", pa.string()),
            ("extra_col", pa.string()),
        ])
        extra_rows = [{
            "player_id": "PLR-X",
            "item_id": "ITEM-X",
            "first_seen_at": datetime(2025, 6, 2, 10, 0, 0),
            "last_seen_at": datetime(2025, 6, 2, 14, 0, 0),
            "last_result": "correct",
            "attempt_count": 1,
            "correct_count": 1,
            "season_id": "S1",
            "plan_id": "P1",
            "extra_col": "bonus",
        }]
        pq.write_table(pa.Table.from_pylist(extra_rows, schema=extra_schema), str(pf2))

        create_archive_views(db, str(lake_dir))

        # Rows from partition 1 have NULL extra_col
        nulls = db.execute(
            "SELECT extra_col FROM practice_log_archive WHERE day=1"
        ).fetchall()
        assert all(r[0] is None for r in nulls)

        # Row from partition 2 has the value
        bonus = db.execute(
            "SELECT extra_col FROM practice_log_archive WHERE day=2"
        ).fetchone()
        assert bonus[0] == "bonus"

    def test_interaction_log_archive(self, db, lake_dir):
        pf = build_hive_dir(lake_dir, "interaction_log", {"year": "2025", "month": "06", "day": "15"})
        write_interaction_log_parquet(pf, n=4)

        created = create_archive_views(db, str(lake_dir))

        assert "interaction_log_archive" in created
        assert db.execute("SELECT COUNT(*) FROM interaction_log_archive").fetchone()[0] == 4

    def test_memory_state_archive_season_partition(self, db, lake_dir):
        """memory_state partitioned by season_seq — column reconstructed from path."""
        part_dir = lake_dir / "memory_state" / "season_seq=5"
        part_dir.mkdir(parents=True)
        _write_memory_state_no_season(part_dir / "part-0000.parquet", n=3)

        created = create_archive_views(db, str(lake_dir))

        assert "memory_state_archive" in created
        rows = db.execute("SELECT season_seq, COUNT(*) FROM memory_state_archive GROUP BY season_seq").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 5
        assert rows[0][1] == 3

    def test_task_run_log_archive(self, db, lake_dir):
        pf = build_hive_dir(lake_dir, "task_run_log", {"year": "2025", "month": "06", "day": "15"})
        write_task_run_log_parquet(pf, n=5)

        created = create_archive_views(db, str(lake_dir))

        assert "task_run_log_archive" in created
        assert db.execute("SELECT COUNT(*) FROM task_run_log_archive").fetchone()[0] == 5

    def test_structure_progress_snapshots_partition(self, db, lake_dir):
        """structure_progress partitioned by snapshot_date."""
        part_dir = lake_dir / "structure_progress" / "snapshot_date=2025-06-15"
        part_dir.mkdir(parents=True)
        _write_structure_progress_no_date(part_dir / "part-0000.parquet", n=4)

        created = create_archive_views(db, str(lake_dir))

        assert "structure_progress_snapshots" in created
        rows = db.execute("SELECT snapshot_date, COUNT(*) FROM structure_progress_snapshots GROUP BY 1").fetchall()
        assert len(rows) == 1
        # DuckDB parses snapshot_date from path as DATE
        assert str(rows[0][0]) == "2025-06-15"
        assert rows[0][1] == 4

    def test_structure_progress_multiple_snapshots(self, db, lake_dir):
        """Multiple snapshot_date partitions queryable with filtering."""
        for d in ["2025-06-10", "2025-06-11"]:
            p = lake_dir / "structure_progress" / f"snapshot_date={d}"
            p.mkdir(parents=True)
            _write_structure_progress_no_date(p / "part-0000.parquet", n=2)

        create_archive_views(db, str(lake_dir))

        total = db.execute("SELECT COUNT(*) FROM structure_progress_snapshots").fetchone()[0]
        assert total == 4

        filtered = db.execute(
            "SELECT COUNT(*) FROM structure_progress_snapshots WHERE snapshot_date = '2025-06-10'"
        ).fetchone()[0]
        assert filtered == 2

    def test_empty_lake_no_views(self, db, lake_dir):
        """No data in lake → no views created, no errors."""
        created = create_archive_views(db, str(lake_dir))
        assert created == []


# ── Dimension view tests ─────────────────────────────────────────────────────


class TestDimensionViews:
    def test_dimension_views_created(self, db, dimensions_dir):
        _write_dim_parquet(
            dimensions_dir / "dim_player.parquet",
            [{"player_id": "PLR-001", "name": "Alice"}],
        )
        _write_dim_parquet(
            dimensions_dir / "dim_season.parquet",
            [{"season_id": "S1", "name": "Season 1"}],
        )

        created = create_dimension_views(db, str(dimensions_dir))

        assert "dim_player" in created
        assert "dim_season" in created
        assert db.execute("SELECT COUNT(*) FROM dim_player").fetchone()[0] == 1

    def test_missing_dimension_skipped(self, db, dimensions_dir):
        """Only dim_player exists — others silently skipped."""
        _write_dim_parquet(
            dimensions_dir / "dim_player.parquet",
            [{"player_id": "PLR-001", "name": "Alice"}],
        )

        created = create_dimension_views(db, str(dimensions_dir))

        assert "dim_player" in created
        assert "dim_player_history" not in created
        assert "dim_season" not in created

    def test_all_six_dimensions(self, db, dimensions_dir):
        """All 6 dimension types create views when present."""
        dim_names = [
            "dim_player", "dim_player_history", "dim_season",
            "dim_plan", "dim_review_item", "dim_lesson",
        ]
        for dim in dim_names:
            _write_dim_parquet(
                dimensions_dir / f"{dim}.parquet",
                [{"id": "1", "label": dim}],
            )

        created = create_dimension_views(db, str(dimensions_dir))
        assert set(created) == set(dim_names)


# ── Live table tests ─────────────────────────────────────────────────────────


class TestLiveTables:
    def test_ensure_live_tables_creates_both(self, db):
        ensure_live_tables(db)

        # Tables exist and are empty
        pl = db.execute("SELECT COUNT(*) FROM practice_log_live").fetchone()[0]
        ms = db.execute("SELECT COUNT(*) FROM memory_state_current").fetchone()[0]
        assert pl == 0
        assert ms == 0

    def test_ensure_live_tables_idempotent(self, db):
        """Calling twice doesn't error."""
        ensure_live_tables(db)
        ensure_live_tables(db)

        assert db.execute("SELECT COUNT(*) FROM practice_log_live").fetchone()[0] == 0


# ── Combined view tests ──────────────────────────────────────────────────────


class TestCombinedViews:
    def test_practice_log_combined(self, db, lake_dir):
        """Combined view unions archive + live data."""
        # Archive data
        pf = build_hive_dir(lake_dir, "practice_log", {"year": "2025", "month": "06", "day": "15"})
        write_practice_log_parquet(pf, n=3)
        create_archive_views(db, str(lake_dir))

        # Live data
        ensure_live_tables(db)
        db.execute("""
            INSERT INTO practice_log_live (player_id, item_id, first_seen_at, last_seen_at,
                last_result, attempt_count, correct_count, season_id, plan_id)
            VALUES ('PLR-LIVE', 'ITEM-LIVE', '2025-07-01', '2025-07-15', 'correct', 5, 4, 'S2', 'P2')
        """)

        create_combined_views(db)

        total = db.execute("SELECT COUNT(*) FROM practice_log_combined").fetchone()[0]
        assert total == 4  # 3 archive + 1 live

        sources = db.execute(
            "SELECT source, COUNT(*) FROM practice_log_combined GROUP BY source ORDER BY source"
        ).fetchall()
        assert sources == [("archive", 3), ("live", 1)]

    def test_memory_state_combined(self, db, lake_dir):
        """Combined view unions archive + current mirror."""
        # Archive data
        part_dir = lake_dir / "memory_state" / "season_seq=5"
        part_dir.mkdir(parents=True)
        _write_memory_state_no_season(part_dir / "part-0000.parquet", n=2)
        create_archive_views(db, str(lake_dir))

        # Current mirror data
        ensure_live_tables(db)
        db.execute("""
            INSERT INTO memory_state_current
                (name, season_seq, subject, player, item_id, stage_id,
                 stability, difficulty, next_review, lesson, state, step,
                 last_review, modified)
            VALUES (9999, 6, 'math', 'PLR-CUR', 'ITEM-CUR', 'STG-01',
                    3.0, 0.5, '2025-08-01', 'L001', 2, 1, '2025-07-01', '2025-07-01')
        """)

        create_combined_views(db)

        total = db.execute("SELECT COUNT(*) FROM memory_state_combined").fetchone()[0]
        assert total == 3  # 2 archive + 1 current

        sources = db.execute(
            "SELECT source, COUNT(*) FROM memory_state_combined GROUP BY source ORDER BY source"
        ).fetchall()
        assert sources == [("archive", 2), ("current", 1)]

    def test_combined_not_created_without_archive(self, db):
        """Combined views require archive views — skip if missing."""
        ensure_live_tables(db)
        created = create_combined_views(db)
        assert created == []


# ── refresh_all_views integration ────────────────────────────────────────────


class TestRefreshAllViews:
    def test_refresh_creates_views_and_tables(self, db, lake_dir, dimensions_dir, manifests_dir):
        """refresh_all_views orchestrates everything."""
        # Set up some data
        pf = build_hive_dir(lake_dir, "practice_log", {"year": "2025", "month": "06", "day": "15"})
        write_practice_log_parquet(pf, n=2)
        _write_dim_parquet(
            dimensions_dir / "dim_player.parquet",
            [{"player_id": "PLR-001"}],
        )

        cfg = Config(
            duckdb_path=":memory:",
            lake_path=str(lake_dir),
            dimensions_path=str(dimensions_dir),
            manifests_path=str(manifests_dir),
        )
        created = refresh_all_views(db, cfg)

        assert "practice_log_archive" in created
        assert "dim_player" in created
        assert "practice_log_combined" in created
        # Live tables exist
        assert db.execute("SELECT COUNT(*) FROM practice_log_live").fetchone()[0] == 0
