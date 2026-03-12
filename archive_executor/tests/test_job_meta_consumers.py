"""Regression tests for the job_meta rename and archive trigger safeguards.

Covers the four issues fixed in review:
  1. live_sync._get_completed_archive_ranges uses job_meta column (not meta)
  2. sync._get_paused_filters requests job_meta field and reads job.job_meta
  3. archive_trigger restores is_published=0 and 90-day cutoff guards
  4. frappe.log() replaced with frappe.logger().info() in trigger and monitor

All tests are pure unit tests — no DB or Frappe bench required.
"""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from archive_executor.config import Config


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_config() -> Config:
    return Config(
        db_host="127.0.0.1", db_port=3306,
        db_user="u", db_password="p", db_name="db",
        archive_output_path="/tmp/", schema_registry_path="/tmp/schema/",
        log_path="/tmp/logs/", lock_file="/tmp/a.lock",
        chunk_size=1000, stuck_timeout_hours=1,
        ssh_host="", ssh_user="", ssh_key_path="", ssh_port=22, ssh_timeout=300,
        remote_archive_path="", remote_live_path="",
        analytics_cmd_path="", duckdb_path="",
        live_output_path="/tmp/live/", live_lock_file="/tmp/live.lock",
        sync_state_path="/tmp/sync_state/", sync_output_path="/tmp/sync_output/",
        sync_overlap_seconds=300, sync_remote_path="",
        snapshot_output_path="/tmp/snapshots/",
        remote_snapshot_path="",
        purge_grace_days=7,
    )


def _meta_json(date_from: str, date_to: str) -> str:
    return json.dumps({
        "query_filter": {
            "date_from": date_from,
            "date_to": date_to,
            "filter_column": "last_seen_at",
        }
    })


def _load_frappe_module(module_path: str, extra_mocks: dict | None = None):
    """Import a Frappe-dependent module with frappe and optional extras mocked.

    Returns (module, fake_frappe). The module is always freshly imported so
    each test gets its own isolated module globals.
    """
    fake_frappe = MagicMock()
    mocks = {"frappe": fake_frappe, **(extra_mocks or {})}

    originals = {k: sys.modules.get(k) for k in mocks}
    for k, v in mocks.items():
        sys.modules[k] = v

    try:
        sys.modules.pop(module_path, None)
        mod = importlib.import_module(module_path)
        return mod, fake_frappe
    finally:
        for k, orig in originals.items():
            if orig is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = orig


# ---------------------------------------------------------------------------
# 1. live_sync._get_completed_archive_ranges
# ---------------------------------------------------------------------------

class TestGetCompletedArchiveRanges:
    """live_sync must SELECT job_meta (not meta) and parse it correctly."""

    def _call(self, rows):
        from archive_executor.live_sync import _get_completed_archive_ranges

        cursor = MagicMock()
        cursor.__enter__ = lambda s: s
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchall.return_value = rows

        conn = MagicMock()
        conn.cursor.return_value = cursor

        with patch("archive_executor.live_sync.get_connection", return_value=conn):
            result = _get_completed_archive_ranges(_make_config(), "tabMemora Practice Log")
        return result, cursor

    def test_sql_selects_job_meta_not_meta(self):
        _, cursor = self._call([])
        sql: str = cursor.execute.call_args[0][0]
        assert "job_meta" in sql
        # 'meta' should only appear as part of 'job_meta', not as a bare column
        assert sql.replace("job_meta", "") == sql.replace("job_meta", "").replace("meta", ""), \
            "SQL must not reference bare 'meta' column"

    def test_parses_single_completed_job(self):
        ranges, _ = self._call([{"job_meta": _meta_json("2024-01-01", "2024-03-31")}])
        assert ranges == [("2024-01-01", "2024-03-31")]

    def test_parses_multiple_completed_jobs(self):
        rows = [
            {"job_meta": _meta_json("2024-01-01", "2024-03-31")},
            {"job_meta": _meta_json("2024-04-01", "2024-06-30")},
        ]
        ranges, _ = self._call(rows)
        assert len(ranges) == 2
        assert ("2024-01-01", "2024-03-31") in ranges
        assert ("2024-04-01", "2024-06-30") in ranges

    def test_skips_null_job_meta(self):
        rows = [
            {"job_meta": None},
            {"job_meta": _meta_json("2024-04-01", "2024-06-30")},
        ]
        ranges, _ = self._call(rows)
        assert ranges == [("2024-04-01", "2024-06-30")]

    def test_skips_job_meta_with_missing_dates(self):
        rows = [{"job_meta": json.dumps({"query_filter": {}})}]
        ranges, _ = self._call(rows)
        assert ranges == []

    def test_returns_empty_when_no_completed_jobs(self):
        ranges, _ = self._call([])
        assert ranges == []


# ---------------------------------------------------------------------------
# 2. sync._get_paused_filters
# ---------------------------------------------------------------------------

def _load_sync():
    extra = {
        "fastapi_app": MagicMock(),
        "fastapi_app.core": MagicMock(),
        "fastapi_app.core.constants": MagicMock(),
        "fastapi_app.core.redis_keys": MagicMock(),
        "memora_admin.utils": MagicMock(),
        "memora_admin.utils.redis_connection": MagicMock(),
    }
    return _load_frappe_module("memora_admin.tasks.sync", extra)


class TestGetPausedFilters:
    """sync._get_paused_filters must request job_meta and read job.job_meta."""

    def test_fields_arg_contains_job_meta_not_meta(self):
        mod, fake_frappe = _load_sync()
        fake_frappe.get_all.return_value = []
        mod.invalidate_paused_filters_cache()

        mod._get_paused_filters()

        call_repr = str(fake_frappe.get_all.call_args)
        assert "job_meta" in call_repr, "frappe.get_all must request 'job_meta'"
        # Ensure bare "meta" doesn't appear outside of "job_meta"
        without_job_meta = call_repr.replace("job_meta", "")
        assert '"meta"' not in without_job_meta and "'meta'" not in without_job_meta, \
            "frappe.get_all must not request bare 'meta' field"

    def test_reads_job_meta_attribute_not_meta(self):
        mod, fake_frappe = _load_sync()

        job = MagicMock()
        job.job_meta = _meta_json("2024-01-01", "2024-03-31")
        job.source_doctype = "Memora Practice Log"
        # Make .meta raise so we catch any regression back to the old attribute
        type(job).meta = PropertyMock(side_effect=AttributeError("bare meta gone"))

        fake_frappe.get_all.return_value = [job]
        mod.invalidate_paused_filters_cache()

        result = mod._get_paused_filters()

        assert len(result) == 1
        assert result[0]["date_from"] == "2024-01-01"
        assert result[0]["date_to"] == "2024-03-31"

    def test_returns_empty_list_on_frappe_exception(self):
        mod, fake_frappe = _load_sync()
        fake_frappe.get_all.side_effect = Exception("DB unavailable")
        mod.invalidate_paused_filters_cache()

        result = mod._get_paused_filters()

        assert result == []

    def test_skips_job_with_unparseable_job_meta(self):
        mod, fake_frappe = _load_sync()

        bad = MagicMock()
        bad.job_meta = "{not valid json}"
        bad.source_doctype = "Memora Practice Log"

        good = MagicMock()
        good.job_meta = _meta_json("2024-07-01", "2024-09-30")
        good.source_doctype = "Memora Practice Log"

        fake_frappe.get_all.return_value = [bad, good]
        mod.invalidate_paused_filters_cache()

        result = mod._get_paused_filters()

        assert len(result) == 1
        assert result[0]["date_from"] == "2024-07-01"


# ---------------------------------------------------------------------------
# 3. archive_trigger.check_seasons_for_archive
# ---------------------------------------------------------------------------

def _load_archive_trigger():
    mod, fake_frappe = _load_frappe_module("memora_admin.tasks.archive_trigger")
    fake_frappe.utils.today.return_value = "2024-10-15"
    fake_frappe.utils.add_days.return_value = "2024-07-17"  # today - 90d
    return mod, fake_frappe


class TestCheckSeasonsForArchive:
    """archive_trigger must enforce is_published=0, 90-day cutoff, and job_meta."""

    def test_sql_filters_is_published_zero(self):
        mod, fake_frappe = _load_archive_trigger()
        fake_frappe.db.sql.return_value = []

        mod.check_seasons_for_archive()

        sql: str = fake_frappe.db.sql.call_args[0][0]
        assert "is_published" in sql, "Query must filter on is_published"
        # The value 0 must appear in SQL or params
        params = fake_frappe.db.sql.call_args[0][1]
        assert "0" in sql or 0 in params, "is_published must be checked against 0"

    def test_sql_passes_two_date_params_for_cutoff(self):
        mod, fake_frappe = _load_archive_trigger()
        fake_frappe.db.sql.return_value = []

        mod.check_seasons_for_archive()

        params = fake_frappe.db.sql.call_args[0][1]
        assert len(params) == 2, "Query must have today + cutoff params (90-day window)"
        fake_frappe.utils.add_days.assert_called_once_with("2024-10-15", -90)

    def test_job_doc_uses_job_meta_field(self):
        mod, fake_frappe = _load_archive_trigger()

        season = MagicMock()
        season.name = "SEAS-2024-001"
        season.start_date = "2024-01-01"
        season.end_date = "2024-03-31"
        fake_frappe.db.sql.return_value = [season]
        fake_frappe.db.exists.return_value = False
        fake_frappe.get_doc.return_value = MagicMock()

        archive_type = {
            "archive_type": "practice_log",
            "source_table": "tabMemora Practice Log",
            "version": "v1",
            "fact_columns": [],
            "dimensions": [],
        }
        with patch.object(mod, "_load_archive_types", return_value=[archive_type]):
            with patch.object(mod, "_build_meta_json", return_value={"query_filter": {}}):
                mod.check_seasons_for_archive()

        assert fake_frappe.get_doc.called
        doc_data: dict = fake_frappe.get_doc.call_args[0][0]
        assert "job_meta" in doc_data, "Archive job must have 'job_meta' field"
        assert "meta" not in {k for k in doc_data if k != "job_meta"}, \
            "Archive job must not have bare 'meta' field"

    def test_does_not_call_frappe_log(self):
        mod, fake_frappe = _load_archive_trigger()
        fake_frappe.db.sql.return_value = []

        mod.check_seasons_for_archive()

        assert not fake_frappe.log.called, \
            "frappe.log() must not be called; use frappe.logger().info()"
        fake_frappe.logger.assert_called()

    def test_skips_existing_jobs(self):
        mod, fake_frappe = _load_archive_trigger()

        season = MagicMock()
        season.name = "SEAS-2024-001"
        season.start_date = "2024-01-01"
        season.end_date = "2024-03-31"
        fake_frappe.db.sql.return_value = [season]
        fake_frappe.db.exists.return_value = True  # job already exists

        archive_type = {
            "archive_type": "practice_log",
            "source_table": "tabMemora Practice Log",
            "version": "v1",
            "fact_columns": [],
            "dimensions": [],
        }
        with patch.object(mod, "_load_archive_types", return_value=[archive_type]):
            mod.check_seasons_for_archive()

        fake_frappe.get_doc.assert_not_called()

    def test_skips_season_scoped_archive_types(self):
        mod, fake_frappe = _load_archive_trigger()

        season = MagicMock()
        season.name = "SEAS-2024-001"
        season.start_date = "2024-01-01"
        season.end_date = "2024-03-31"
        fake_frappe.db.sql.return_value = [season]

        archive_type = {
            "archive_type": "memory_state",
            "source_table": "tabMemora Memory State",
            "version": "v1",
            "fact_columns": [],
            "dimensions": [],
            "trigger_mode": "season",
        }
        with patch.object(mod, "_load_archive_types", return_value=[archive_type]):
            mod.check_seasons_for_archive()

        fake_frappe.get_doc.assert_not_called()


class TestCheckSeasonScopedArchives:
    """Season-scoped trigger must create season-keyed archive jobs."""

    def test_sql_filters_is_published_zero(self):
        mod, fake_frappe = _load_archive_trigger()
        fake_frappe.db.sql.return_value = []

        mod.check_season_scoped_archives()

        sql: str = fake_frappe.db.sql.call_args[0][0]
        assert "is_published" in sql
        params = fake_frappe.db.sql.call_args[0][1]
        assert "0" in sql or 0 in params

    def test_sql_requests_season_seq(self):
        mod, fake_frappe = _load_archive_trigger()
        fake_frappe.db.sql.return_value = []

        mod.check_season_scoped_archives()

        sql: str = fake_frappe.db.sql.call_args[0][0]
        assert "season_seq" in sql

    def test_job_doc_uses_season_scope_and_filter_type(self):
        mod, fake_frappe = _load_archive_trigger()

        season = MagicMock()
        season.name = "SEAS-2024-001"
        season.season_seq = 7
        season.end_date = "2024-03-31"
        fake_frappe.db.sql.return_value = [season]
        fake_frappe.db.exists.return_value = False
        fake_frappe.get_doc.return_value = MagicMock()

        archive_type = {
            "archive_type": "memory_state",
            "source_table": "tabMemora Memory State",
            "version": "v1",
            "scope_column": "season_seq",
            "fact_columns": [],
            "dimensions": [],
            "trigger_mode": "season",
        }
        with patch.object(mod, "_load_archive_types", return_value=[archive_type]):
            mod.check_season_scoped_archives()

        doc_data: dict = fake_frappe.get_doc.call_args[0][0]
        assert doc_data["archive_scope"] == "season_7"
        assert doc_data["post_archive_action"] == "Delete"
        meta = json.loads(doc_data["job_meta"])
        assert meta["query_filter"]["filter_type"] == "season"
        assert meta["query_filter"]["season_seq"] == 7
        assert meta["query_filter"]["season_name"] == "SEAS-2024-001"

    def test_skips_non_season_scoped_archive_types(self):
        mod, fake_frappe = _load_archive_trigger()

        season = MagicMock()
        season.name = "SEAS-2024-001"
        season.season_seq = 7
        season.end_date = "2024-03-31"
        fake_frappe.db.sql.return_value = [season]

        archive_type = {
            "archive_type": "practice_log",
            "source_table": "tabMemora Practice Log",
            "version": "v1",
            "fact_columns": [],
            "dimensions": [],
        }
        with patch.object(mod, "_load_archive_types", return_value=[archive_type]):
            mod.check_season_scoped_archives()

        fake_frappe.get_doc.assert_not_called()


# ---------------------------------------------------------------------------
# 4. archive_monitor.check_archive_health — logging regression
# ---------------------------------------------------------------------------

def _load_archive_monitor():
    mod, fake_frappe = _load_frappe_module("memora_admin.tasks.archive_monitor")
    fake_frappe.utils.time_diff_in_hours.return_value = 1.0
    fake_frappe.utils.now.return_value = "2024-10-15 12:00:00"
    return mod, fake_frappe


class TestArchiveMonitorLogging:
    """archive_monitor must not call frappe.log() anywhere."""

    def test_no_alerts_does_not_call_frappe_log(self):
        mod, fake_frappe = _load_archive_monitor()
        # All DB queries return no rows → no alerts
        fake_frappe.db.sql.return_value = []

        mod.check_archive_health()  # must not raise AttributeError

        assert not fake_frappe.log.called, \
            "frappe.log() must not be called; use frappe.logger().info()"

    def test_summary_uses_frappe_logger(self):
        mod, fake_frappe = _load_archive_monitor()
        fake_frappe.db.sql.return_value = []

        mod.check_archive_health()

        fake_frappe.logger.assert_called()

    def test_alerts_present_does_not_call_frappe_log(self):
        mod, fake_frappe = _load_archive_monitor()

        # Simulate a stale job to trigger at least one alert
        stale_job = MagicMock()
        stale_job.name = "ARCH-001"
        stale_job.archive_scope = "SEAS-2024"
        stale_job.status = "Exported"
        stale_job.exported_at = "2024-10-12 08:00:00"

        def sql_side_effect(query, *args, **kwargs):
            if "Live Sync Job" in query:
                return []
            if "Exported" in query or "Transferred" in query:
                return [stale_job]
            return []

        fake_frappe.db.sql.side_effect = sql_side_effect
        fake_frappe.utils.time_diff_in_hours.return_value = 50.0

        with patch.object(mod, "_send_alerts"):
            mod.check_archive_health()

        assert not fake_frappe.log.called, \
            "frappe.log() must not be called even when alerts are present"
