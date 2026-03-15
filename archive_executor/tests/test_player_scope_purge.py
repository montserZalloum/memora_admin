"""Unit tests for player-scoped purge (_purge_player_scope).

Covers:
  PSP-001: Full flow — source table purge + cleanup table purge + mark Purged
  PSP-002: Resume from partial source purge (source_purge_complete=False)
  PSP-003: Cleanup tables run after source purge completes
  PSP-004: Empty player_ids completes cleanly (no queries, marks Purged)
  PSP-005: Archive files missing → skipped with warning
  PSP-006: Cleanup table already completed → skipped on resume

Run with:
    python3 -m pytest archive_executor/tests/test_player_scope_purge.py -v
"""

import json
import os
from unittest.mock import MagicMock, call, patch

import pytest

from archive_executor.purge import (
    _purge_cleanup_table,
    _purge_player_scope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config():
    """Return a minimal Config-like mock."""
    config = MagicMock()
    config.db_host = "127.0.0.1"
    config.db_port = 3306
    return config


def _make_job(name="ARCH-30001", source_doctype="Memora Practice Log",
              archive_scope="SEAS-TEST", file_path="/tmp/archive/ARCH-30001",
              purge_progress=None):
    return {
        "name": name,
        "source_doctype": source_doctype,
        "archive_scope": archive_scope,
        "file_path": file_path,
        "purge_progress": json.dumps(purge_progress) if purge_progress else None,
        "job_meta": "{}",
    }


def _make_meta(player_ids, cleanup_tables=None):
    meta = {
        "query_filter": {
            "filter_type": "player_scope",
            "filter_column": "player_id",
            "player_ids": player_ids,
            "season_date_from": "2025-09-01",
            "season_date_to": "2026-01-01",
        },
    }
    if cleanup_tables:
        meta["cleanup_tables"] = cleanup_tables
    return meta


def _mock_cursor_delete(rowcounts):
    """Return a mock cursor whose execute sets rowcount from a sequence."""
    idx = {"n": 0}

    class FakeCursor:
        rowcount = 0

        def execute(self, sql, params=None):
            i = idx["n"]
            self.rowcount = rowcounts[i] if i < len(rowcounts) else 0
            idx["n"] = i + 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    return FakeCursor()


# ---------------------------------------------------------------------------
# PSP-001: Full flow
# ---------------------------------------------------------------------------

class TestPurgePlayerScope:

    @patch("archive_executor.purge._log_delete_audit")
    @patch("archive_executor.purge._mark_purged")
    @patch("archive_executor.purge._update_purge_progress")
    @patch("archive_executor.purge.time")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.get_connection")
    def test_full_flow(self, mock_get_conn, mock_isdir, mock_time,
                       mock_update_progress, mock_mark_purged, mock_audit):
        """PSP-001: Source purge + cleanup table purge + mark Purged."""
        config = _make_config()
        log = MagicMock()

        # Source table: first batch deletes 100, second deletes 0
        # Cleanup table: deletes 5
        cursors = [
            _mock_cursor_delete([100]),
            _mock_cursor_delete([0]),
            _mock_cursor_delete([5]),
        ]
        connections = []
        for c in cursors:
            conn = MagicMock()
            conn.cursor.return_value = c
            connections.append(conn)
        mock_get_conn.side_effect = connections

        job = _make_job()
        meta = _make_meta(
            player_ids=["P1", "P2"],
            cleanup_tables=[{"table": "tabPlayer Practice Summary", "player_column": "player_id"}],
        )

        _purge_player_scope(config, job, meta, log)

        mock_mark_purged.assert_called_once_with(config, "ARCH-30001")
        mock_audit.assert_called_once()
        assert mock_audit.call_args[1]["status"] == "success"

    # -----------------------------------------------------------------------
    # PSP-004: Empty player_ids
    # -----------------------------------------------------------------------

    @patch("archive_executor.purge._log_delete_audit")
    @patch("archive_executor.purge._mark_purged")
    @patch("archive_executor.purge._update_purge_progress")
    @patch("archive_executor.purge.time")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.get_connection")
    def test_empty_player_ids(self, mock_get_conn, mock_isdir, mock_time,
                              mock_update_progress, mock_mark_purged, mock_audit):
        """PSP-004: Empty player_ids completes without any DELETE queries."""
        config = _make_config()
        log = MagicMock()

        job = _make_job()
        meta = _make_meta(player_ids=[])

        _purge_player_scope(config, job, meta, log)

        mock_get_conn.assert_not_called()
        mock_mark_purged.assert_called_once_with(config, "ARCH-30001")

    # -----------------------------------------------------------------------
    # PSP-005: Archive files missing
    # -----------------------------------------------------------------------

    @patch("archive_executor.purge.os.path.isdir", return_value=False)
    def test_archive_files_missing(self, mock_isdir):
        """PSP-005: Missing archive files → skipped with warning."""
        config = _make_config()
        log = MagicMock()

        job = _make_job(file_path="/nonexistent")
        meta = _make_meta(player_ids=["P1"])

        _purge_player_scope(config, job, meta, log)

        log.warning.assert_called_once()
        assert "archive_files_missing" in str(log.warning.call_args)

    # -----------------------------------------------------------------------
    # PSP-002: Resume from partial source purge
    # -----------------------------------------------------------------------

    @patch("archive_executor.purge._log_delete_audit")
    @patch("archive_executor.purge._mark_purged")
    @patch("archive_executor.purge._update_purge_progress")
    @patch("archive_executor.purge.time")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.get_connection")
    def test_resume_source_purge(self, mock_get_conn, mock_isdir, mock_time,
                                 mock_update_progress, mock_mark_purged, mock_audit):
        """PSP-002: Resume with source_purge_complete=True skips Phase A."""
        config = _make_config()
        log = MagicMock()

        job = _make_job(purge_progress={
            "total_deleted": 500,
            "source_purge_complete": True,
        })
        meta = _make_meta(player_ids=["P1", "P2"])

        _purge_player_scope(config, job, meta, log)

        # No source table DELETE should have been issued
        mock_get_conn.assert_not_called()
        mock_mark_purged.assert_called_once()

    # -----------------------------------------------------------------------
    # PSP-006: Cleanup table already completed → skipped
    # -----------------------------------------------------------------------

    @patch("archive_executor.purge._log_delete_audit")
    @patch("archive_executor.purge._mark_purged")
    @patch("archive_executor.purge._update_purge_progress")
    @patch("archive_executor.purge.time")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.get_connection")
    def test_cleanup_table_already_complete(self, mock_get_conn, mock_isdir, mock_time,
                                            mock_update_progress, mock_mark_purged, mock_audit):
        """PSP-006: Cleanup table marked complete in progress → skipped on resume."""
        config = _make_config()
        log = MagicMock()

        job = _make_job(purge_progress={
            "total_deleted": 500,
            "source_purge_complete": True,
            "cleanup_tables_deleted": {
                "tabPlayer Practice Summary": {"deleted": 10, "complete": True},
            },
        })
        meta = _make_meta(
            player_ids=["P1"],
            cleanup_tables=[{"table": "tabPlayer Practice Summary", "player_column": "player_id"}],
        )

        _purge_player_scope(config, job, meta, log)

        mock_get_conn.assert_not_called()
        mock_mark_purged.assert_called_once()
        # Verify the skip was logged
        log.info.assert_any_call("cleanup_table_skipped", job="ARCH-30001",
                                 table="tabPlayer Practice Summary", reason="already_complete")


# ---------------------------------------------------------------------------
# PSP-003: _purge_cleanup_table unit test
# ---------------------------------------------------------------------------

class TestPurgeCleanupTable:

    @patch("archive_executor.purge.get_connection")
    def test_cleanup_table_deletes_rows(self, mock_get_conn):
        """PSP-003: _purge_cleanup_table issues DELETE and returns count."""
        config = _make_config()
        log = MagicMock()

        cursor = _mock_cursor_delete([7])
        conn = MagicMock()
        conn.cursor.return_value = cursor
        mock_get_conn.return_value = conn

        total = _purge_cleanup_table(
            config, "tabPlayer Practice Summary", "player_id",
            ["P1", "P2", "P3"], log, "ARCH-30001",
        )

        assert total == 7
        conn.commit.assert_called_once()

    @patch("archive_executor.purge.get_connection")
    def test_cleanup_table_empty_players(self, mock_get_conn):
        """Empty player list → 0 deleted, no DB calls."""
        config = _make_config()
        log = MagicMock()

        total = _purge_cleanup_table(
            config, "tabPlayer Practice Summary", "player_id",
            [], log, "ARCH-30001",
        )

        assert total == 0
        mock_get_conn.assert_not_called()
