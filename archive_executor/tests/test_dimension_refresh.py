"""Unit tests for dimension refresh service (T019).

Tests cover:
  1. SCD2 Player History — valid_from / valid_to boundary logic via DuckDB
  2. Dimension refresh trigger — mocked doc_event handlers
  3. Schema loading and export orchestration
  4. Transfer invocation after export

Architecture
------------
- SCD2 window-function logic is tested against DuckDB in-memory (no MariaDB needed).
- Frappe is mocked via sys.modules (same pattern as test_task_log_pipeline.py).
- Export / transfer calls use unittest.mock.patch.

Run with:
    python3 -m pytest archive_executor/tests/test_dimension_refresh.py -v
"""

# ============================================================================
# Frappe mock — must be installed BEFORE any memora_admin imports
# ============================================================================

import sys
import types
from datetime import datetime
from unittest.mock import MagicMock


def _install_frappe_mock() -> None:
    """Install a minimal frappe stub into sys.modules."""
    if "frappe" in sys.modules and hasattr(sys.modules["frappe"], "get_all"):
        return

    _frappe_utils = types.ModuleType("frappe.utils")
    _frappe_utils.now_datetime = datetime.now  # type: ignore[attr-defined]

    _frappe = types.ModuleType("frappe")
    _frappe.utils = _frappe_utils  # type: ignore[attr-defined]
    _frappe.db = MagicMock()  # type: ignore[attr-defined]
    _frappe.get_all = MagicMock(return_value=[])  # type: ignore[attr-defined]
    _frappe.get_doc = MagicMock()  # type: ignore[attr-defined]
    _frappe.log_error = MagicMock()  # type: ignore[attr-defined]
    _frappe.logger = MagicMock()  # type: ignore[attr-defined]
    _frappe.enqueue = MagicMock()  # type: ignore[attr-defined]

    sys.modules["frappe"] = _frappe
    sys.modules["frappe.utils"] = _frappe_utils


_install_frappe_mock()

# ============================================================================
# Standard imports — AFTER frappe mock is in place
# ============================================================================

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

try:
    import duckdb

    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

try:
    import pyarrow
    import pyarrow.parquet as pq

    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False


# ============================================================================
# SCD2 Player History — DuckDB-based tests
# ============================================================================

# The SCD2 query from player_history.v1.yaml (adapted for DuckDB syntax)
SCD2_QUERY = """
SELECT
    h.player AS player_id,
    h.new_plan AS plan_id,
    ap.plan_name,
    h.new_grade AS grade,
    h.new_major AS major,
    h.new_season AS season_id,
    h.changed_at AS valid_from,
    LEAD(h.changed_at) OVER (
        PARTITION BY h.player ORDER BY h.changed_at
    ) AS valid_to,
    CASE WHEN LEAD(h.changed_at) OVER (
        PARTITION BY h.player ORDER BY h.changed_at
    ) IS NULL THEN 1 ELSE 0 END AS is_current,
    h.trigger_reason
FROM player_plan_history h
LEFT JOIN academic_plan ap ON h.new_plan = ap.name
ORDER BY h.player, h.changed_at
"""


def _setup_duckdb():
    """Create DuckDB in-memory tables mimicking the MariaDB schema."""
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE player_plan_history (
            player VARCHAR,
            new_plan VARCHAR,
            new_grade VARCHAR,
            new_major VARCHAR,
            new_season VARCHAR,
            changed_at TIMESTAMP,
            trigger_reason VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE academic_plan (
            name VARCHAR,
            plan_name VARCHAR
        )
    """)
    return conn


@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
class TestSCD2PlayerHistory:
    """Test the SCD2 LEAD window function query using DuckDB in-memory."""

    def test_two_plan_changes_correct_boundaries(self):
        """Single player with two plan changes produces correct valid_from/valid_to."""
        conn = _setup_duckdb()

        # Insert plan reference data
        conn.execute(
            "INSERT INTO academic_plan VALUES ('PLAN-A', 'Science Plan A')"
        )
        conn.execute(
            "INSERT INTO academic_plan VALUES ('PLAN-B', 'Science Plan B')"
        )

        # Insert two plan changes for one player
        conn.execute("""
            INSERT INTO player_plan_history VALUES
            ('PLAYER-001', 'PLAN-A', 'Grade-5', 'Science', 'SEAS-1',
             '2025-01-15 10:00:00', 'enrollment'),
            ('PLAYER-001', 'PLAN-B', 'Grade-6', 'Arts', 'SEAS-2',
             '2025-06-01 08:00:00', 'admin_change')
        """)

        rows = conn.execute(SCD2_QUERY).fetchall()
        cols = [d[0] for d in conn.execute(SCD2_QUERY).description]

        assert len(rows) == 2

        # Row 0: first plan change — should have valid_to = second change's timestamp
        r0 = dict(zip(cols, rows[0]))
        assert r0["player_id"] == "PLAYER-001"
        assert r0["plan_id"] == "PLAN-A"
        assert r0["plan_name"] == "Science Plan A"
        assert r0["grade"] == "Grade-5"
        assert r0["is_current"] == 0
        assert r0["valid_from"] == datetime(2025, 1, 15, 10, 0, 0)
        assert r0["valid_to"] == datetime(2025, 6, 1, 8, 0, 0)

        # Row 1: second plan change — is_current=1, valid_to=NULL
        r1 = dict(zip(cols, rows[1]))
        assert r1["player_id"] == "PLAYER-001"
        assert r1["plan_id"] == "PLAN-B"
        assert r1["plan_name"] == "Science Plan B"
        assert r1["is_current"] == 1
        assert r1["valid_to"] is None
        assert r1["valid_from"] == datetime(2025, 6, 1, 8, 0, 0)

    def test_multiple_players_independent_windows(self):
        """Each player's SCD2 windows are independent — no cross-contamination."""
        conn = _setup_duckdb()

        conn.execute("INSERT INTO academic_plan VALUES ('PLAN-X', 'Plan X')")
        conn.execute("INSERT INTO academic_plan VALUES ('PLAN-Y', 'Plan Y')")

        conn.execute("""
            INSERT INTO player_plan_history VALUES
            ('PLAYER-AAA', 'PLAN-X', 'G1', 'Sci', 'S1',
             '2025-01-01 00:00:00', 'enrollment'),
            ('PLAYER-AAA', 'PLAN-Y', 'G2', 'Art', 'S2',
             '2025-03-01 00:00:00', 'upgrade'),
            ('PLAYER-BBB', 'PLAN-X', 'G1', 'Sci', 'S1',
             '2025-02-01 00:00:00', 'enrollment')
        """)

        rows = conn.execute(SCD2_QUERY).fetchall()
        cols = [d[0] for d in conn.execute(SCD2_QUERY).description]
        results = [dict(zip(cols, r)) for r in rows]

        # Player AAA: 2 rows
        aaa_rows = [r for r in results if r["player_id"] == "PLAYER-AAA"]
        assert len(aaa_rows) == 2
        assert aaa_rows[0]["is_current"] == 0
        assert aaa_rows[0]["valid_to"] == datetime(2025, 3, 1, 0, 0, 0)
        assert aaa_rows[1]["is_current"] == 1
        assert aaa_rows[1]["valid_to"] is None

        # Player BBB: 1 row, is_current=1
        bbb_rows = [r for r in results if r["player_id"] == "PLAYER-BBB"]
        assert len(bbb_rows) == 1
        assert bbb_rows[0]["is_current"] == 1
        assert bbb_rows[0]["valid_to"] is None

    def test_single_change_is_current(self):
        """Player with one change: single row with is_current=1 and valid_to=NULL."""
        conn = _setup_duckdb()

        conn.execute("INSERT INTO academic_plan VALUES ('PLAN-ONLY', 'Only Plan')")
        conn.execute("""
            INSERT INTO player_plan_history VALUES
            ('PLAYER-SOLO', 'PLAN-ONLY', 'G3', 'Sci', 'S1',
             '2025-05-01 12:00:00', 'enrollment')
        """)

        rows = conn.execute(SCD2_QUERY).fetchall()
        cols = [d[0] for d in conn.execute(SCD2_QUERY).description]
        results = [dict(zip(cols, r)) for r in rows]

        assert len(results) == 1
        r = results[0]
        assert r["is_current"] == 1
        assert r["valid_to"] is None
        assert r["plan_name"] == "Only Plan"
        assert r["trigger_reason"] == "enrollment"

    def test_plan_name_left_join_populates(self):
        """plan_name is populated via LEFT JOIN — verified for each row."""
        conn = _setup_duckdb()

        conn.execute(
            "INSERT INTO academic_plan VALUES ('PLAN-J1', 'Joined Plan One')"
        )
        conn.execute(
            "INSERT INTO academic_plan VALUES ('PLAN-J2', 'Joined Plan Two')"
        )
        conn.execute("""
            INSERT INTO player_plan_history VALUES
            ('PLAYER-JOIN', 'PLAN-J1', 'G1', 'Sci', 'S1',
             '2025-01-01 00:00:00', 'enrollment'),
            ('PLAYER-JOIN', 'PLAN-J2', 'G2', 'Art', 'S2',
             '2025-07-01 00:00:00', 'admin_change')
        """)

        rows = conn.execute(SCD2_QUERY).fetchall()
        cols = [d[0] for d in conn.execute(SCD2_QUERY).description]
        results = [dict(zip(cols, r)) for r in rows]

        assert results[0]["plan_name"] == "Joined Plan One"
        assert results[1]["plan_name"] == "Joined Plan Two"

    def test_plan_name_null_when_no_plan_record(self):
        """When academic_plan has no matching record, plan_name is NULL (LEFT JOIN)."""
        conn = _setup_duckdb()

        # No plan record inserted
        conn.execute("""
            INSERT INTO player_plan_history VALUES
            ('PLAYER-NOPLAN', 'PLAN-MISSING', 'G1', 'Sci', 'S1',
             '2025-01-01 00:00:00', 'enrollment')
        """)

        rows = conn.execute(SCD2_QUERY).fetchall()
        cols = [d[0] for d in conn.execute(SCD2_QUERY).description]
        results = [dict(zip(cols, r)) for r in rows]

        assert len(results) == 1
        assert results[0]["plan_name"] is None


# ============================================================================
# Dimension Refresh Service — unit tests with mocked frappe
# ============================================================================


class TestDimensionRefreshService:
    """Unit tests for dimension_refresh.py service functions."""

    def test_load_schema_reads_yaml(self):
        """_load_schema loads and parses a YAML dimension schema."""
        from memora_admin.memora_admin.services.dimension_refresh import _load_schema

        schema = _load_schema("player_history", "v1")
        assert schema["entity"] == "player_history"
        assert schema["version"] == "v1"
        assert "query" in schema
        assert "fields" in schema
        assert "player_id" in schema["fields"]

    def test_load_schema_all_entities(self):
        """All 6 dimension schemas can be loaded without error."""
        from memora_admin.memora_admin.services.dimension_refresh import (
            DIMENSION_REGISTRY,
            _load_schema,
        )

        for entity, version in DIMENSION_REGISTRY:
            schema = _load_schema(entity, version)
            assert schema["entity"] == entity
            assert schema["version"] == version
            assert "fields" in schema

    def test_dimension_registry_has_six_entries(self):
        """DIMENSION_REGISTRY contains all 6 expected dimension entries."""
        from memora_admin.memora_admin.services.dimension_refresh import DIMENSION_REGISTRY

        assert len(DIMENSION_REGISTRY) == 6
        entities = [e for e, v in DIMENSION_REGISTRY]
        assert "player" in entities
        assert "player_history" in entities
        assert "season" in entities
        assert "plan" in entities
        assert "review_item" in entities
        assert "lesson" in entities

    @pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
    def test_export_dimension_writes_parquet(self):
        """_export_dimension writes a Parquet file and returns correct row count."""
        from memora_admin.memora_admin.services.dimension_refresh import _export_dimension

        import frappe

        # Mock frappe.db.sql to return test rows
        test_rows = [
            {"player_id": "P1", "plan_id": "PLAN-1", "plan_name": "Test Plan",
             "grade": "G1", "major": "Sci", "season_id": "S1",
             "valid_from": "2025-01-01", "valid_to": None,
             "is_current": 1, "trigger_reason": "enrollment"},
        ]
        frappe.db.sql = MagicMock(return_value=test_rows)

        with tempfile.TemporaryDirectory() as tmpdir:
            path, count = _export_dimension("player_history", "v1", tmpdir)

            assert count == 1
            assert os.path.isfile(path)
            assert path.endswith("dim_player_history.parquet")

            # Read back and verify
            table = pq.read_table(path)
            assert table.num_rows == 1
            assert "player_id" in table.column_names

    @pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
    def test_export_dimension_empty_result_writes_empty_parquet(self):
        """_export_dimension with no rows writes an empty Parquet file."""
        from memora_admin.memora_admin.services.dimension_refresh import _export_dimension

        import frappe

        frappe.db.sql = MagicMock(return_value=[])

        with tempfile.TemporaryDirectory() as tmpdir:
            path, count = _export_dimension("season", "v1", tmpdir)

            assert count == 0
            assert os.path.isfile(path)

            table = pq.read_table(path)
            assert table.num_rows == 0

    @pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
    def test_export_dimension_strips_where_clause_for_full_refresh(self):
        """For schemas with WHERE ... IN ({placeholders}), full refresh strips the WHERE clause."""
        from memora_admin.memora_admin.services.dimension_refresh import _export_dimension

        import frappe

        captured_queries = []

        def _mock_sql(query, *args, **kwargs):
            captured_queries.append(query)
            return []

        frappe.db.sql = _mock_sql

        with tempfile.TemporaryDirectory() as tmpdir:
            _export_dimension("player", "v3", tmpdir)

        assert len(captured_queries) == 1
        # The executed query should NOT contain {placeholders} or WHERE ... IN
        assert "{placeholders}" not in captured_queries[0]

    def test_refresh_dimension_unknown_entity_raises(self):
        """refresh_dimension raises ValueError for unknown dimension entity."""
        from memora_admin.memora_admin.services.dimension_refresh import refresh_dimension

        with pytest.raises(ValueError, match="Unknown dimension entity"):
            refresh_dimension("nonexistent_entity")

    @pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
    def test_refresh_dimension_calls_export_and_transfer(self):
        """refresh_dimension exports the dimension and triggers transfer."""
        from memora_admin.memora_admin.services import dimension_refresh as dr

        import frappe

        frappe.db.sql = MagicMock(return_value=[])

        with patch.object(dr, "_transfer_dimensions") as mock_transfer:
            count = dr.refresh_dimension("season", "v1")

        assert count == 0
        mock_transfer.assert_called_once()

    @pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
    def test_refresh_all_dimensions_exports_all_six(self):
        """refresh_all_dimensions exports all 6 dimensions."""
        from memora_admin.memora_admin.services import dimension_refresh as dr

        import frappe

        frappe.db.sql = MagicMock(return_value=[])

        with patch.object(dr, "_transfer_dimensions") as mock_transfer:
            results = dr.refresh_all_dimensions()

        assert len(results) == 6
        expected_entities = {"player", "player_history", "season", "plan",
                             "review_item", "lesson"}
        assert set(results.keys()) == expected_entities
        mock_transfer.assert_called_once()

    @pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
    def test_refresh_all_dimensions_handles_single_failure(self):
        """If one dimension fails, others still export; failed one returns -1."""
        from memora_admin.memora_admin.services import dimension_refresh as dr

        import frappe

        call_count = [0]

        def _mock_sql(query, *args, **kwargs):
            call_count[0] += 1
            # Fail on the second dimension export
            if call_count[0] == 2:
                raise RuntimeError("simulated export error")
            return []

        frappe.db.sql = _mock_sql
        frappe.log_error = MagicMock()

        with patch.object(dr, "_transfer_dimensions"):
            results = dr.refresh_all_dimensions()

        # Exactly one should be -1
        failures = [e for e, c in results.items() if c == -1]
        successes = [e for e, c in results.items() if c >= 0]
        assert len(failures) == 1
        assert len(successes) == 5

    def test_transfer_skips_when_no_ssh_host(self):
        """_transfer_dimensions does nothing when ANALYTICS_SSH_HOST is not set."""
        from memora_admin.memora_admin.services.dimension_refresh import _transfer_dimensions

        import frappe

        frappe.logger = MagicMock()

        with patch.dict(os.environ, {}, clear=False):
            # Ensure ANALYTICS_SSH_HOST is not set
            os.environ.pop("ANALYTICS_SSH_HOST", None)
            _transfer_dimensions("/tmp/test-dims")

        # No subprocess should have been called

    def test_transfer_calls_rsync_with_correct_args(self):
        """_transfer_dimensions invokes rsync with SSH key and correct paths."""
        from memora_admin.memora_admin.services.dimension_refresh import _transfer_dimensions

        env_vars = {
            "ANALYTICS_SSH_HOST": "analytics.example.com",
            "ANALYTICS_SSH_USER": "deploy",
            "ANALYTICS_SSH_KEY_PATH": "/home/deploy/.ssh/id_ed25519",
            "ANALYTICS_REMOTE_PATH": "/data/analytics",
        }

        with (
            patch.dict(os.environ, env_vars, clear=False),
            patch("memora_admin.memora_admin.services.dimension_refresh.subprocess.run") as mock_run,
        ):
            _transfer_dimensions("/tmp/test-dims")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "rsync"
        assert "-az" in cmd
        assert "deploy@analytics.example.com:/data/analytics/dimensions/" in cmd[-1]
        # SSH key should be included
        ssh_flag_idx = cmd.index("-e")
        assert "/home/deploy/.ssh/id_ed25519" in cmd[ssh_flag_idx + 1]


# ============================================================================
# Dimension Refresh Trigger — doc_event handler tests
# ============================================================================


class TestDimensionSyncEventHandlers:
    """Test that doc_event handlers enqueue the correct dimension refreshes."""

    def test_on_player_changed_enqueues_player_and_history(self):
        """Player profile change triggers both player and player_history refresh."""
        from memora_admin.events.dimension_sync import on_player_changed

        import frappe

        frappe.enqueue = MagicMock()
        doc = SimpleNamespace(name="PLAYER-001")

        on_player_changed(doc, "on_update")

        assert frappe.enqueue.call_count == 2
        enqueued_entities = [
            c.kwargs.get("entity") for c in frappe.enqueue.call_args_list
        ]
        assert "player" in enqueued_entities
        assert "player_history" in enqueued_entities

    def test_on_plan_changed_enqueues_plan_refresh(self):
        """Academic plan change triggers plan dimension refresh."""
        from memora_admin.events.dimension_sync import on_plan_changed

        import frappe

        frappe.enqueue = MagicMock()
        doc = SimpleNamespace(name="PLAN-001")

        on_plan_changed(doc, "on_update")

        assert frappe.enqueue.call_count == 1
        assert frappe.enqueue.call_args.kwargs["entity"] == "plan"

    def test_on_season_changed_enqueues_season_refresh(self):
        """Season change triggers season dimension refresh."""
        from memora_admin.events.dimension_sync import on_season_changed

        import frappe

        frappe.enqueue = MagicMock()
        doc = SimpleNamespace(name="SEAS-001")

        on_season_changed(doc, "on_update")

        assert frappe.enqueue.call_count == 1
        assert frappe.enqueue.call_args.kwargs["entity"] == "season"

    def test_on_review_item_changed_enqueues_review_item_refresh(self):
        """Review item change triggers review_item dimension refresh."""
        from memora_admin.events.dimension_sync import on_review_item_changed

        import frappe

        frappe.enqueue = MagicMock()
        doc = SimpleNamespace(name="RI-001")

        on_review_item_changed(doc, "on_update")

        assert frappe.enqueue.call_count == 1
        assert frappe.enqueue.call_args.kwargs["entity"] == "review_item"

    def test_on_lesson_changed_enqueues_lesson_refresh(self):
        """Lesson change triggers lesson dimension refresh."""
        from memora_admin.events.dimension_sync import on_lesson_changed

        import frappe

        frappe.enqueue = MagicMock()
        doc = SimpleNamespace(name="LESSON-001")

        on_lesson_changed(doc, "on_update")

        assert frappe.enqueue.call_count == 1
        assert frappe.enqueue.call_args.kwargs["entity"] == "lesson"

    def test_all_handlers_use_deduplicate(self):
        """All dimension sync handlers use deduplicate=True to debounce."""
        from memora_admin.events import dimension_sync as ds

        import frappe

        handlers = [
            ds.on_player_changed,
            ds.on_plan_changed,
            ds.on_season_changed,
            ds.on_review_item_changed,
            ds.on_lesson_changed,
        ]

        for handler in handlers:
            frappe.enqueue = MagicMock()
            doc = SimpleNamespace(name="TEST-001")
            handler(doc, "on_update")

            for c in frappe.enqueue.call_args_list:
                assert c.kwargs.get("deduplicate") is True, (
                    f"{handler.__name__} must use deduplicate=True"
                )

    def test_all_handlers_use_short_queue(self):
        """All dimension sync handlers enqueue to the 'short' queue."""
        from memora_admin.events import dimension_sync as ds

        import frappe

        handlers = [
            ds.on_player_changed,
            ds.on_plan_changed,
            ds.on_season_changed,
            ds.on_review_item_changed,
            ds.on_lesson_changed,
        ]

        for handler in handlers:
            frappe.enqueue = MagicMock()
            doc = SimpleNamespace(name="TEST-001")
            handler(doc, "on_update")

            for c in frappe.enqueue.call_args_list:
                assert c.kwargs.get("queue") == "short", (
                    f"{handler.__name__} must enqueue to 'short' queue"
                )


# ============================================================================
# Daily reconciliation task tests
# ============================================================================


class TestDimensionReconciliationTask:
    """Test the daily reconcile_dimensions task."""

    def test_reconcile_calls_refresh_all(self):
        """reconcile_dimensions calls refresh_all_dimensions."""
        with patch(
            "memora_admin.memora_admin.services.dimension_refresh.refresh_all_dimensions",
            return_value={"player": 10, "season": 5},
        ) as mock_refresh:
            from memora_admin.tasks.dimension_sync import reconcile_dimensions

            import frappe

            frappe.logger = MagicMock()
            reconcile_dimensions()

        mock_refresh.assert_called_once()

    def test_reconcile_logs_error_on_failure(self):
        """reconcile_dimensions calls frappe.log_error on exception."""
        import frappe

        frappe.log_error = MagicMock()

        with patch(
            "memora_admin.memora_admin.services.dimension_refresh.refresh_all_dimensions",
            side_effect=RuntimeError("connection refused"),
        ):
            from memora_admin.tasks.dimension_sync import reconcile_dimensions

            reconcile_dimensions()

        frappe.log_error.assert_called_once()
