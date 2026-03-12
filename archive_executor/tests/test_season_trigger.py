"""Unit tests for check_season_scoped_archives() — deduplication by schema_version.

Covers:
  TRG-001: A v2 job IS created even when a non-Failed v1 job already exists for
            the same (source_doctype, archive_scope, archive_type).  schema_version
            must be part of the uniqueness key used by the trigger.
  TRG-002: A v1 job is NOT created (skipped) when a non-Failed v1 job already
            exists with the exact same schema_version.

Frappe ORM is stubbed via sys.modules — no bench context required.

Run with:
    python3 -m pytest archive_executor/tests/test_season_trigger.py -v
"""

# ============================================================================
# Frappe mock — must be installed BEFORE any memora_admin imports
# ============================================================================

import sys
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _install_frappe_mock() -> None:
    """Install or augment a frappe stub for season-trigger tests.

    Uses the same sentinel (``get_all``) as test_task_log_pipeline.py so both
    files recognise each other's stub.  When another module has already
    installed a stub we only add the attributes our tests need (today,
    add_days) rather than replacing the whole stub.
    """
    if "frappe" in sys.modules and hasattr(sys.modules["frappe"], "get_all"):
        # Stub already present — augment with what season-trigger tests need
        _frappe = sys.modules["frappe"]
        if not hasattr(_frappe, "logger"):
            _frappe.logger = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
        utils = sys.modules.get("frappe.utils")
        if utils is not None:
            if not hasattr(utils, "today"):
                utils.today = lambda: "2026-03-12"  # type: ignore[attr-defined]
            if not hasattr(utils, "add_days"):
                utils.add_days = lambda d, n: "2025-12-12"  # type: ignore[attr-defined]
        return

    _frappe_utils = types.ModuleType("frappe.utils")
    _frappe_utils.today = lambda: "2026-03-12"  # type: ignore[attr-defined]
    _frappe_utils.add_days = lambda d, n: "2025-12-12"  # type: ignore[attr-defined]
    _frappe_utils.now_datetime = datetime.now  # type: ignore[attr-defined]

    _frappe = types.ModuleType("frappe")
    _frappe.utils = _frappe_utils  # type: ignore[attr-defined]
    _frappe.db = MagicMock()  # type: ignore[attr-defined]
    _frappe.get_all = MagicMock(return_value=[])  # type: ignore[attr-defined]
    _frappe.get_doc = MagicMock()  # type: ignore[attr-defined]
    _frappe.log_error = MagicMock()  # type: ignore[attr-defined]
    _frappe.logger = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    sys.modules["frappe"] = _frappe
    sys.modules["frappe.utils"] = _frappe_utils


_install_frappe_mock()

import frappe  # noqa: E402  (after mock install)

# ============================================================================
# Fixtures
# ============================================================================

_ARCHIVE_TYPE_V1 = {
    "archive_type": "practice_log",
    "source_table": "tabMemora Practice Log",
    "trigger_mode": "season",
    "version": "v1",
    "fact_columns": [],
    "dimensions": [],
}

_ARCHIVE_TYPE_V2 = {
    "archive_type": "practice_log",
    "source_table": "tabMemora Practice Log",
    "trigger_mode": "season",
    "version": "v2",
    "fact_columns": [],
    "dimensions": [],
}

_ENDED_SEASON = SimpleNamespace(name="Season-TRG", season_seq=99, end_date="2026-01-01")


@pytest.fixture(autouse=True)
def reset_frappe_mocks():
    """Reset all frappe mock call counts before each test."""
    frappe.db.reset_mock()
    frappe.get_doc.reset_mock()
    frappe.log_error.reset_mock()
    yield


# ============================================================================
# TRG-001: v2 job created when v1 exists
# ============================================================================

def test_v2_job_created_when_v1_exists():
    """A v2 job must NOT be skipped just because a non-Failed v1 job exists.

    TRG-001: schema_version is included in the uniqueness check — the trigger
    must call frappe.db.exists() with schema_version=v2 and, finding no match,
    insert a new job doc.
    """
    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    # DB returns one ended season; exists() says no v2 job yet
    frappe.db.sql.return_value = [_ENDED_SEASON]
    frappe.db.exists.return_value = None  # v2 job does not exist

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_V2],
    ):
        check_season_scoped_archives()

    # exists() must have been called with schema_version included
    frappe.db.exists.assert_called_once_with(
        "Memora Archive Job",
        {
            "source_doctype": "Memora Practice Log",
            "archive_scope": "season_99",
            "archive_type": "practice_log",
            "schema_version": "v2",
            "status": ["!=", "Failed"],
        },
    )

    # A new job doc must have been inserted
    job_mock.insert.assert_called_once_with(ignore_permissions=True)


# ============================================================================
# TRG-002: same-version job is skipped
# ============================================================================

def test_same_version_job_is_skipped():
    """When a non-Failed job with the same schema_version already exists it is skipped.

    TRG-002: basic deduplication — the trigger must not create a second identical job.
    """
    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    frappe.db.sql.return_value = [_ENDED_SEASON]
    # exists() returns a truthy value — v1 job already present
    frappe.db.exists.return_value = "ARCH-TRG-001"

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_V1],
    ):
        check_season_scoped_archives()

    # exists() must have been called with schema_version=v1
    frappe.db.exists.assert_called_once_with(
        "Memora Archive Job",
        {
            "source_doctype": "Memora Practice Log",
            "archive_scope": "season_99",
            "archive_type": "practice_log",
            "schema_version": "v1",
            "status": ["!=", "Failed"],
        },
    )

    # No new job must be inserted
    job_mock.insert.assert_not_called()


# ============================================================================
# TRG-003: both versions co-exist when v1 exists but v2 does not
# ============================================================================

def test_v1_skipped_v2_created_in_same_run():
    """When a run sees two archive types (v1 and v2) and v1 already has a job,
    the trigger skips v1 and creates only v2.

    TRG-003: verifies the per-version deduplication within a single trigger run.
    """
    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    frappe.db.sql.return_value = [_ENDED_SEASON]

    def _exists_side_effect(doctype, filters):
        if filters.get("schema_version") == "v1":
            return "ARCH-TRG-002"  # v1 job exists
        return None  # v2 job does not exist

    frappe.db.exists.side_effect = _exists_side_effect

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_V1, _ARCHIVE_TYPE_V2],
    ):
        check_season_scoped_archives()

    # exists() called twice — once per version
    assert frappe.db.exists.call_count == 2

    # insert() called exactly once — only for v2
    job_mock.insert.assert_called_once_with(ignore_permissions=True)
    # The inserted doc must be for v2
    call_args = frappe.get_doc.call_args[0][0]
    assert call_args["schema_version"] == "v2"


# ============================================================================
# TRG-004: SQL query uses only end_date < today (no 90-day cutoff)
# ============================================================================

def test_sql_query_has_no_age_cutoff():
    """check_season_scoped_archives() must query ALL ended seasons, not just
    those ended within the last 90 days.

    TRG-004: the SQL WHERE clause must contain only `end_date < today` — no
    lower-bound date parameter — so seasons ended more than 90 days ago are
    included and can still have jobs created for them.
    """
    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    # No seasons returned — we only care about the SQL call, not the loop.
    frappe.db.sql.return_value = []

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[],
    ):
        check_season_scoped_archives()

    frappe.db.sql.assert_called_once()
    _sql_args = frappe.db.sql.call_args[0]
    # Second positional arg is the parameters tuple — must be a 1-tuple (today only)
    params = _sql_args[1]
    assert len(params) == 1, (
        f"SQL query must pass exactly 1 parameter (today), got {len(params)}: {params!r}. "
        "A 90-day cutoff date must not be added as a second parameter."
    )
