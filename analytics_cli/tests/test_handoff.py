"""Tests for the handoff CLI command (T012 — US4).

Covers:
- Date-range mode DELETE from practice_log_live by date column/range
- Season mode DELETE from memory_state_current by season_seq
- rows_removed count accuracy
- JSON response schemas for both modes per cli-contract.json
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
from click.testing import CliRunner

from analytics_cli.__main__ import cli
from analytics_cli.views.semantic import ensure_live_tables


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


def _populate_practice_log_live(duckdb_path: str, rows: list[dict]) -> None:
    """Create practice_log_live and insert rows."""
    conn = duckdb.connect(duckdb_path)
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
    conn.close()


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


def _make_practice_rows(dates: list[datetime]) -> list[dict]:
    """Generate practice_log rows for specific dates."""
    return [
        {
            "player_id": f"PLR-{i:04d}",
            "item_id": f"ITEM-{i:04d}",
            "first_seen_at": d,
            "last_seen_at": d,
            "last_result": "correct",
            "attempt_count": 1,
            "correct_count": 1,
            "season_id": "S1",
            "plan_id": "PL1",
        }
        for i, d in enumerate(dates)
    ]


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


# ---------------------------------------------------------------------------
# Date-range mode
# ---------------------------------------------------------------------------


class TestHandoffDateRange:
    """Date-range handoff: DELETE from practice_log_live by date range."""

    def test_deletes_rows_in_date_range(self, cfg, tmp_path):
        archive_batch = tmp_path / "archive_batch"
        archive_batch.mkdir()

        rows = _make_practice_rows([
            datetime(2025, 6, 10),  # in range
            datetime(2025, 6, 15),  # in range
            datetime(2025, 6, 20),  # out of range
        ])
        _populate_practice_log_live(cfg.duckdb_path, rows)

        result = _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", str(archive_batch),
            "--date-column", "last_seen_at",
            "--from", "2025-06-01",
            "--to", "2025-06-15",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["rows_removed"] == 2

    def test_preserves_rows_outside_range(self, cfg, tmp_path):
        archive_batch = tmp_path / "archive_batch"
        archive_batch.mkdir()

        rows = _make_practice_rows([
            datetime(2025, 6, 10),
            datetime(2025, 6, 20),
            datetime(2025, 6, 25),
        ])
        _populate_practice_log_live(cfg.duckdb_path, rows)

        _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", str(archive_batch),
            "--date-column", "last_seen_at",
            "--from", "2025-06-01",
            "--to", "2025-06-15",
        ])

        conn = duckdb.connect(cfg.duckdb_path)
        count = conn.execute("SELECT COUNT(*) FROM practice_log_live").fetchone()[0]
        conn.close()
        assert count == 2  # Only rows outside range remain

    def test_response_schema_date_range(self, cfg, tmp_path):
        archive_batch = tmp_path / "archive_batch"
        archive_batch.mkdir()

        _populate_practice_log_live(cfg.duckdb_path, _make_practice_rows([
            datetime(2025, 6, 10),
        ]))

        result = _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", str(archive_batch),
            "--date-column", "last_seen_at",
            "--from", "2025-06-01",
            "--to", "2025-06-30",
        ])
        data = json.loads(result.output)

        assert data["status"] == "ok"
        assert data["mode"] == "date_range"
        assert isinstance(data["rows_removed"], int)
        assert data["date_column"] == "last_seen_at"
        assert data["from"] == "2025-06-01"
        assert data["to"] == "2025-06-30"
        assert isinstance(data["duration_ms"], int)


# ---------------------------------------------------------------------------
# Season mode
# ---------------------------------------------------------------------------


class TestHandoffSeason:
    """Season handoff: DELETE from memory_state_current by season_seq."""

    def test_deletes_matching_season(self, cfg, tmp_path):
        archive_batch = tmp_path / "archive_batch"
        archive_batch.mkdir()

        rows = _make_memory_state_rows([5, 5, 5, 6, 6])
        _populate_memory_state_current(cfg.duckdb_path, rows)

        result = _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", str(archive_batch),
            "--season-seq", "5",
            "--archive-type", "memory_state",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["rows_removed"] == 3

    def test_preserves_other_seasons(self, cfg, tmp_path):
        archive_batch = tmp_path / "archive_batch"
        archive_batch.mkdir()

        rows = _make_memory_state_rows([5, 5, 6, 7])
        _populate_memory_state_current(cfg.duckdb_path, rows)

        _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", str(archive_batch),
            "--season-seq", "5",
            "--archive-type", "memory_state",
        ])

        conn = duckdb.connect(cfg.duckdb_path)
        count = conn.execute("SELECT COUNT(*) FROM memory_state_current").fetchone()[0]
        conn.close()
        assert count == 2  # season 6 and 7 remain

    def test_response_schema_season(self, cfg, tmp_path):
        archive_batch = tmp_path / "archive_batch"
        archive_batch.mkdir()

        _populate_memory_state_current(cfg.duckdb_path, _make_memory_state_rows([5]))

        result = _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", str(archive_batch),
            "--season-seq", "5",
            "--archive-type", "memory_state",
        ])
        data = json.loads(result.output)

        assert data["status"] == "ok"
        assert data["mode"] == "season"
        assert isinstance(data["season_seq"], int)
        assert data["season_seq"] == 5
        assert isinstance(data["rows_removed"], int)
        assert isinstance(data["duration_ms"], int)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestHandoffErrors:
    """Error handling for handoff command."""

    def test_error_on_missing_batch_dir(self, cfg):
        result = _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", "/nonexistent/dir",
            "--date-column", "last_seen_at",
            "--from", "2025-06-01",
            "--to", "2025-06-30",
        ])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"

    def test_error_on_missing_mode_args(self, cfg, tmp_path):
        archive_batch = tmp_path / "archive_batch"
        archive_batch.mkdir()

        result = _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", str(archive_batch),
        ])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"

    def test_error_on_invalid_date_column(self, cfg, tmp_path):
        archive_batch = tmp_path / "archive_batch"
        archive_batch.mkdir()

        _populate_practice_log_live(cfg.duckdb_path, _make_practice_rows([
            datetime(2025, 6, 10),
        ]))

        result = _invoke(CliRunner(), cfg, [
            "handoff",
            "--archive-batch-dir", str(archive_batch),
            "--date-column", "invalid_column",
            "--from", "2025-06-01",
            "--to", "2025-06-30",
        ])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
