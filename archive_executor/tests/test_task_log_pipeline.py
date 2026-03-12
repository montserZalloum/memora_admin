"""Integration and unit tests for the Task Log Archive Pipeline (T016).

Covers the eight scenarios specified in tasks.md:
  1. Happy-path end-to-end state machine
  2. No-eligible-rows: create_pending_jobs returns empty list
  3. Idempotency: re-running creates no duplicate jobs or batches
  4. Retention window guard: purge never deletes rows within 90 days
  5. Runtime cap: task exits cleanly when time limit is hit
  6. Failure retry: Failed batch retried, retry_count incremented
  7. Max retry skip: batch at MAX_RETRY_COUNT skipped, frappe.log_error called
  8. Sub-batch commit: each 10,000-row chunk committed independently

Architecture
------------
- Frappe ORM is mocked via sys.modules (no bench context required).
- _purge_sub_batch is tested against the real MariaDB via _FrappeDbLike wrapper.
- Higher-level orchestration is tested with patch() for all frappe calls.

Run with:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=_9be6802bfff1e8ca \\
    DB_PASSWORD=zjAACevKaH5VGVP2 DB_NAME=_9be6802bfff1e8ca \\
    SCHEMA_REGISTRY_PATH=$(pwd)/archive_schemas \\
    ARCHIVE_OUTPUT_PATH=/tmp/memora-archive-test \\
    python3 -m pytest archive_executor/tests/test_task_log_pipeline.py -v
"""

# ============================================================================
# Frappe mock — must be installed BEFORE any memora_admin imports
# ============================================================================

import sys
import types
from datetime import datetime
from unittest.mock import MagicMock


def _install_frappe_mock() -> None:
    """Install a minimal frappe stub into sys.modules so task modules can import."""
    if "frappe" in sys.modules and hasattr(sys.modules["frappe"], "get_all"):
        return  # already installed by a sibling test module

    _frappe_utils = types.ModuleType("frappe.utils")
    _frappe_utils.now_datetime = datetime.now  # type: ignore[attr-defined]

    _frappe = types.ModuleType("frappe")
    _frappe.utils = _frappe_utils  # type: ignore[attr-defined]
    _frappe.db = MagicMock()  # type: ignore[attr-defined]
    _frappe.get_all = MagicMock(return_value=[])  # type: ignore[attr-defined]
    _frappe.get_doc = MagicMock()  # type: ignore[attr-defined]
    _frappe.log_error = MagicMock()  # type: ignore[attr-defined]

    sys.modules["frappe"] = _frappe
    sys.modules["frappe.utils"] = _frappe_utils


_install_frappe_mock()

# ============================================================================
# Standard imports — AFTER frappe mock is in place
# ============================================================================

import json
import time
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import call, patch

import pymysql
import pymysql.cursors
import pytest

from archive_executor.config import Config
from memora_admin.tasks.archive_task_log import (
    MAX_RETRY_COUNT,
    RETENTION_DAYS,
    TERMINAL_STATUSES,
    _create_batch_for_job,
    _retry_failed_batches,
    _sync_batch_statuses,
    archive_task_log,
)
from memora_admin.tasks.purge_task_log import (
    SOURCE_TABLE,
    SUB_BATCH_SIZE,
    TERMINAL_STATUSES as PURGE_TERMINAL_STATUSES,
    _purge_sub_batch,
    purge_task_log,
)

from .conftest import db_conn, integration_db_config  # noqa: F401 (fixtures)

pytestmark = pytest.mark.integration

# ============================================================================
# Constants
# ============================================================================

# Date ranges well outside the 90-day retention window (relative to 2026-03-11)
TL_RANGE_A = ("2025-01-01", "2025-01-02")   # single day, small dataset
TL_RANGE_B = ("2025-01-05", "2025-01-06")   # distinct window for sub-batch test
TL_RANGE_C = ("2025-01-07", "2025-01-08")   # for purge orchestration tests

# Archive job name constants (used only in mock state, not inserted into DB)
TL_JOB_1 = "ARCH-20001"
TL_JOB_2 = "ARCH-20002"

# Frappe module paths for patching
_ARC = "memora_admin.tasks.archive_task_log"
_PRG = "memora_admin.tasks.purge_task_log"

# ============================================================================
# Helpers
# ============================================================================


class _FrappeDbLike:
    """Thin wrapper making a pymysql connection look like frappe.db for _purge_sub_batch.

    _purge_sub_batch calls conn.sql(), conn.commit(), conn.rollback() only.
    sql() returns a list of tuples (matching frappe.db.sql default behaviour).
    """

    def __init__(self, conn):
        self._conn = conn

    def sql(self, query, values=None):
        with self._conn.cursor(pymysql.cursors.Cursor) as cur:
            cur.execute(query, values or ())
            return cur.fetchall()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


def _insert_task_log_rows(
    conn,
    prefix: str,
    count: int,
    date_from: str,
    date_to: str,
    status: str = "Success",
    batch_size: int = 500,
) -> int:
    """Insert synthetic tabMemora Task Run Log rows for integration tests.

    Names are TL-{prefix}-{n:06d}.  completed_at is spread evenly across
    [date_from, date_to).  Returns rows actually inserted.
    """
    from datetime import datetime as _dt, timedelta as _td

    dt_from = _dt.fromisoformat(date_from)
    dt_to = _dt.fromisoformat(date_to)
    span = max(int((dt_to - dt_from).total_seconds()), 1)

    sql = (
        "INSERT IGNORE INTO `tabMemora Task Run Log` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `task_name`, `run_date`, `started_at`, `completed_at`, "
        " `duration_sec`, `status`, `triggered_by`, `processed_count`, `failed_count`) "
        "VALUES (%s, NOW(), NOW(), 'test', 'test', 0, %s, %s, %s, %s, %s, 1.0, %s, 'Scheduler', 0, 0)"
    )

    rows = []
    inserted = 0
    for n in range(1, count + 1):
        step = max(span // max(count, 1), 1)
        ts = dt_from + _td(seconds=(n - 1) * step)
        rows.append((
            f"TL-{prefix}-{n:06d}",
            n,
            f"test_task_{prefix}_{n}",
            ts.date().isoformat(),
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            status,
        ))
        if len(rows) >= batch_size:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
            inserted += len(rows)
            rows = []

    if rows:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
        inserted += len(rows)

    return inserted


def _delete_task_log_rows(conn, prefix: str) -> None:
    """Delete test rows from tabMemora Task Run Log by name prefix."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Task Run Log` WHERE `name` LIKE %s",
            (f"TL-{prefix}-%",),
        )
    conn.commit()


def _count_task_log_rows(conn, date_from: str, date_to: str) -> int:
    """Count rows in tabMemora Task Run Log within the completed_at window."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM `tabMemora Task Run Log` "
            "WHERE `completed_at` >= %s AND `completed_at` < %s",
            (date_from, date_to),
        )
        return cur.fetchone()["cnt"]


def _count_task_log_rows_by_prefix(conn, prefix: str) -> int:
    """Count rows in tabMemora Task Run Log matching the TL-{prefix}-* name pattern."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM `tabMemora Task Run Log` WHERE `name` LIKE %s",
            (f"TL-{prefix}-%",),
        )
        return cur.fetchone()["cnt"]


def _make_batch(**kwargs) -> SimpleNamespace:
    """Build a batch record matching frappe.get_all() output structure."""
    defaults = dict(
        name="TLBATCH-00001",
        source_doctype="Memora Task Run Log",
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 2),
        cutoff_date=str(date.today() - timedelta(days=RETENTION_DAYS)),
        status="Pending",
        archive_job_id=TL_JOB_1,
        retry_count=0,
        last_error="",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_job_dict(**kwargs) -> dict:
    """Build a job record matching frappe.db.get_value(..., as_dict=True) output."""
    defaults = dict(status="Pending", file_path="", file_checksum="", row_count=0)
    defaults.update(kwargs)
    return defaults


# ============================================================================
# 1. _purge_sub_batch — integration tests against the real DB
# ============================================================================


class TestPurgeSubBatch:
    """Direct integration tests for _purge_sub_batch using a real MariaDB connection."""

    @pytest.fixture(autouse=True)
    def _setup(self, db_conn):
        self.conn = db_conn
        self.db = _FrappeDbLike(db_conn)
        # Ensure a fresh snapshot for REPEATABLE READ isolation
        self.conn.commit()
        yield
        # Teardown: remove all TL-PSB-* rows
        _delete_task_log_rows(self.conn, "PSB")
        self.conn.commit()

    # Scenario 2 helper — empty window
    def test_empty_window_returns_zero(self):
        """No rows in the date window → returns 0 without error."""
        deleted = _purge_sub_batch(
            self.db, SOURCE_TABLE, "2025-01-20", "2025-01-21", 90, TERMINAL_STATUSES
        )
        assert deleted == 0

    # Scenario 4 — retention window guard
    def test_retention_guard_protects_recent_rows(self):
        """Rows with completed_at inside the 90-day window are never deleted."""
        thirty_ago = (date.today() - timedelta(days=30)).isoformat()
        twenty_nine_ago = (date.today() - timedelta(days=29)).isoformat()
        _insert_task_log_rows(self.conn, "PSB-RET", 5, thirty_ago, twenty_nine_ago)
        self.conn.commit()

        deleted = _purge_sub_batch(
            self.db, SOURCE_TABLE, thirty_ago, twenty_nine_ago, 90, TERMINAL_STATUSES
        )

        assert deleted == 0
        # Count by name prefix to avoid collisions with production rows in this date range
        assert _count_task_log_rows_by_prefix(self.conn, "PSB-RET") == 5

    def test_deletes_eligible_rows(self):
        """Rows outside the retention window and with terminal status are deleted."""
        _insert_task_log_rows(self.conn, "PSB-DEL", 10, "2025-01-09", "2025-01-10")
        self.conn.commit()

        deleted = _purge_sub_batch(
            self.db, SOURCE_TABLE, "2025-01-09", "2025-01-10", 90, TERMINAL_STATUSES
        )

        assert deleted == 10
        assert _count_task_log_rows(self.conn, "2025-01-09", "2025-01-10") == 0

    def test_skips_non_terminal_status_rows(self):
        """Rows with non-terminal status (e.g. 'Running') are never deleted."""
        _insert_task_log_rows(
            self.conn, "PSB-SKIP", 5, "2025-01-11", "2025-01-12", status="Running"
        )
        self.conn.commit()

        deleted = _purge_sub_batch(
            self.db, SOURCE_TABLE, "2025-01-11", "2025-01-12", 90, TERMINAL_STATUSES
        )

        assert deleted == 0

    # Scenario 8 — sub-batch commit
    def test_sub_batch_size_limit(self):
        """First call deletes exactly SUB_BATCH_SIZE rows; second call gets the rest."""
        n = SUB_BATCH_SIZE + 1
        _insert_task_log_rows(self.conn, "PSB-LIM", n, *TL_RANGE_B)
        self.conn.commit()

        first = _purge_sub_batch(
            self.db, SOURCE_TABLE, *TL_RANGE_B, 90, TERMINAL_STATUSES
        )
        assert first == SUB_BATCH_SIZE
        assert _count_task_log_rows(self.conn, *TL_RANGE_B) == 1

        second = _purge_sub_batch(
            self.db, SOURCE_TABLE, *TL_RANGE_B, 90, TERMINAL_STATUSES
        )
        assert second == 1
        assert _count_task_log_rows(self.conn, *TL_RANGE_B) == 0

    def test_third_call_returns_zero_when_empty(self):
        """Calling _purge_sub_batch when no rows remain returns 0."""
        _insert_task_log_rows(self.conn, "PSB-ZERO", 5, "2025-01-13", "2025-01-14")
        self.conn.commit()

        _purge_sub_batch(self.db, SOURCE_TABLE, "2025-01-13", "2025-01-14", 90, TERMINAL_STATUSES)
        result = _purge_sub_batch(
            self.db, SOURCE_TABLE, "2025-01-13", "2025-01-14", 90, TERMINAL_STATUSES
        )
        assert result == 0

    def test_lock_timeout_rolls_back_and_raises(self):
        """OperationalError (lock timeout) triggers rollback and propagates."""
        db = MagicMock()
        db.sql.side_effect = pymysql.OperationalError(1205, "Lock wait timeout exceeded")

        with pytest.raises(pymysql.OperationalError):
            _purge_sub_batch(
                db, SOURCE_TABLE, "2025-01-01", "2025-01-02", 90, TERMINAL_STATUSES
            )

        db.rollback.assert_called_once()


# ============================================================================
# 2. archive_task_log — unit tests with mocked frappe
# ============================================================================


class TestArchiveTaskLog:
    """Unit tests for archive_task_log() and its helpers.

    All frappe ORM calls are replaced with patch() — no batch table required.
    """

    # Scenario 2 — no eligible rows
    def test_no_eligible_rows_creates_no_batches(self):
        """If create_pending_jobs returns [], no batches are created."""
        with (
            patch(f"{_ARC}.Config.from_env", return_value=MagicMock()),
            patch(f"{_ARC}.create_pending_jobs", return_value=[]),
            patch("frappe.get_all", return_value=[]),
            patch("frappe.db.set_value"),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
            patch("frappe.get_doc") as mock_get_doc,
        ):
            archive_task_log(triggered_by="Test")

        # frappe.get_doc is only called for batch creation — should not happen
        batch_creates = [
            c for c in mock_get_doc.call_args_list
            if isinstance(c.args[0], dict)
            and c.args[0].get("doctype") == "Memora Task Log Archive Batch"
        ]
        assert len(batch_creates) == 0

    # Scenario 1 partial — batch created for new archive job
    def test_creates_batch_for_new_job(self):
        """A newly created archive job triggers Memora Task Log Archive Batch creation."""
        new_job = "ARCH-29999"
        batch_docs = []

        def _mock_get_doc(first, *rest, **kw):
            if isinstance(first, str) and first == "Memora Archive Job":
                return SimpleNamespace(
                    name=new_job,
                    source_doctype="Memora Task Run Log",
                    job_meta=json.dumps({
                        "query_filter": {
                            "date_from": "2025-01-01",
                            "date_to": "2025-01-02",
                            "cutoff_date": "2025-12-01",
                        }
                    }),
                )
            if isinstance(first, dict) and first.get("doctype") == "Memora Task Log Archive Batch":
                batch_docs.append(first.copy())
                mock_doc = MagicMock()
                mock_doc.name = "TLBATCH-00001"
                return mock_doc
            return MagicMock()

        with (
            patch(f"{_ARC}.Config.from_env", return_value=MagicMock()),
            patch(f"{_ARC}.create_pending_jobs", return_value=[new_job]),
            patch("frappe.get_all", return_value=[]),
            patch("frappe.get_doc", side_effect=_mock_get_doc),
            patch("frappe.db.set_value"),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            archive_task_log(triggered_by="Test")

        assert len(batch_docs) == 1
        bd = batch_docs[0]
        assert bd["status"] == "Pending"
        assert bd["archive_job_id"] == new_job
        assert bd["date_from"] == "2025-01-01"
        assert bd["date_to"] == "2025-01-02"
        assert bd["source_doctype"] == "Memora Task Run Log"

    # Scenario 1 — Pending + Completed job → Synced
    def test_sync_pending_batch_to_synced_when_job_completed(self):
        """_sync_batch_statuses: Pending batch whose job is Completed → transitions to Synced."""
        batch = _make_batch(status="Pending", archive_job_id=TL_JOB_1)
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch(
                "frappe.db.get_value",
                return_value=_make_job_dict(
                    status="Completed",
                    file_path="/tmp/arch/job1",
                    file_checksum=f"sha256:{'a' * 64}",
                    row_count=99,
                ),
            ),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            synced, failed = _sync_batch_statuses()

        assert synced == 1
        assert failed == 0
        synced_vals = next(v for v in set_calls if v.get("status") == "Synced")
        assert "synced_at" in synced_vals
        assert "exported_at" in synced_vals  # Pending → Synced skips Exported
        assert synced_vals["row_count"] == 99
        assert synced_vals["file_path"] == "/tmp/arch/job1"
        assert synced_vals["file_checksum"] == "a" * 64

    # Scenario 1 — Exported + Completed job → Synced (no exported_at re-set)
    def test_sync_exported_batch_to_synced(self):
        """_sync_batch_statuses: Exported batch whose job is Completed → Synced (no exported_at)."""
        batch = _make_batch(status="Exported", archive_job_id=TL_JOB_1)
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch(
                "frappe.db.get_value",
                return_value=_make_job_dict(status="Completed", row_count=5),
            ),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            synced, failed = _sync_batch_statuses()

        assert synced == 1
        synced_vals = next(v for v in set_calls if v.get("status") == "Synced")
        assert "exported_at" not in synced_vals  # already Exported, don't overwrite

    def test_sync_pending_batch_to_purged_when_job_purged(self):
        """_sync_batch_statuses: Pending batch whose job is Purged → transitions to Purged."""
        batch = _make_batch(status="Pending", archive_job_id=TL_JOB_1)
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch(
                "frappe.db.get_value",
                return_value=_make_job_dict(
                    status="Purged",
                    file_path="/tmp/arch/job1",
                    file_checksum=f"sha256:{'d' * 64}",
                    row_count=12,
                ),
            ),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            synced, failed = _sync_batch_statuses()

        assert synced == 1
        assert failed == 0
        purged_vals = next(v for v in set_calls if v.get("status") == "Purged")
        assert "exported_at" in purged_vals
        assert "synced_at" in purged_vals
        assert "purged_at" in purged_vals
        assert purged_vals["row_count"] == 12
        assert purged_vals["file_path"] == "/tmp/arch/job1"
        assert purged_vals["file_checksum"] == "d" * 64

    def test_sync_synced_batch_to_purged_when_job_purged(self):
        """_sync_batch_statuses: Synced batch whose job is Purged → transitions to Purged."""
        batch = _make_batch(status="Synced", archive_job_id=TL_JOB_1)
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch(
                "frappe.db.get_value",
                return_value=_make_job_dict(status="Purged", row_count=5),
            ),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            synced, failed = _sync_batch_statuses()

        assert synced == 1
        assert failed == 0
        purged_vals = next(v for v in set_calls if v.get("status") == "Purged")
        assert "purged_at" in purged_vals
        assert "synced_at" not in purged_vals
        assert "exported_at" not in purged_vals

    # Pending + in-flight Exported job → batch transitions to Exported
    def test_sync_pending_batch_to_exported_when_job_in_flight(self):
        """_sync_batch_statuses: Pending batch with Exported job → batch transitions to Exported."""
        batch = _make_batch(status="Pending", archive_job_id=TL_JOB_1)
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch(
                "frappe.db.get_value",
                return_value=_make_job_dict(
                    status="Exported", file_path="/tmp/x", file_checksum="b" * 64, row_count=7
                ),
            ),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            synced, failed = _sync_batch_statuses()

        assert synced == 0
        exported_vals = next(v for v in set_calls if v.get("status") == "Exported")
        assert exported_vals["row_count"] == 7
        assert "exported_at" in exported_vals

    # Scenario 3 — idempotency
    def test_idempotent_when_no_new_jobs(self):
        """When create_pending_jobs returns [] (all jobs exist), no batches are created."""
        batch_inserts = []

        def _mock_get_doc(first, *rest, **kw):
            if isinstance(first, dict) and first.get("doctype") == "Memora Task Log Archive Batch":
                batch_inserts.append(first)
                m = MagicMock()
                m.name = "TLBATCH-00001"
                return m
            return MagicMock()

        with (
            patch(f"{_ARC}.Config.from_env", return_value=MagicMock()),
            patch(f"{_ARC}.create_pending_jobs", return_value=[]),
            patch("frappe.get_all", return_value=[]),
            patch("frappe.get_doc", side_effect=_mock_get_doc),
            patch("frappe.db.set_value"),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            archive_task_log(triggered_by="Test")
            archive_task_log(triggered_by="Test")  # second call — idempotent

        # No batches should have been inserted on either call
        assert batch_inserts == []

    # Scenario 5 — runtime cap
    def test_runtime_cap_skips_phase2(self):
        """Runtime cap hit after sync phase: Phase 2 (new job creation) is skipped."""
        # Call sequence inside archive_task_log (with empty failed/sync batches):
        #   call 0: start_time = time.monotonic()       → 0
        #   call 1: cap check after retry phase          → 0  (0-0=0 < 300, continue)
        #   call 2: cap check after sync phase           → 400 (400-0=400 >= 300, EXIT)
        _time_seq = [0, 0, 400, 400, 400, 400, 400]
        _idx = [0]

        def _mock_mono():
            v = _time_seq[min(_idx[0], len(_time_seq) - 1)]
            _idx[0] += 1
            return v

        with (
            patch(f"{_ARC}.time.monotonic", side_effect=_mock_mono),
            patch(f"{_ARC}.create_pending_jobs") as mock_sched,
            patch("frappe.get_all", return_value=[]),
            patch("frappe.db.set_value"),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
            patch("frappe.get_doc"),
        ):
            archive_task_log(triggered_by="Test")

        mock_sched.assert_not_called()

    # Scenario 6 — failure retry
    def test_retry_failed_batch_increments_count(self):
        """_retry_failed_batches: Failed batch with retry_count < MAX → retried, count+1."""
        batch = _make_batch(status="Failed", retry_count=1, archive_job_id=TL_JOB_2)
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
            patch(
                f"{_ARC}._get_or_create_archive_job",
                return_value=TL_JOB_2,
            ),
        ):
            retried = _retry_failed_batches(start_time=time.monotonic())

        assert retried == 1
        pending_update = next((v for v in set_calls if v.get("status") == "Pending"), None)
        assert pending_update is not None
        assert pending_update["retry_count"] == 2
        assert pending_update["last_error"] == ""
        assert pending_update["archive_job_id"] == TL_JOB_2

    def test_retry_failed_batch_handles_get_or_create_error(self):
        """When _get_or_create_archive_job raises, batch last_error is populated."""
        batch = _make_batch(status="Failed", retry_count=0)
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
            patch(
                f"{_ARC}._get_or_create_archive_job",
                side_effect=RuntimeError("DB unavailable"),
            ),
        ):
            retried = _retry_failed_batches(start_time=time.monotonic())

        assert retried == 0
        # last_error should be set from the exception
        error_updates = [v for v in set_calls if "last_error" in v and "status" not in v]
        assert len(error_updates) == 1
        assert "DB unavailable" in error_updates[0]["last_error"]

    # Scenario 7 — max retry skip
    def test_max_retry_skip_logs_error(self):
        """_retry_failed_batches: batch at MAX_RETRY_COUNT is skipped; frappe.log_error called."""
        batch = _make_batch(status="Failed", retry_count=MAX_RETRY_COUNT)
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error") as mock_log_err,
            patch(f"{_ARC}._get_or_create_archive_job"),
        ):
            retried = _retry_failed_batches(start_time=time.monotonic())

        assert retried == 0
        mock_log_err.assert_called_once()
        # Status must NOT be changed to Pending
        assert not any(v.get("status") == "Pending" for v in set_calls)
        # last_error must mention max retries
        error_updates = [v for v in set_calls if "last_error" in v]
        assert len(error_updates) == 1
        assert "manual intervention" in error_updates[0]["last_error"].lower()

    def test_max_retry_const_is_three(self):
        """MAX_RETRY_COUNT constant is 3 as specified in the design."""
        assert MAX_RETRY_COUNT == 3

    # Scenario 1 — full state machine via archive_task_log entry point
    def test_full_state_machine_pending_to_synced(self):
        """archive_task_log(): Pending batch + Completed job → batch becomes Synced."""
        batch_state: dict = {"status": "Pending", "archive_job_id": TL_JOB_1, "retry_count": 0}

        def _get_all(doctype, filters=None, fields=None, **kw):
            if doctype == "Memora Task Log Archive Batch":
                s = (filters or {}).get("status")
                cond = s[1] if isinstance(s, list) else [s] if s else []
                if not cond or batch_state["status"] in cond:
                    return [SimpleNamespace(
                        name="TLBATCH-00001",
                        archive_job_id=batch_state["archive_job_id"],
                        status=batch_state["status"],
                        retry_count=batch_state["retry_count"],
                        source_doctype="Memora Task Run Log",
                        date_from=date(2025, 1, 1),
                        date_to=date(2025, 1, 2),
                    )]
            return []

        def _set_value(doctype, name, vals):
            if doctype == "Memora Task Log Archive Batch":
                batch_state.update(vals)

        with (
            patch(f"{_ARC}.Config.from_env", return_value=MagicMock()),
            patch("frappe.get_all", side_effect=_get_all),
            patch(
                "frappe.db.get_value",
                return_value=_make_job_dict(status="Completed", row_count=10),
            ),
            patch("frappe.db.set_value", side_effect=_set_value),
            patch("frappe.db.commit"),
            patch(f"{_ARC}.create_pending_jobs", return_value=[]),
            patch("frappe.log_error"),
            patch("frappe.get_doc"),
        ):
            archive_task_log(triggered_by="Test")

        assert batch_state["status"] == "Synced"
        assert "synced_at" in batch_state


# ============================================================================
# 3. purge_task_log — unit tests with mocked frappe
# ============================================================================


class TestPurgeTaskLog:
    """Unit tests for purge_task_log() orchestration.

    _purge_sub_batch is patched so no real DB interaction needed (covered by
    TestPurgeSubBatch).  Real DB is used only where noted.
    """

    # Scenario 1 — happy path Synced → Purged
    def test_happy_path_synced_to_purged(self):
        """Synced batch: _purge_sub_batch returns 0 → batch transitions to Purged."""
        batch = _make_batch(status="Synced")
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch(f"{_PRG}._purge_sub_batch", return_value=0),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            purge_task_log(triggered_by="Test")

        purged_vals = next((v for v in set_calls if v.get("status") == "Purged"), None)
        assert purged_vals is not None
        assert "purged_at" in purged_vals

    def test_only_synced_batches_are_processed(self):
        """purge_task_log fetches only Synced batches; other statuses are not processed."""
        get_all_calls = []

        def _get_all(doctype, filters=None, **kw):
            get_all_calls.append((doctype, filters))
            return []

        with (
            patch("frappe.get_all", side_effect=_get_all),
            patch("frappe.db.set_value"),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            purge_task_log(triggered_by="Test")

        batch_queries = [
            c for c in get_all_calls
            if c[0] == "Memora Task Log Archive Batch"
        ]
        assert batch_queries, "purge_task_log must query Memora Task Log Archive Batch"
        _, filters = batch_queries[0]
        assert filters.get("status") == "Synced"

    # Scenario 5 — runtime cap during purge
    def test_runtime_cap_defers_remaining_batches(self):
        """Runtime cap hit mid-loop: remaining batches are deferred, no exception raised."""
        batch1 = _make_batch(name="TLBATCH-99901", status="Synced",
                              date_from=date(2025, 1, 1), date_to=date(2025, 1, 2))
        batch2 = _make_batch(name="TLBATCH-99902", status="Synced",
                              date_from=date(2025, 1, 2), date_to=date(2025, 1, 3))

        # Cap is hit immediately after the first sub-batch check of batch1
        _times = iter([0, 400, 400, 400, 400, 400, 400])

        with (
            patch("frappe.get_all", return_value=[batch1, batch2]),
            patch(f"{_PRG}.time.monotonic", side_effect=lambda: next(_times)),
            patch(f"{_PRG}._purge_sub_batch", return_value=0),
            patch("frappe.db.set_value"),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            purge_task_log(triggered_by="Test")  # must not raise

    def test_lock_timeout_sets_last_error_leaves_synced(self):
        """OperationalError during purge sub-batch sets last_error; status stays Synced."""
        batch = _make_batch(status="Synced")
        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch(
                f"{_PRG}._purge_sub_batch",
                side_effect=pymysql.OperationalError(1205, "Lock wait timeout"),
            ),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            purge_task_log(triggered_by="Test")

        # last_error must be set
        assert any("last_error" in v for v in set_calls)
        # status must NOT be Purged
        assert not any(v.get("status") == "Purged" for v in set_calls)

    def test_partial_purge_loop_continues_until_zero(self):
        """_purge_sub_batch is called in a loop until it returns 0 (all rows deleted)."""
        batch = _make_batch(status="Synced")
        sub_batch_call_count = [0]

        def _mock_purge(*args, **kwargs):
            sub_batch_call_count[0] += 1
            # Return 10000 for first call, then 0
            return 10_000 if sub_batch_call_count[0] == 1 else 0

        set_calls = []

        with (
            patch("frappe.get_all", return_value=[batch]),
            patch(f"{_PRG}._purge_sub_batch", side_effect=_mock_purge),
            patch("frappe.db.set_value", side_effect=lambda *a: set_calls.append(a[2])),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            purge_task_log(triggered_by="Test")

        assert sub_batch_call_count[0] == 2  # called twice: one batch, then zero
        assert any(v.get("status") == "Purged" for v in set_calls)

    # Integration: real source row deletion via _purge_sub_batch with real DB
    def test_purge_deletes_source_rows_in_real_db(self, db_conn):
        """_purge_sub_batch loop fully deletes eligible source rows from the real DB."""
        _insert_task_log_rows(db_conn, "PTL-INT", 15, *TL_RANGE_C)
        db_conn.commit()

        db_wrapper = _FrappeDbLike(db_conn)
        total_deleted = 0
        while True:
            n = _purge_sub_batch(
                db_wrapper, SOURCE_TABLE, *TL_RANGE_C, 90, TERMINAL_STATUSES
            )
            total_deleted += n
            if n == 0:
                break

        assert total_deleted == 15
        assert _count_task_log_rows_by_prefix(db_conn, "PTL-INT") == 0

        _delete_task_log_rows(db_conn, "PTL-INT")


# ============================================================================
# 4. End-to-end state machine integration test
# ============================================================================


class TestHappyPathEndToEnd:
    """Scenario 1: Full archive → sync → purge lifecycle using real DB for source rows.

    Validates the state machine transitions and that source rows are actually
    deleted from tabMemora Task Run Log.
    """

    @pytest.fixture(autouse=True)
    def _setup_teardown(self, db_conn):
        self.conn = db_conn
        self.conn.commit()
        yield
        _delete_task_log_rows(self.conn, "E2E")
        self.conn.commit()

    def test_full_archive_sync_purge_lifecycle(self):
        """Full lifecycle: seed rows → archive creates batch → job completes →
        sync to Synced → purge deletes rows → batch Purged."""

        _insert_task_log_rows(self.conn, "E2E-MAIN", 50, *TL_RANGE_A)
        self.conn.commit()

        # ---- Stage 1: archive_task_log creates a batch in Pending status ----
        batch_state: dict = {}
        new_job = "ARCH-29998"

        def _mock_get_doc_create(first, *rest, **kw):
            if isinstance(first, str) and first == "Memora Archive Job":
                return SimpleNamespace(
                    name=new_job,
                    source_doctype="Memora Task Run Log",
                    job_meta=json.dumps({
                        "query_filter": {
                            "date_from": TL_RANGE_A[0],
                            "date_to": TL_RANGE_A[1],
                            "cutoff_date": "2025-12-01",
                        }
                    }),
                )
            if isinstance(first, dict) and first.get("doctype") == "Memora Task Log Archive Batch":
                batch_state.update(first)
                batch_state.setdefault("name", "TLBATCH-E2E-001")
                doc = MagicMock()
                doc.name = batch_state["name"]
                return doc
            return MagicMock()

        with (
            patch(f"{_ARC}.create_pending_jobs", return_value=[new_job]),
            patch("frappe.get_all", return_value=[]),
            patch("frappe.get_doc", side_effect=_mock_get_doc_create),
            patch("frappe.db.set_value"),
            patch("frappe.db.commit"),
            patch("frappe.log_error"),
        ):
            archive_task_log(triggered_by="Test")

        assert batch_state.get("status") == "Pending"
        assert batch_state.get("archive_job_id") == new_job

        # ---- Stage 2: simulate archive executor completing the job ----
        job_status = {"status": "Completed", "file_path": "/tmp/x", "file_checksum": "c" * 64, "row_count": 50}

        # ---- Stage 3: archive_task_log syncs batch to Synced ----
        def _get_all_stage3(doctype, filters=None, fields=None, **kw):
            if doctype == "Memora Task Log Archive Batch":
                s = (filters or {}).get("status")
                allowed = s[1] if isinstance(s, list) else [s] if s else [batch_state["status"]]
                if batch_state["status"] in allowed:
                    return [SimpleNamespace(
                        name=batch_state["name"],
                        archive_job_id=new_job,
                        status=batch_state["status"],
                    )]
            return []

        def _set_value_stage3(doctype, name, vals):
            if doctype == "Memora Task Log Archive Batch":
                batch_state.update(vals)

        with (
            patch("frappe.get_all", side_effect=_get_all_stage3),
            patch("frappe.db.get_value", return_value=dict(job_status)),
            patch("frappe.db.set_value", side_effect=_set_value_stage3),
            patch("frappe.db.commit"),
            patch(f"{_ARC}.create_pending_jobs", return_value=[]),
            patch("frappe.log_error"),
            patch("frappe.get_doc"),
        ):
            archive_task_log(triggered_by="Test")

        assert batch_state["status"] == "Synced"
        assert "synced_at" in batch_state

        # ---- Stage 4: purge source rows directly via _purge_sub_batch ----
        # (purge_task_log() orchestration is covered by TestPurgeTaskLog; here we
        #  verify that the source rows are actually removed from the real DB)
        db_wrapper = _FrappeDbLike(self.conn)
        total_purged = 0
        while True:
            n = _purge_sub_batch(
                db_wrapper, SOURCE_TABLE, *TL_RANGE_A, RETENTION_DAYS, TERMINAL_STATUSES
            )
            total_purged += n
            if n == 0:
                break

        # Source rows must be gone
        assert _count_task_log_rows_by_prefix(self.conn, "E2E-MAIN") == 0
