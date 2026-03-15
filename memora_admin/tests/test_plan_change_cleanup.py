"""Unit tests for plan change Practice Log + Practice Summary deletion.

Verifies that execute_plan_change() issues DELETE queries for both
tabMemora Practice Log and tabPlayer Practice Summary (steps 8c, 8d).

Run with:
    python3 -m pytest memora_admin/tests/test_plan_change_cleanup.py -v
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Frappe mock setup
# ---------------------------------------------------------------------------

def _install_frappe_mock():
    if "frappe" in sys.modules and hasattr(sys.modules["frappe"], "get_all"):
        return

    _frappe_utils = types.ModuleType("frappe.utils")
    _frappe_utils.today = lambda: "2026-03-15"
    _frappe_utils.now_datetime = datetime.now

    _frappe = types.ModuleType("frappe")
    _frappe.utils = _frappe_utils
    _frappe.db = MagicMock()
    _frappe.get_all = MagicMock(return_value=[])
    _frappe.get_doc = MagicMock()
    _frappe.log_error = MagicMock()
    _frappe.logger = MagicMock(return_value=MagicMock())
    _frappe.whitelist = lambda **kw: (lambda fn: fn)

    sys.modules["frappe"] = _frappe
    sys.modules["frappe.utils"] = _frappe_utils


_install_frappe_mock()

import frappe


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_mocks():
    frappe.db.reset_mock(side_effect=True)
    frappe.get_doc.reset_mock(side_effect=True)
    yield


def _get_value_side_effect(doctype, name_or_filters, fields=None, as_dict=None):
    """Side effect for frappe.db.get_value that returns appropriate data."""
    from types import SimpleNamespace

    if doctype == "Memora Academic Plan":
        return SimpleNamespace(
            name="PLAN-NEW",
            plan_name="Test Plan",
            grade="GRADE-001",
            major="MAJOR-001",
            season="SEAS-ACTIVE",
            is_published=1,
        )
    if doctype == "Memora Season":
        if name_or_filters == "SEAS-ACTIVE":
            return SimpleNamespace(
                name="SEAS-ACTIVE",
                season_title="Active Season",
                end_date="2026-12-31",
                is_published=1,
            )
        if name_or_filters == "SEAS-OLD":
            return SimpleNamespace(end_date="2026-12-31")
        return SimpleNamespace(end_date="2026-12-31")
    if doctype == "Memora Player Profile":
        return SimpleNamespace(
            name="PLYR-001",
            plan="PLAN-OLD",
            grade="GRADE-OLD",
            major="MAJOR-OLD",
            season="SEAS-OLD",
        )
    if doctype == "Memora Player Wallet":
        return SimpleNamespace(
            name="WALLET-001",
            total_xp=100,
            current_streak=5,
            total_lessons=10,
            total_time_min=60,
        )
    return None


def _setup_valid_plan_change():
    """Configure frappe mocks for a valid plan change scenario."""
    # No cooldown
    frappe.db.sql.return_value = []
    frappe.db.get_value.side_effect = _get_value_side_effect

    # History doc
    history_mock = MagicMock()
    history_mock.name = "PCH-001"
    frappe.get_doc.return_value = history_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPlanChangeCleanup:
    """Tests for Practice Log and Practice Summary deletion during plan change."""

    def test_practice_log_deleted_on_plan_change(self):
        """Step 8c: DELETE FROM tabMemora Practice Log WHERE player_id = %s."""
        _setup_valid_plan_change()

        sql_calls = []

        def capture_sql(query, values=None, **kwargs):
            sql_calls.append((query.strip() if isinstance(query, str) else query, values))
            if "ROW_COUNT" in str(query):
                return [(0,)]
            if "tabMemora Player Plan History" in str(query):
                return []
            if "COUNT" in str(query):
                return [(0,)]
            return []

        frappe.db.sql.side_effect = capture_sql

        from memora_admin.api.plan_change import execute_plan_change

        result = execute_plan_change("PLYR-001", "PLAN-NEW")

        practice_log_deletes = [
            c for c in sql_calls
            if "tabMemora Practice Log" in str(c[0]) and "DELETE" in str(c[0]).upper()
        ]
        assert len(practice_log_deletes) == 1, (
            f"Expected 1 DELETE from tabMemora Practice Log, got {len(practice_log_deletes)}"
        )
        assert practice_log_deletes[0][1] == ("PLYR-001",)

    def test_practice_summary_deleted_on_plan_change(self):
        """Step 8d: DELETE FROM tabPlayer Practice Summary WHERE player_id = %s."""
        _setup_valid_plan_change()

        sql_calls = []

        def capture_sql(query, values=None, **kwargs):
            sql_calls.append((query.strip() if isinstance(query, str) else query, values))
            if "ROW_COUNT" in str(query):
                return [(0,)]
            if "tabMemora Player Plan History" in str(query):
                return []
            if "COUNT" in str(query):
                return [(0,)]
            return []

        frappe.db.sql.side_effect = capture_sql

        from memora_admin.api.plan_change import execute_plan_change

        result = execute_plan_change("PLYR-001", "PLAN-NEW")

        summary_deletes = [
            c for c in sql_calls
            if "tabPlayer Practice Summary" in str(c[0]) and "DELETE" in str(c[0]).upper()
        ]
        assert len(summary_deletes) == 1, (
            f"Expected 1 DELETE from tabPlayer Practice Summary, got {len(summary_deletes)}"
        )
        assert summary_deletes[0][1] == ("PLYR-001",)

    def test_both_deletes_happen_after_memory_state(self):
        """Steps 8c and 8d happen after 8b (Memory State deletion)."""
        _setup_valid_plan_change()

        sql_calls = []

        def capture_sql(query, values=None, **kwargs):
            sql_calls.append(query.strip() if isinstance(query, str) else str(query))
            if "ROW_COUNT" in str(query):
                return [(0,)]
            if "tabMemora Player Plan History" in str(query):
                return []
            if "COUNT" in str(query):
                return [(0,)]
            return []

        frappe.db.sql.side_effect = capture_sql

        from memora_admin.api.plan_change import execute_plan_change

        execute_plan_change("PLYR-001", "PLAN-NEW")

        # Find positions
        delete_queries = [q for q in sql_calls if "DELETE" in q.upper()]
        memory_state_idx = None
        practice_log_idx = None
        practice_summary_idx = None

        for i, q in enumerate(delete_queries):
            if "tabMemora Memory State" in q:
                memory_state_idx = i
            elif "tabMemora Practice Log" in q:
                practice_log_idx = i
            elif "tabPlayer Practice Summary" in q:
                practice_summary_idx = i

        # 8c and 8d come after 8b
        if memory_state_idx is not None:
            assert practice_log_idx is not None and practice_log_idx > memory_state_idx
            assert practice_summary_idx is not None and practice_summary_idx > memory_state_idx
