"""Tests for the mirror-status CLI command (US4 — T017).

Covers:
- Per-season row counts from memory_state_current
- Per-season row counts from memory_state_archive
- JSON response schema per cli-contract.json mirror-status schema
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from analytics_cli.__main__ import cli
from analytics_cli.views.semantic import (
    create_archive_views,
    ensure_live_tables,
)


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


def _populate_memory_state_current(duckdb_path: str, rows: list[dict]) -> None:
    """Create memory_state_current and insert rows."""
    conn = duckdb.connect(duckdb_path)
    ensure_live_tables(conn)
    for r in rows:
        conn.execute(
            "INSERT INTO memory_state_current "
            "(name, season_seq, subject, player, item_id, stage_id, "
            " stability, difficulty, next_review, lesson, state, step, "
            " last_review, modified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                r["name"], r["season_seq"], r["subject"], r["player"],
                r["item_id"], r["stage_id"], r["stability"], r["difficulty"],
                r["next_review"], r["lesson"], r["state"], r["step"],
                r["last_review"], r["modified"],
            ],
        )
    conn.close()


def _write_memory_state_no_season(path: Path, n: int = 5) -> None:
    """Write memory_state Parquet WITHOUT season_seq (for Hive-partitioned dirs)."""
    rows = [
        {
            "name": 1000 + i,
            "subject": "math",
            "player": f"PLR-{i:04d}",
            "item_id": f"ITEM-{i:04d}",
            "stage_id": "STAGE-01",
            "stability": 2.5 + i * 0.1,
            "difficulty": 0.3 + i * 0.05,
            "next_review": datetime(2025, 7, 1),
            "lesson": "LESSON-001",
            "state": 2,
            "step": 1,
            "last_review": datetime(2025, 6, 15),
            "modified": datetime(2025, 6, 15, 14, 30),
        }
        for i in range(n)
    ]
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
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, str(path))


def _make_memory_state_rows(season_seqs: list[int]) -> list[dict]:
    """Generate memory_state rows for specific season_seqs."""
    return [
        {
            "name": 1000 + i,
            "season_seq": seq,
            "subject": "math",
            "player": f"PLR-{i:04d}",
            "item_id": f"ITEM-{i:04d}",
            "stage_id": "STAGE-01",
            "stability": 2.5,
            "difficulty": 0.3,
            "next_review": datetime(2025, 7, 1),
            "lesson": "LESSON-001",
            "state": 2,
            "step": 1,
            "last_review": datetime(2025, 6, 15),
            "modified": datetime(2025, 6, 15, 14, 30),
        }
        for i, seq in enumerate(season_seqs)
    ]


class TestMirrorStatus:
    """Verify mirror-status returns per-season row counts."""

    def test_current_mirror_stats(self, cfg, lake_dir, tmp_path):
        rows = _make_memory_state_rows([5, 5, 5, 6, 6])
        _populate_memory_state_current(cfg.duckdb_path, rows)

        result = _invoke(CliRunner(), cfg, [
            "mirror-status", "--archive-type", "memory_state",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)

        assert data["status"] == "ok"
        assert data["archive_type"] == "memory_state"
        assert data["current_mirror"]["total_rows"] == 5
        seasons = data["current_mirror"]["seasons"]
        assert len(seasons) == 2
        season_map = {s["season_seq"]: s["row_count"] for s in seasons}
        assert season_map[5] == 3
        assert season_map[6] == 2

    def test_archived_seasons_from_parquet(self, cfg, lake_dir, tmp_path):
        # Create Hive-partitioned memory_state archive
        s5_dir = lake_dir / "memory_state" / "season_seq=5"
        s5_dir.mkdir(parents=True)
        _write_memory_state_no_season(s5_dir / "part-0000.parquet", n=10)

        s6_dir = lake_dir / "memory_state" / "season_seq=6"
        s6_dir.mkdir(parents=True)
        _write_memory_state_no_season(s6_dir / "part-0000.parquet", n=3)

        # Create archive views first
        conn = duckdb.connect(cfg.duckdb_path)
        ensure_live_tables(conn)
        create_archive_views(conn, str(lake_dir))
        conn.close()

        result = _invoke(CliRunner(), cfg, [
            "mirror-status", "--archive-type", "memory_state",
        ])
        data = json.loads(result.output)

        archived = data["archived_seasons"]
        assert len(archived) == 2
        archive_map = {a["season_seq"]: a for a in archived}
        assert archive_map[5]["row_count"] == 10
        assert archive_map[6]["row_count"] == 3
        assert "parquet_path" in archive_map[5]

    def test_response_schema(self, cfg, lake_dir, tmp_path):
        _populate_memory_state_current(
            cfg.duckdb_path, _make_memory_state_rows([5])
        )

        result = _invoke(CliRunner(), cfg, [
            "mirror-status", "--archive-type", "memory_state",
        ])
        data = json.loads(result.output)

        assert data["status"] == "ok"
        assert data["archive_type"] == "memory_state"
        assert "current_mirror" in data
        assert "total_rows" in data["current_mirror"]
        assert "seasons" in data["current_mirror"]
        assert isinstance(data.get("duration_ms"), int)

    def test_empty_mirror(self, cfg, lake_dir, tmp_path):
        """Works with empty tables (no data yet)."""
        conn = duckdb.connect(cfg.duckdb_path)
        ensure_live_tables(conn)
        conn.close()

        result = _invoke(CliRunner(), cfg, [
            "mirror-status", "--archive-type", "memory_state",
        ])
        data = json.loads(result.output)

        assert data["status"] == "ok"
        assert data["current_mirror"]["total_rows"] == 0
        assert data["current_mirror"]["seasons"] == []
        assert data["archived_seasons"] == []

    def test_error_on_unsupported_archive_type(self, cfg):
        result = _invoke(CliRunner(), cfg, [
            "mirror-status", "--archive-type", "unsupported",
        ])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
