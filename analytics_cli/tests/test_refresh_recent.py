"""Tests for the refresh-recent CLI command (US4 — T015).

Covers:
- Rebuilds practice_recent from practice_log_combined
- Window-days filtering
- JSON response schema per cli-contract.json
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

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
    # Create a combined view that reads from the live table only
    # (archive view would need Parquet files; live-only is sufficient for testing)
    conn.execute("""\
CREATE OR REPLACE VIEW practice_log_combined AS
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'live' AS source
FROM practice_log_live""")
    conn.close()


class TestRefreshRecent:
    """Verify refresh-recent rebuilds practice_recent table."""

    def test_rebuilds_practice_recent(self, cfg, tmp_path):
        now = datetime.now()
        rows = [
            {
                "player_id": f"PLR-{i:04d}", "item_id": f"ITEM-{i:04d}",
                "first_seen_at": now - timedelta(days=10),
                "last_seen_at": now - timedelta(days=i),
                "last_result": "correct", "attempt_count": 1,
                "correct_count": 1, "season_id": "S1", "plan_id": "PL1",
            }
            for i in range(5)
        ]
        _setup_combined_view(cfg.duckdb_path, rows)

        result = _invoke(CliRunner(), cfg, [
            "refresh-recent", "--archive-type", "practice_log", "--window-days", "90",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["row_count"] == 5
        assert data["window_days"] == 90

    def test_window_days_filters_old_rows(self, cfg, tmp_path):
        now = datetime.now()
        rows = [
            {
                "player_id": "PLR-NEW", "item_id": "ITEM-NEW",
                "first_seen_at": now - timedelta(days=5),
                "last_seen_at": now - timedelta(days=5),
                "last_result": "correct", "attempt_count": 1,
                "correct_count": 1, "season_id": "S1", "plan_id": "PL1",
            },
            {
                "player_id": "PLR-OLD", "item_id": "ITEM-OLD",
                "first_seen_at": now - timedelta(days=200),
                "last_seen_at": now - timedelta(days=200),
                "last_result": "correct", "attempt_count": 1,
                "correct_count": 1, "season_id": "S1", "plan_id": "PL1",
            },
        ]
        _setup_combined_view(cfg.duckdb_path, rows)

        result = _invoke(CliRunner(), cfg, [
            "refresh-recent", "--archive-type", "practice_log", "--window-days", "90",
        ])
        data = json.loads(result.output)
        assert data["row_count"] == 1  # Only the recent row

    def test_response_schema(self, cfg, tmp_path):
        now = datetime.now()
        rows = [
            {
                "player_id": "PLR-0001", "item_id": "ITEM-0001",
                "first_seen_at": now - timedelta(days=5),
                "last_seen_at": now - timedelta(days=5),
                "last_result": "correct", "attempt_count": 1,
                "correct_count": 1, "season_id": "S1", "plan_id": "PL1",
            },
        ]
        _setup_combined_view(cfg.duckdb_path, rows)

        result = _invoke(CliRunner(), cfg, [
            "refresh-recent", "--archive-type", "practice_log",
        ])
        data = json.loads(result.output)

        assert data["status"] == "ok"
        assert isinstance(data["row_count"], int)
        assert isinstance(data["window_days"], int)
        assert isinstance(data["duration_ms"], int)

    def test_error_on_unsupported_archive_type(self, cfg):
        result = _invoke(CliRunner(), cfg, [
            "refresh-recent", "--archive-type", "unsupported",
        ])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
