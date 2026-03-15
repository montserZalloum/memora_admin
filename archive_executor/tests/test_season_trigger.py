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

_ENDED_SEASON_WITH_START = SimpleNamespace(
    name="SEAS-00623", season_seq=623, start_date="2025-09-01", end_date="2026-01-01",
)

_ARCHIVE_TYPE_DATE_WINDOW = {
    "archive_type": "practice_log",
    "source_table": "tabMemora Practice Log",
    "trigger_mode": "season",
    "purge_mode": "date_window",
    "version": "v1",
    "scope_column": "last_seen_at",
    "fact_columns": ["player_id", "item_id"],
    "dimensions": [],
}

_ARCHIVE_TYPE_PLAYER_SCOPE = {
    "archive_type": "practice_log",
    "source_table": "tabMemora Practice Log",
    "trigger_mode": "season",
    "purge_mode": "player_scope",
    "version": "v1",
    "scope_column": "last_seen_at",
    "fact_columns": ["player_id", "item_id"],
    "dimensions": [],
    "cleanup_tables": [
        {"table": "tabPlayer Practice Summary", "player_column": "player_id"},
    ],
}


@pytest.fixture(autouse=True)
def reset_frappe_mocks():
    """Reset all frappe mock call counts and side_effects before each test."""
    frappe.db.reset_mock(side_effect=True)
    frappe.get_doc.reset_mock(side_effect=True)
    frappe.log_error.reset_mock(side_effect=True)
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


# ============================================================================
# TRG-005: purge_mode=date_window produces date_from/date_to (no filter_type)
# ============================================================================

def test_date_window_produces_date_range_meta():
    """A hybrid archive type with purge_mode=date_window must produce job_meta
    with date_from/date_to query_filter — NOT filter_type=season.

    TRG-005: the executor pipeline uses the date-window code path (batched
    DELETE, not DROP PARTITION) when query_filter has date_from/date_to.
    """
    import json

    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    frappe.db.sql.return_value = [_ENDED_SEASON_WITH_START]
    frappe.db.exists.return_value = None  # no existing job

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_DATE_WINDOW],
    ):
        check_season_scoped_archives()

    # A job must have been inserted
    job_mock.insert.assert_called_once_with(ignore_permissions=True)

    # Inspect the job_meta passed to frappe.get_doc
    doc_dict = frappe.get_doc.call_args[0][0]
    meta = json.loads(doc_dict["job_meta"])
    qf = meta["query_filter"]

    assert qf["date_from"] == "2025-09-01", f"Expected date_from=2025-09-01, got {qf['date_from']}"
    assert qf["date_to"] == "2026-01-01", f"Expected date_to=2026-01-01, got {qf['date_to']}"
    assert qf["filter_column"] == "last_seen_at"
    assert "filter_type" not in qf, f"date_window meta must not contain filter_type, got {qf!r}"
    assert "season_seq" not in qf, f"date_window meta must not contain season_seq, got {qf!r}"


# ============================================================================
# TRG-006: hybrid type uses season.name as archive_scope (not season_N)
# ============================================================================

def test_date_window_uses_season_name_as_scope():
    """A hybrid archive type must use the season's name (e.g. SEAS-00623) as
    archive_scope, not the season_N format used by standard season-scoped types.

    TRG-006: this ensures dedup against the 5 existing practice_log jobs which
    already used season.name as archive_scope.
    """
    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    frappe.db.sql.return_value = [_ENDED_SEASON_WITH_START]
    frappe.db.exists.return_value = None

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_DATE_WINDOW],
    ):
        check_season_scoped_archives()

    doc_dict = frappe.get_doc.call_args[0][0]
    assert doc_dict["archive_scope"] == "SEAS-00623", (
        f"Expected archive_scope=SEAS-00623 (season name), got {doc_dict['archive_scope']!r}"
    )

    # Dedup check must also use the season name
    frappe.db.exists.assert_called_once_with(
        "Memora Archive Job",
        {
            "source_doctype": "Memora Practice Log",
            "archive_scope": "SEAS-00623",
            "archive_type": "practice_log",
            "schema_version": "v1",
            "status": ["!=", "Failed"],
        },
    )


# ============================================================================
# TRG-007: hybrid type gets post_archive_action="Delete"
# ============================================================================

def test_date_window_gets_delete_action():
    """A hybrid archive type routed through check_season_scoped_archives() must
    get post_archive_action="Delete" so the purge stage picks it up.

    TRG-007: all season-triggered types use Delete, including hybrid ones.
    """
    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    frappe.db.sql.return_value = [_ENDED_SEASON_WITH_START]
    frappe.db.exists.return_value = None

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_DATE_WINDOW],
    ):
        check_season_scoped_archives()

    doc_dict = frappe.get_doc.call_args[0][0]
    assert doc_dict["post_archive_action"] == "Delete", (
        f"Expected post_archive_action=Delete, got {doc_dict['post_archive_action']!r}"
    )


# ============================================================================
# TRG-008: player_scope produces player_ids + season dates in meta
# ============================================================================

def test_player_scope_meta_contains_player_ids():
    """A player_scope archive type must produce job_meta with filter_type=player_scope,
    player_ids list, season_date_from/to, and cleanup_tables.

    TRG-008: the archive trigger snapshots player_ids at job creation time.
    """
    import json

    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    frappe.db.sql.return_value = [_ENDED_SEASON_WITH_START]
    frappe.db.exists.return_value = None

    # Dispatch by query content (robust against internal query reordering)
    def _sql_side_effect(query, values=None, as_dict=None, **kwargs):
        q = str(query)
        if "tabMemora Player Profile" in q:
            return [("PLYR-001",), ("PLYR-002",), ("PLYR-003",)]
        # Default: ended seasons query
        return [_ENDED_SEASON_WITH_START]

    frappe.db.sql.side_effect = _sql_side_effect

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_PLAYER_SCOPE],
    ):
        check_season_scoped_archives()

    job_mock.insert.assert_called_once_with(ignore_permissions=True)

    doc_dict = frappe.get_doc.call_args[0][0]
    meta = json.loads(doc_dict["job_meta"])
    qf = meta["query_filter"]

    assert qf["filter_type"] == "player_scope"
    assert qf["filter_column"] == "player_id"
    assert qf["player_ids"] == ["PLYR-001", "PLYR-002", "PLYR-003"]
    assert qf["season_date_from"] == "2025-09-01"
    assert qf["season_date_to"] == "2026-01-01"

    # cleanup_tables must be present in meta
    assert meta["cleanup_tables"] == [
        {"table": "tabPlayer Practice Summary", "player_column": "player_id"},
    ]


# ============================================================================
# TRG-009: player_scope with empty player list still creates job
# ============================================================================

def test_player_scope_empty_players_creates_job():
    """When no players exist for the season, a job is still created with
    empty player_ids. The archive pipeline handles 0-row exports gracefully.

    TRG-009: empty season must not be skipped.
    """
    import json

    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    def _sql_side_effect(query, values=None, as_dict=None, **kwargs):
        q = str(query)
        if "tabMemora Player Profile" in q:
            return []  # no players
        return [_ENDED_SEASON_WITH_START]

    frappe.db.sql.side_effect = _sql_side_effect
    frappe.db.exists.return_value = None

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_PLAYER_SCOPE],
    ):
        check_season_scoped_archives()

    job_mock.insert.assert_called_once_with(ignore_permissions=True)

    doc_dict = frappe.get_doc.call_args[0][0]
    meta = json.loads(doc_dict["job_meta"])
    assert meta["query_filter"]["player_ids"] == []


# ============================================================================
# TRG-010: player_scope uses season.name as archive_scope
# ============================================================================

def test_player_scope_uses_season_name_as_scope():
    """A player_scope archive type must use the season's name (not season_N)
    as archive_scope.

    TRG-010: consistent with existing practice_log jobs.
    """
    from memora_admin.tasks.archive_trigger import check_season_scoped_archives

    def _sql_side_effect(query, values=None, as_dict=None, **kwargs):
        q = str(query)
        if "tabMemora Player Profile" in q:
            return [("PLYR-001",)]
        return [_ENDED_SEASON_WITH_START]

    frappe.db.sql.side_effect = _sql_side_effect
    frappe.db.exists.return_value = None

    job_mock = MagicMock()
    frappe.get_doc.return_value = job_mock

    with patch(
        "memora_admin.tasks.archive_trigger._load_archive_types",
        return_value=[_ARCHIVE_TYPE_PLAYER_SCOPE],
    ):
        check_season_scoped_archives()

    doc_dict = frappe.get_doc.call_args[0][0]
    assert doc_dict["archive_scope"] == "SEAS-00623"
