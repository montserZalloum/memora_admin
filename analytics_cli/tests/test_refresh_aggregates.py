"""Tests for the refresh-aggregates CLI command (US4 — T016).

Covers:
- Rebuilds practice_daily_agg and practice_monthly_agg from combined view
- Correct aggregation logic
- JSON response schema per cli-contract.json
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb
import pytest
from click.testing import CliRunner

from analytics_cli.__main__ import cli
from analytics_cli.views.semantic import ensure_live_tables


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


def _setup_combined_view(duckdb_path: str, rows: list[dict]) -> None:
    """Create practice_log_live with data and a practice_log_combined view."""
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
    conn.execute("""\
CREATE OR REPLACE VIEW practice_log_combined AS
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'live' AS source
FROM practice_log_live""")
    conn.close()


class TestRefreshAggregates:
    """Verify refresh-aggregates rebuilds daily and monthly agg tables."""

    def test_rebuilds_daily_and_monthly(self, cfg, tmp_path):
        rows = [
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0001",
                "first_seen_at": datetime(2025, 6, 10, 10, 0),
                "last_seen_at": datetime(2025, 6, 10, 14, 0),
                "last_result": "correct", "attempt_count": 3,
                "correct_count": 2, "season_id": "S1", "plan_id": "PL1",
            },
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0002",
                "first_seen_at": datetime(2025, 6, 10, 11, 0),
                "last_seen_at": datetime(2025, 6, 10, 15, 0),
                "last_result": "correct", "attempt_count": 5,
                "correct_count": 4, "season_id": "S1", "plan_id": "PL1",
            },
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0003",
                "first_seen_at": datetime(2025, 6, 12, 10, 0),
                "last_seen_at": datetime(2025, 6, 12, 14, 0),
                "last_result": "correct", "attempt_count": 2,
                "correct_count": 1, "season_id": "S1", "plan_id": "PL1",
            },
        ]
        _setup_combined_view(cfg.duckdb_path, rows)

        result = _invoke(CliRunner(), cfg, [
            "refresh-aggregates", "--archive-type", "practice_log",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["daily_rows"] == 2  # 2 distinct dates for PLR-0001
        assert data["monthly_rows"] == 1  # 1 distinct year-month

    def test_daily_agg_values(self, cfg, tmp_path):
        rows = [
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0001",
                "first_seen_at": datetime(2025, 6, 10, 10, 0),
                "last_seen_at": datetime(2025, 6, 10, 14, 0),
                "last_result": "correct", "attempt_count": 3,
                "correct_count": 2, "season_id": "S1", "plan_id": "PL1",
            },
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0002",
                "first_seen_at": datetime(2025, 6, 10, 11, 0),
                "last_seen_at": datetime(2025, 6, 10, 15, 0),
                "last_result": "correct", "attempt_count": 5,
                "correct_count": 4, "season_id": "S1", "plan_id": "PL1",
            },
        ]
        _setup_combined_view(cfg.duckdb_path, rows)

        _invoke(CliRunner(), cfg, [
            "refresh-aggregates", "--archive-type", "practice_log",
        ])

        conn = duckdb.connect(cfg.duckdb_path)
        row = conn.execute(
            "SELECT total_attempts, total_correct, unique_items "
            "FROM practice_daily_agg WHERE player_id = 'PLR-0001'"
        ).fetchone()
        conn.close()

        assert row[0] == 8   # 3 + 5
        assert row[1] == 6   # 2 + 4
        assert row[2] == 2   # 2 distinct items

    def test_monthly_agg_active_days(self, cfg, tmp_path):
        rows = [
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0001",
                "first_seen_at": datetime(2025, 6, 10, 10, 0),
                "last_seen_at": datetime(2025, 6, 10, 14, 0),
                "last_result": "correct", "attempt_count": 1,
                "correct_count": 1, "season_id": "S1", "plan_id": "PL1",
            },
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0002",
                "first_seen_at": datetime(2025, 6, 12, 10, 0),
                "last_seen_at": datetime(2025, 6, 12, 14, 0),
                "last_result": "correct", "attempt_count": 1,
                "correct_count": 1, "season_id": "S1", "plan_id": "PL1",
            },
        ]
        _setup_combined_view(cfg.duckdb_path, rows)

        _invoke(CliRunner(), cfg, [
            "refresh-aggregates", "--archive-type", "practice_log",
        ])

        conn = duckdb.connect(cfg.duckdb_path)
        row = conn.execute(
            "SELECT year_month, active_days FROM practice_monthly_agg "
            "WHERE player_id = 'PLR-0001'"
        ).fetchone()
        conn.close()

        assert row[0] == "2025-06"
        assert row[1] == 2  # Active on 2 days

    def test_response_schema(self, cfg, tmp_path):
        rows = [
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0001",
                "first_seen_at": datetime(2025, 6, 10, 10, 0),
                "last_seen_at": datetime(2025, 6, 10, 14, 0),
                "last_result": "correct", "attempt_count": 1,
                "correct_count": 1, "season_id": "S1", "plan_id": "PL1",
            },
        ]
        _setup_combined_view(cfg.duckdb_path, rows)

        result = _invoke(CliRunner(), cfg, [
            "refresh-aggregates", "--archive-type", "practice_log",
        ])
        data = json.loads(result.output)

        assert data["status"] == "ok"
        assert isinstance(data["daily_rows"], int)
        assert isinstance(data["monthly_rows"], int)
        assert isinstance(data["duration_ms"], int)

    def test_error_on_unsupported_archive_type(self, cfg):
        result = _invoke(CliRunner(), cfg, [
            "refresh-aggregates", "--archive-type", "unsupported",
        ])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
