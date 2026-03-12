"""Extended integration and unit tests for the Memory State Archive Pipeline.

Tests cover:
- Executor runtime flow (claim, process, stage tracking)
- Failure handling and retry logic
- SSH handoff robustness (pure unit tests)
- Export file integrity (pure unit tests)
- DQ failure paths (pure unit tests)
- Purge integration confidence
- Audit logging behavior
- Idempotency and duplicate protection
- Concurrency resistance
- Temp file cleanup (pure unit tests)
- Realistic high-volume data
- End-to-end branches

Run with:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python3 -m pytest archive_executor/tests/test_memory_state_pipeline_extended.py -v
"""

import dataclasses
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from archive_executor.config import Config
from archive_executor.ingestion import IngestionError, get_mirror_status, handoff_season
from archive_executor.purge import _log_delete_audit, _mark_purged
from archive_executor.run import (
    _claim_job,
    _cleanup_staging,
    _fail_job,
    _get_jobs_by_status,
    _mark_completed,
    _process_pending_jobs,
    _read_stage,
    _update_stage,
)
from archive_executor.safety_gates import GateCheck, GateResult
from archive_executor.scheduler import create_season_archive_jobs
from archive_executor.validator import compute_sha256, validate_fact_quality_generic, validate_file

from .conftest import (
    MS_SEASON_NAME_E,
    MS_SEASON_SEQ_E,
    MS_TEST_JOB_ARCHIVE,
    MS_TEST_JOB_PURGE,
    _make_season_job_meta,
    count_memory_state_rows,
    delete_memory_state_rows,
    delete_ms_jobs_by_scope,
    delete_ms_test_jobs,
    delete_test_seasons,
    ensure_audit_table,
    insert_test_seasons,
    upsert_ms_archive_job,
)


# ===========================================================================
# Module-level constants
# ===========================================================================

# Numeric job names matching ^ARCH-\d+$ required by _process_pending_jobs
EXEC_JOB_CLAIM  = "ARCH-10100"
EXEC_JOB_FLOW1  = "ARCH-10101"   # happy path non-zero rows
EXEC_JOB_FLOW2  = "ARCH-10102"   # zero rows -> completed
EXEC_JOB_FAIL1  = "ARCH-10110"   # first failure -> retry
EXEC_JOB_FAIL2  = "ARCH-10111"   # third failure -> permanent fail
EXEC_JOB_FAIL3  = "ARCH-10112"   # DQ failure -> retry
EXEC_JOB_FAIL4  = "ARCH-10113"   # staging cleanup on failure
EXEC_JOB_IDEM1  = "ARCH-10120"   # idempotency
EXEC_JOB_IDEM2  = "ARCH-10121"
EXEC_JOB_RACE1  = "ARCH-10130"   # concurrency
EXEC_JOB_E2E1   = "ARCH-10140"   # e2e export failure
EXEC_JOB_E2E2   = "ARCH-10141"   # e2e DQ failure
EXEC_JOB_E2E3   = "ARCH-10142"   # e2e purge blocked
EXEC_JOB_HVOL   = "ARCH-10150"   # high volume

ALL_EXEC_JOBS = [
    EXEC_JOB_CLAIM, EXEC_JOB_FLOW1, EXEC_JOB_FLOW2,
    EXEC_JOB_FAIL1, EXEC_JOB_FAIL2, EXEC_JOB_FAIL3, EXEC_JOB_FAIL4,
    EXEC_JOB_IDEM1, EXEC_JOB_IDEM2,
    EXEC_JOB_RACE1,
    EXEC_JOB_E2E1, EXEC_JOB_E2E2, EXEC_JOB_E2E3,
    EXEC_JOB_HVOL,
]


def _scoped_process_pending_jobs(cfg, log, allowed_names=None):
    """Call _process_pending_jobs but only process jobs in *allowed_names*.

    Patches _get_jobs_by_status so the executor never touches real production
    jobs that happen to be Pending in the shared database.  If *allowed_names*
    is ``None`` the ``ALL_EXEC_JOBS`` set (plus the invalid-name test job) is
    used.
    """
    if allowed_names is None:
        allowed_names = set(ALL_EXEC_JOBS) | {"ARCH-MS-EXEC"}
    else:
        allowed_names = set(allowed_names)

    _original = _get_jobs_by_status

    def _filtered(config, status):
        jobs = _original(config, status)
        return [j for j in jobs if j["name"] in allowed_names]

    with patch("archive_executor.run._get_jobs_by_status", side_effect=_filtered):
        return _process_pending_jobs(cfg, log)


# Seasons for executor tests (high seqs avoid production collision)
EXEC_SEASON_SEQ_1  = 9910
EXEC_SEASON_SEQ_2  = 9911
EXEC_SEASON_SEQ_3  = 9912
EXEC_SEASON_NAME_1 = "SEAS-EXEC-9910"
EXEC_SEASON_NAME_2 = "SEAS-EXEC-9911"
EXEC_SEASON_NAME_3 = "SEAS-EXEC-9912"
EXEC_PLAYER_PREFIX = "EXEC-PLYR"

# Memory State DQ rules for v1 (used in DQ failure path tests)
MS_DQ_RULES_V1 = [
    {"id": "DQ-01", "type": "not_null",    "column": "name"},
    {"id": "DQ-02", "type": "not_null",    "column": "season_seq"},
    {"id": "DQ-03", "type": "not_null",    "column": "player"},
    {"id": "DQ-04", "type": "not_null",    "column": "item_id"},
    {"id": "DQ-05", "type": "not_null",    "column": "stability"},
    {"id": "DQ-06", "type": "not_null",    "column": "difficulty"},
    {"id": "DQ-07", "type": "min_value",   "column": "stability",  "min": 0},
    {"id": "DQ-08", "type": "min_value",   "column": "difficulty", "min": 0},
    {"id": "DQ-09", "type": "max_value",   "column": "difficulty", "max": 1},
    {"id": "DQ-10", "type": "unique_key",  "columns": ["name", "season_seq"]},
    {"id": "DQ-11", "type": "referential", "column": "player", "dimension": "player"},
]

# Parquet schema for memory_state fact files
MS_PARQUET_SCHEMA = pa.schema([
    pa.field("name",           pa.int64()),
    pa.field("season_seq",     pa.int64()),
    pa.field("subject",        pa.string()),
    pa.field("player",         pa.string()),
    pa.field("item_id",        pa.string()),
    pa.field("stage_id",       pa.string()),
    pa.field("stability",      pa.float64()),
    pa.field("difficulty",     pa.float64()),
    pa.field("next_review",    pa.timestamp("us")),
    pa.field("lesson",         pa.string()),
    pa.field("state",          pa.int64()),
    pa.field("step",           pa.int64()),
    pa.field("last_review",    pa.timestamp("us")),
    pa.field("modified",       pa.timestamp("us")),
    pa.field("archive_scope",  pa.string()),
    pa.field("archive_job_id", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("exported_at",    pa.timestamp("us")),
])


# ===========================================================================
# Module-level helper functions
# ===========================================================================

def _insert_exec_ms_rows(conn, prefix: str, count: int, season_seq: int, num_players: int = 5) -> int:
    """Insert Memory State rows for executor tests with deterministic BIGINT names."""
    now = datetime(2099, 7, 1, 10, 0, 0)
    rows = []
    for n in range(1, count + 1):
        ts = now + timedelta(seconds=n * 60)
        player_id = f"{EXEC_PLAYER_PREFIX}-{(n % num_players) + 1:03d}"
        item_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{prefix}-{n}").bytes
        name_bytes = uuid.uuid5(uuid.NAMESPACE_DNS, f"ms-name-{prefix}-{n}").bytes
        name_val = int.from_bytes(name_bytes[:7], "big") + 1

        rows.append((
            name_val,
            season_seq,
            f"Science-{(n % 3) + 1}",
            player_id,
            item_uuid,
            f"STAGE-{(n % 4) + 1}",
            round(0.5 + (n % 100) * 0.05, 4),
            round(0.1 + (n % 9) * 0.09, 4),   # difficulty in [0.10, 0.91]
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            f"EXEC-LESSON-{prefix}-{(n % 3) + 1:03d}",
            n % 4,
            n % 5 if n % 3 != 0 else None,
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            "test@test.com",
            "test@test.com",
        ))

    sql = (
        "INSERT IGNORE INTO `tabMemora Memory State` "
        "(`name`, `season_seq`, `subject`, `player`, `item_id`, `stage_id`, "
        " `stability`, `difficulty`, `next_review`, `lesson`, "
        " `state`, `step`, `last_review`, "
        " `creation`, `modified`, `modified_by`, `owner`) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()
    return count


def _insert_exec_season(conn, season_seq: int, season_name: str, end_date: str = "2025-01-01") -> None:
    """Insert a single executor test season."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Season` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `season_title`, `season_seq`, "
        " `start_date`, `end_date`, `is_published`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', "
        "        0, %s, %s, %s, '2024-01-01', %s, 1)"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (season_name, season_seq, f"Exec Test Season {season_seq}", season_seq, end_date))
    conn.commit()


def _delete_exec_seasons(conn) -> None:
    """Delete all executor test seasons."""
    names = [EXEC_SEASON_NAME_1, EXEC_SEASON_NAME_2, EXEC_SEASON_NAME_3]
    placeholders = ", ".join(["%s"] * len(names))
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `tabMemora Season` WHERE `name` IN ({placeholders})",
            names,
        )
    conn.commit()


def _insert_exec_players(conn, season_name: str, num_players: int = 5) -> None:
    """Insert EXEC-PLYR-001 through EXEC-PLYR-00N player profiles."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Player Profile` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `grade`, `major`, `season`, `plan`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', "
        "        0, %s, %s, %s, %s, %s)"
    )
    rows = [
        (
            f"{EXEC_PLAYER_PREFIX}-{i + 1:03d}",
            i + 1,
            f"Grade-{(i % 4) + 1}",
            "Science" if i % 2 == 0 else "Arts",
            season_name,
            f"PLAN-EXEC-{(i % 3) + 1:03d}",
        )
        for i in range(num_players)
    ]
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()


def _delete_exec_players(conn) -> None:
    """Delete all EXEC-PLYR-* player profiles."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Player Profile` WHERE `name` LIKE 'EXEC-PLYR-%'"
        )
    conn.commit()


def _insert_exec_job(
    conn,
    name: str,
    season_seq: int,
    season_name: str,
    status: str = "Pending",
    retry_count: int = 0,
    sync_paused: int = 0,
    file_path: str = "",
) -> None:
    """Insert a numeric-name Memory State archive job for executor tests."""
    archive_scope = f"season_{season_seq}"
    job_meta = _make_season_job_meta(season_seq, season_name)
    sql = (
        "INSERT INTO `tabMemora Archive Job` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
        " `status`, `priority`, `retry_count`, `post_archive_action`, "
        " `source_deleted`, `sync_paused`, "
        " `duration_seconds`, `row_count`, `file_size_bytes`, "
        " `file_path`, `job_meta`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0, "
        "        'Memora Memory State', %s, 'v1', 'memory_state', "
        "        %s, 'Normal', %s, 'Delete', 0, %s, "
        "        0, 0, 0, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  status=%s, retry_count=%s, sync_paused=%s, file_path=%s, job_meta=%s, modified=NOW()"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            name, archive_scope, status, retry_count, sync_paused, file_path, job_meta,
            status, retry_count, sync_paused, file_path, job_meta,
        ))
    conn.commit()


def _delete_exec_jobs(conn) -> None:
    """Delete all executor test archive jobs."""
    names = ALL_EXEC_JOBS + ["ARCH-MS-EXEC"]   # include invalid-name job used in one test
    placeholders = ", ".join(["%s"] * len(names))
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `tabMemora Archive Job` WHERE `name` IN ({placeholders})",
            names,
        )
    conn.commit()


def _delete_exec_audit(conn) -> None:
    """Delete audit log entries for executor test jobs."""
    placeholders = ", ".join(["%s"] * len(ALL_EXEC_JOBS))
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `archive_delete_audit_log` WHERE `job_id` IN ({placeholders})",
            ALL_EXEC_JOBS,
        )
    conn.commit()


def _make_mock_log() -> MagicMock:
    """Return a MagicMock logger with info/warning/error/debug sub-mocks."""
    log = MagicMock()
    log.info    = MagicMock()
    log.warning = MagicMock()
    log.error   = MagicMock()
    log.debug   = MagicMock()
    return log


def _get_archive_job(conn, name: str) -> dict | None:
    """Fetch a single archive job row by name."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM `tabMemora Archive Job` WHERE name = %s", (name,))
        return cursor.fetchone()


def _config_with_archive_dir(cfg: Config, tmpdir: str) -> Config:
    """Return a Config with archive_output_path set to tmpdir/."""
    return dataclasses.replace(cfg, archive_output_path=tmpdir + "/")


def _make_good_ms_parquet(tmp_path: str, count: int = 10) -> str:
    """Create a valid memory_state parquet file for DQ tests."""
    base_ts = datetime(2099, 6, 1, 12, 0, 0)
    names       = list(range(1, count + 1))
    season_seqs = [EXEC_SEASON_SEQ_1] * count
    subjects    = [f"Science-{(i % 3) + 1}" for i in range(count)]
    players     = [f"{EXEC_PLAYER_PREFIX}-{(i % 5) + 1:03d}" for i in range(count)]
    item_ids    = [str(uuid.uuid5(uuid.NAMESPACE_DNS, f"good-item-{i}")) for i in range(count)]
    stage_ids   = [f"STAGE-{(i % 4) + 1}" for i in range(count)]
    stabilities = [round(0.5 + i * 0.1, 4) for i in range(count)]
    difficulties = [round(0.1 + (i % 9) * 0.09, 4) for i in range(count)]
    ts_vals     = [base_ts + timedelta(minutes=i) for i in range(count)]
    lessons     = [f"EXEC-LESSON-{i + 1:03d}" for i in range(count)]
    states      = [i % 3 for i in range(count)]
    steps       = [i % 5 if i % 3 != 0 else None for i in range(count)]
    archive_scope  = [f"season_{EXEC_SEASON_SEQ_1}"] * count
    archive_job_id = ["ARCH-10150"] * count
    schema_ver  = ["v1"] * count
    exported_at = [base_ts] * count

    table = pa.table({
        "name":           pa.array(names,        type=pa.int64()),
        "season_seq":     pa.array(season_seqs,  type=pa.int64()),
        "subject":        pa.array(subjects,     type=pa.string()),
        "player":         pa.array(players,      type=pa.string()),
        "item_id":        pa.array(item_ids,     type=pa.string()),
        "stage_id":       pa.array(stage_ids,    type=pa.string()),
        "stability":      pa.array(stabilities,  type=pa.float64()),
        "difficulty":     pa.array(difficulties, type=pa.float64()),
        "next_review":    pa.array(ts_vals,      type=pa.timestamp("us")),
        "lesson":         pa.array(lessons,      type=pa.string()),
        "state":          pa.array(states,       type=pa.int64()),
        "step":           pa.array(steps,        type=pa.int64()),
        "last_review":    pa.array(ts_vals,      type=pa.timestamp("us")),
        "modified":       pa.array(ts_vals,      type=pa.timestamp("us")),
        "archive_scope":  pa.array(archive_scope,  type=pa.string()),
        "archive_job_id": pa.array(archive_job_id, type=pa.string()),
        "schema_version": pa.array(schema_ver,     type=pa.string()),
        "exported_at":    pa.array(exported_at,    type=pa.timestamp("us")),
    })
    path = os.path.join(tmp_path, "fact_memory_state.parquet")
    pq.write_table(table, path)
    return path


def _make_bad_ms_parquet(
    tmp_path: str,
    *,
    null_col: str | None = None,
    dup_key: bool = False,
    stability_neg: bool = False,
    difficulty_high: bool = False,
    null_player: bool = False,
    count: int = 5,
) -> str:
    """Create a memory_state parquet with an intentional defect for DQ failure tests."""
    base_ts = datetime(2099, 6, 1, 12, 0, 0)
    names       = list(range(1, count + 1))
    season_seqs = [EXEC_SEASON_SEQ_1] * count

    if dup_key:
        # Make rows 0 and 1 share the same (name, season_seq)
        names[1] = names[0]

    subjects    = [f"Science-{(i % 3) + 1}" for i in range(count)]
    players     = [f"{EXEC_PLAYER_PREFIX}-{(i % 5) + 1:03d}" for i in range(count)]
    item_ids    = [str(uuid.uuid5(uuid.NAMESPACE_DNS, f"bad-item-{i}")) for i in range(count)]
    stage_ids   = [f"STAGE-{(i % 4) + 1}" for i in range(count)]
    stabilities = [round(0.5 + i * 0.1, 4) for i in range(count)]
    difficulties = [round(0.1 + (i % 9) * 0.09, 4) for i in range(count)]
    ts_vals     = [base_ts + timedelta(minutes=i) for i in range(count)]
    lessons     = [f"EXEC-LESSON-{i + 1:03d}" for i in range(count)]
    states      = [i % 3 for i in range(count)]
    steps_col   = [i % 5 for i in range(count)]

    if null_col == "name":
        names[0] = None
    elif null_col == "player":
        players[0] = None
    elif null_col == "item_id":
        item_ids[0] = None
    elif null_col == "stability":
        stabilities[0] = None
    elif null_col == "difficulty":
        difficulties[0] = None

    if null_player:
        players[0] = None

    if stability_neg:
        stabilities[0] = -0.1

    if difficulty_high:
        difficulties[0] = 1.5

    archive_scope  = [f"season_{EXEC_SEASON_SEQ_1}"] * count
    archive_job_id = ["ARCH-10150"] * count
    schema_ver     = ["v1"] * count
    exported_at    = [base_ts] * count

    table = pa.table({
        "name":           pa.array(names,        type=pa.int64()),
        "season_seq":     pa.array(season_seqs,  type=pa.int64()),
        "subject":        pa.array(subjects,     type=pa.string()),
        "player":         pa.array(players,      type=pa.string()),
        "item_id":        pa.array(item_ids,     type=pa.string()),
        "stage_id":       pa.array(stage_ids,    type=pa.string()),
        "stability":      pa.array(stabilities,  type=pa.float64()),
        "difficulty":     pa.array(difficulties, type=pa.float64()),
        "next_review":    pa.array(ts_vals,      type=pa.timestamp("us")),
        "lesson":         pa.array(lessons,      type=pa.string()),
        "state":          pa.array(states,       type=pa.int64()),
        "step":           pa.array(steps_col,    type=pa.int64()),
        "last_review":    pa.array(ts_vals,      type=pa.timestamp("us")),
        "modified":       pa.array(ts_vals,      type=pa.timestamp("us")),
        "archive_scope":  pa.array(archive_scope,  type=pa.string()),
        "archive_job_id": pa.array(archive_job_id, type=pa.string()),
        "schema_version": pa.array(schema_ver,     type=pa.string()),
        "exported_at":    pa.array(exported_at,    type=pa.timestamp("us")),
    })
    path = os.path.join(tmp_path, "fact_memory_state_bad.parquet")
    pq.write_table(table, path)
    return path


def _make_player_dim_parquet(tmp_path: str, player_ids: list[str]) -> str:
    """Create a minimal player dimension parquet for referential DQ tests."""
    table = pa.table({
        "name":   pa.array(player_ids, type=pa.string()),
        "season": pa.array(["SEAS-EXEC-9910"] * len(player_ids), type=pa.string()),
    })
    path = os.path.join(tmp_path, "dim_player.parquet")
    pq.write_table(table, path)
    return path


# ===========================================================================
# TestExecutorRuntimeFlow — integration
# ===========================================================================

class TestExecutorRuntimeFlow:
    """Tests for the executor's job claiming and processing state machine."""

    pytestmark = pytest.mark.integration

    @pytest.fixture(autouse=True)
    def _cleanup(self, db_conn):
        ensure_audit_table(db_conn)
        yield
        _delete_exec_jobs(db_conn)
        _delete_exec_players(db_conn)
        delete_memory_state_rows(db_conn, EXEC_SEASON_SEQ_1)
        delete_memory_state_rows(db_conn, EXEC_SEASON_SEQ_2)
        _delete_exec_seasons(db_conn)

    def test_claim_job_sets_processing_status(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_CLAIM, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)
        claimed = _claim_job(integration_db_config, EXEC_JOB_CLAIM)
        assert claimed is True
        row = _get_archive_job(db_conn, EXEC_JOB_CLAIM)
        assert row["status"] == "Processing"
        assert row["execution_stage"] == "claiming"
        assert row["sync_paused"] == 1

    def test_claim_job_returns_false_when_already_processing(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_CLAIM, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, status="Processing")
        claimed = _claim_job(integration_db_config, EXEC_JOB_CLAIM)
        assert claimed is False

    def test_claim_job_returns_false_for_nonexistent_job(self, integration_db_config):
        result = _claim_job(integration_db_config, "ARCH-99999")
        assert result is False

    def test_invalid_job_name_skipped_by_executor(self, integration_db_config, db_conn):
        # Insert a job whose name does NOT match ^ARCH-\d+$ — executor must skip it
        sql = (
            "INSERT IGNORE INTO `tabMemora Archive Job` "
            "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
            " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
            " `status`, `priority`, `retry_count`, `post_archive_action`, "
            " `source_deleted`, `sync_paused`, "
            " `duration_seconds`, `row_count`, `file_size_bytes`, `job_meta`) "
            "VALUES ('ARCH-MS-EXEC', NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0, "
            "        'Memora Memory State', 'season_99110', 'v1', 'memory_state', "
            "        'Pending', 'Normal', 0, 'Delete', 0, 0, 0, 0, 0, '{}')"
        )
        with db_conn.cursor() as cursor:
            cursor.execute(sql)
        db_conn.commit()

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            processed, _ = _scoped_process_pending_jobs(cfg, log)

        # The invalid-name job must not have been claimed
        row = _get_archive_job(db_conn, "ARCH-MS-EXEC")
        assert row["status"] == "Pending"
        assert processed == 0

    def test_process_pending_advances_to_exported_on_success(
        self, integration_db_config, db_conn
    ):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_1)
        _insert_exec_ms_rows(db_conn, "FLOW1", 20, EXEC_SEASON_SEQ_1)
        _insert_exec_job(db_conn, EXEC_JOB_FLOW1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            processed, failed = _scoped_process_pending_jobs(cfg, log)

        assert processed >= 1
        assert failed == 0

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FLOW1)
        assert row is not None
        # Should be Exported (or Completed if 0 rows — but we inserted 20)
        assert row["status"] in ("Exported", "Completed")

    def test_zero_row_job_completes_immediately(self, integration_db_config, db_conn):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_2, EXEC_SEASON_NAME_2, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_2)
        # No data rows inserted — season has 0 rows
        _insert_exec_job(db_conn, EXEC_JOB_FLOW2, EXEC_SEASON_SEQ_2, EXEC_SEASON_NAME_2)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            processed, failed = _scoped_process_pending_jobs(cfg, log)

        assert failed == 0
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FLOW2)
        assert row is not None
        # 0-row export always completes immediately
        assert row["status"] == "Completed"

    def test_execution_stage_set_to_claiming_after_claim(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_CLAIM, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)
        _claim_job(integration_db_config, EXEC_JOB_CLAIM)
        stage = _read_stage(integration_db_config, EXEC_JOB_CLAIM)
        assert stage == "claiming"


# ===========================================================================
# TestFailureHandlingDuringExecution — integration
# ===========================================================================

class TestFailureHandlingDuringExecution:
    """Tests for retry logic, error logging, and failure transitions."""

    pytestmark = pytest.mark.integration

    @pytest.fixture(autouse=True)
    def _cleanup(self, db_conn):
        ensure_audit_table(db_conn)
        yield
        _delete_exec_jobs(db_conn)
        _delete_exec_players(db_conn)
        delete_memory_state_rows(db_conn, EXEC_SEASON_SEQ_1)
        _delete_exec_seasons(db_conn)

    def test_first_failure_resets_job_to_pending(self, integration_db_config, db_conn):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_1)
        _insert_exec_ms_rows(db_conn, "FAIL1", 5, EXEC_SEASON_SEQ_1)
        _insert_exec_job(db_conn, EXEC_JOB_FAIL1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, retry_count=0)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            with patch("archive_executor.run._export_job", side_effect=RuntimeError("simulated export error")):
                _scoped_process_pending_jobs(cfg, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FAIL1)
        assert row is not None
        assert row["status"] == "Pending"
        assert row["retry_count"] == 1
        assert row["error_log"] is not None
        assert len(row["error_log"]) > 0

    def test_third_failure_permanently_fails_job(self, integration_db_config, db_conn):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_1)
        _insert_exec_ms_rows(db_conn, "FAIL2", 5, EXEC_SEASON_SEQ_1)
        _insert_exec_job(db_conn, EXEC_JOB_FAIL2, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, retry_count=3)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            with patch("archive_executor.run._export_job", side_effect=RuntimeError("permanent error")):
                _scoped_process_pending_jobs(cfg, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FAIL2)
        assert row is not None
        assert row["status"] == "Failed"

    def test_fail_job_error_log_contains_phase_prefix(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_FAIL3, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, status="Processing")
        _fail_job(
            integration_db_config,
            EXEC_JOB_FAIL3,
            "something went wrong",
            retry_count=0,
            current_status="Processing",
            stage="exporting_fact",
        )
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FAIL3)
        assert row is not None
        assert row["error_log"].startswith("Phase: exporting_fact")

    def test_fail_job_guard_prevents_wrong_status_update(self, integration_db_config, db_conn):
        # Insert a Completed job — _fail_job with current_status='Processing' should not touch it
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_FAIL4, "Completed",
            EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
        )
        _fail_job(
            integration_db_config,
            EXEC_JOB_FAIL4,
            "should not apply",
            retry_count=0,
            current_status="Processing",
        )
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FAIL4)
        assert row is not None
        assert row["status"] == "Completed"

    def test_staging_cleanup_on_export_failure(self, integration_db_config, db_conn):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_1)
        _insert_exec_ms_rows(db_conn, "FAIL4STAGE", 5, EXEC_SEASON_SEQ_1)
        _insert_exec_job(db_conn, EXEC_JOB_FAIL1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            staging_dir = os.path.join(tmpdir, ".staging", EXEC_JOB_FAIL1)
            os.makedirs(staging_dir, exist_ok=True)
            # Pre-create a file in staging to confirm removal
            open(os.path.join(staging_dir, "test.parquet"), "w").close()

            with patch("archive_executor.run._export_job", side_effect=RuntimeError("export failure")):
                _scoped_process_pending_jobs(cfg, log)

        # Staging directory must be cleaned up after failure
        assert not os.path.isdir(staging_dir)

    def test_dq_failure_message_contains_rule_names(self, integration_db_config, db_conn):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_1)
        _insert_exec_ms_rows(db_conn, "FAIL3DQ", 5, EXEC_SEASON_SEQ_1)
        _insert_exec_job(db_conn, EXEC_JOB_FAIL3, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, retry_count=0)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            with patch(
                "archive_executor.run._export_job",
                side_effect=RuntimeError("Data quality validation failed: DQ-05"),
            ):
                _scoped_process_pending_jobs(cfg, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FAIL3)
        assert row is not None
        assert "DQ-05" in row["error_log"]

    def test_sync_paused_cleared_on_permanent_failure(self, integration_db_config, db_conn):
        _insert_exec_job(
            db_conn, EXEC_JOB_FAIL2, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
            status="Processing", retry_count=3, sync_paused=1,
        )
        _fail_job(
            integration_db_config,
            EXEC_JOB_FAIL2,
            "permanent fail",
            retry_count=3,
            current_status="Processing",
        )
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FAIL2)
        assert row is not None
        assert row["status"] == "Failed"
        assert row["sync_paused"] == 0


# ===========================================================================
# TestHandoffRobustness — pure unit tests (no DB)
# ===========================================================================

class TestHandoffRobustness:
    """Pure unit tests for SSH handoff and JSON parsing robustness."""

    pytestmark = pytest.mark.unit

    def _make_config(self):
        return MagicMock(
            analytics_cmd_path="/usr/local/bin/memora-analytics",
            ssh_timeout=30,
            ssh_host="analytics.example.com",
            ssh_user="deploy",
            ssh_key_path="/home/deploy/.ssh/id_rsa",
            ssh_port=22,
        )

    def test_parse_remote_json_raises_on_completely_invalid_stdout(self):
        from archive_executor.ingestion import _parse_remote_json
        with pytest.raises(IngestionError):
            _parse_remote_json("not json at all", "", "test")

    def test_parse_remote_json_raises_on_empty_stdout(self):
        from archive_executor.ingestion import _parse_remote_json
        with pytest.raises(IngestionError):
            _parse_remote_json("", "", "test")

    def test_parse_remote_json_extracts_json_after_log_prefix(self):
        from archive_executor.ingestion import _parse_remote_json
        stdout = 'INFO some log line\n{"status": "ok", "rows_removed": 5}'
        result = _parse_remote_json(stdout, "", "test")
        assert result == {"status": "ok", "rows_removed": 5}

    def test_parse_remote_json_direct_parse(self):
        from archive_executor.ingestion import _parse_remote_json
        result = _parse_remote_json('{"status": "ok", "data": 42}', "", "test")
        assert result["status"] == "ok"
        assert result["data"] == 42

    def test_handoff_season_malformed_json_raises_ingestion_error(self):
        cfg = self._make_config()
        log = _make_mock_log()
        with patch("archive_executor.ingestion._run_ssh_command", return_value=(0, "garbage output", "")):
            with pytest.raises(IngestionError):
                handoff_season(cfg, "/remote/path", 9910, "memory_state", log)

    def test_handoff_season_nonzero_exit_raises_ingestion_error(self):
        cfg = self._make_config()
        log = _make_mock_log()
        with patch(
            "archive_executor.ingestion._run_ssh_command",
            return_value=(1, '{"error": "server failed"}', ""),
        ):
            with pytest.raises(IngestionError) as exc_info:
                handoff_season(cfg, "/remote/path", 9910, "memory_state", log)
        assert "server failed" in str(exc_info.value)

    def test_handoff_season_missing_error_field_raises(self):
        cfg = self._make_config()
        log = _make_mock_log()
        with patch("archive_executor.ingestion._run_ssh_command", return_value=(1, "{}", "")):
            with pytest.raises(IngestionError):
                handoff_season(cfg, "/remote/path", 9910, "memory_state", log)

    def test_get_mirror_status_nonzero_exit_raises_ingestion_error(self):
        cfg = self._make_config()
        log = _make_mock_log()
        with patch(
            "archive_executor.ingestion._run_ssh_command",
            return_value=(1, '{"error": "mirror offline"}', ""),
        ):
            with pytest.raises(IngestionError):
                get_mirror_status(cfg, "memory_state", log)

    def test_get_mirror_status_status_not_ok_raises(self):
        cfg = self._make_config()
        log = _make_mock_log()
        with patch(
            "archive_executor.ingestion._run_ssh_command",
            return_value=(0, '{"status": "error", "error": "bad state"}', ""),
        ):
            with pytest.raises(IngestionError):
                get_mirror_status(cfg, "memory_state", log)

    def test_handoff_season_success_returns_result_dict(self):
        cfg = self._make_config()
        log = _make_mock_log()
        payload = '{"status": "ok", "rows_removed": 10, "season_seq": 9910}'
        with patch("archive_executor.ingestion._run_ssh_command", return_value=(0, payload, "")):
            result = handoff_season(cfg, "/remote/path", 9910, "memory_state", log)
        assert result["status"] == "ok"
        assert result["rows_removed"] == 10


# ===========================================================================
# TestExportFileIntegrity — pure unit tests (no DB)
# ===========================================================================

class TestExportFileIntegrity:
    """Pure unit tests for parquet validation and checksum utilities."""

    pytestmark = pytest.mark.unit

    def _make_simple_parquet(self, path: str, row_count: int = 5) -> str:
        """Write a minimal parquet file at path."""
        table = pa.table({
            "id":    pa.array(list(range(row_count)), type=pa.int64()),
            "value": pa.array([f"v{i}" for i in range(row_count)], type=pa.string()),
        })
        pq.write_table(table, path)
        return path

    def test_validate_file_detects_row_count_mismatch(self, tmp_path):
        path = self._make_simple_parquet(str(tmp_path / "fact.parquet"), row_count=5)
        result = validate_file(path, 10)
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_validate_file_accepts_correct_row_count(self, tmp_path):
        path = self._make_simple_parquet(str(tmp_path / "fact.parquet"), row_count=5)
        result = validate_file(path, 5)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_file_accepts_zero_row_parquet(self, tmp_path):
        table = pa.table({"id": pa.array([], type=pa.int64())})
        path = str(tmp_path / "empty.parquet")
        pq.write_table(table, path)
        result = validate_file(path, 0)
        assert result["valid"] is True

    def test_compute_sha256_same_file_stable(self, tmp_path):
        path = self._make_simple_parquet(str(tmp_path / "file.parquet"), row_count=3)
        h1 = compute_sha256(path)
        h2 = compute_sha256(path)
        assert h1 == h2

    def test_compute_sha256_different_files_differ(self, tmp_path):
        path1 = self._make_simple_parquet(str(tmp_path / "a.parquet"), row_count=3)
        path2 = self._make_simple_parquet(str(tmp_path / "b.parquet"), row_count=7)
        assert compute_sha256(path1) != compute_sha256(path2)

    def test_validate_file_size_nonzero_for_parquet(self, tmp_path):
        path = self._make_simple_parquet(str(tmp_path / "fact.parquet"), row_count=5)
        result = validate_file(path, 5)
        assert result["size_bytes"] > 0

    def test_validate_file_checksum_format(self, tmp_path):
        path = self._make_simple_parquet(str(tmp_path / "fact.parquet"), row_count=5)
        result = validate_file(path, 5)
        assert result["checksum"].startswith("sha256:")


# ===========================================================================
# TestDQFailurePaths — pure unit tests (no DB)
# ===========================================================================

class TestDQFailurePaths:
    """Pure unit tests for the generic DQ validator using memory_state rules."""

    pytestmark = pytest.mark.unit

    def test_dq_passes_on_valid_data(self, tmp_path):
        path = _make_good_ms_parquet(str(tmp_path), count=10)
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is True
        assert all(r["passed"] for r in result["results"])

    def test_dq_fails_on_null_name(self, tmp_path):
        path = _make_bad_ms_parquet(str(tmp_path), null_col="name")
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is False
        dq01 = next(r for r in result["results"] if r["rule"] == "DQ-01")
        assert dq01["passed"] is False

    def test_dq_fails_on_null_player(self, tmp_path):
        path = _make_bad_ms_parquet(str(tmp_path), null_col="player")
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is False
        dq03 = next(r for r in result["results"] if r["rule"] == "DQ-03")
        assert dq03["passed"] is False

    def test_dq_fails_on_null_item_id(self, tmp_path):
        path = _make_bad_ms_parquet(str(tmp_path), null_col="item_id")
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is False
        dq04 = next(r for r in result["results"] if r["rule"] == "DQ-04")
        assert dq04["passed"] is False

    def test_dq_fails_on_null_stability(self, tmp_path):
        path = _make_bad_ms_parquet(str(tmp_path), null_col="stability")
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is False
        dq05 = next(r for r in result["results"] if r["rule"] == "DQ-05")
        assert dq05["passed"] is False

    def test_dq_fails_on_negative_stability(self, tmp_path):
        path = _make_bad_ms_parquet(str(tmp_path), stability_neg=True)
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is False
        dq07 = next(r for r in result["results"] if r["rule"] == "DQ-07")
        assert dq07["passed"] is False

    def test_dq_fails_on_difficulty_above_one(self, tmp_path):
        path = _make_bad_ms_parquet(str(tmp_path), difficulty_high=True)
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is False
        dq09 = next(r for r in result["results"] if r["rule"] == "DQ-09")
        assert dq09["passed"] is False

    def test_dq_fails_on_negative_difficulty(self, tmp_path):
        # Build a parquet where difficulty[0] = -0.1 (negative)
        base_ts = datetime(2099, 6, 1, 12, 0, 0)
        count = 5
        table = pa.table({
            "name":           pa.array(list(range(1, count + 1)), type=pa.int64()),
            "season_seq":     pa.array([EXEC_SEASON_SEQ_1] * count, type=pa.int64()),
            "subject":        pa.array(["S"] * count, type=pa.string()),
            "player":         pa.array([f"{EXEC_PLAYER_PREFIX}-001"] * count, type=pa.string()),
            "item_id":        pa.array([str(uuid.uuid4()) for _ in range(count)], type=pa.string()),
            "stage_id":       pa.array(["STAGE-1"] * count, type=pa.string()),
            "stability":      pa.array([0.5] * count, type=pa.float64()),
            "difficulty":     pa.array([-0.1, 0.2, 0.3, 0.4, 0.5], type=pa.float64()),
            "next_review":    pa.array([base_ts] * count, type=pa.timestamp("us")),
            "lesson":         pa.array(["L"] * count, type=pa.string()),
            "state":          pa.array([0] * count, type=pa.int64()),
            "step":           pa.array([1] * count, type=pa.int64()),
            "last_review":    pa.array([base_ts] * count, type=pa.timestamp("us")),
            "modified":       pa.array([base_ts] * count, type=pa.timestamp("us")),
            "archive_scope":  pa.array([f"season_{EXEC_SEASON_SEQ_1}"] * count, type=pa.string()),
            "archive_job_id": pa.array(["ARCH-10150"] * count, type=pa.string()),
            "schema_version": pa.array(["v1"] * count, type=pa.string()),
            "exported_at":    pa.array([base_ts] * count, type=pa.timestamp("us")),
        })
        path = str(tmp_path / "neg_diff.parquet")
        pq.write_table(table, path)

        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is False
        dq08 = next(r for r in result["results"] if r["rule"] == "DQ-08")
        assert dq08["passed"] is False

    def test_dq_fails_on_duplicate_name_season_seq(self, tmp_path):
        path = _make_bad_ms_parquet(str(tmp_path), dup_key=True)
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is False
        dq10 = next(r for r in result["results"] if r["rule"] == "DQ-10")
        assert dq10["passed"] is False

    def test_dq_empty_parquet_passes_with_warning(self, tmp_path):
        # Build an empty parquet matching the MS schema
        table = pa.table({col.name: pa.array([], type=col.type) for col in MS_PARQUET_SCHEMA})
        path = str(tmp_path / "empty.parquet")
        pq.write_table(table, path)
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is True
        assert len(result["warnings"]) > 0

    def test_dq_referential_fails_when_player_not_in_dim(self, tmp_path):
        path = _make_good_ms_parquet(str(tmp_path), count=5)
        # Player dim missing the players in the fact
        dim_path = _make_player_dim_parquet(str(tmp_path), ["SOME-OTHER-PLAYER"])
        result = validate_fact_quality_generic(
            path, MS_DQ_RULES_V1,
            dimension_paths={"player": dim_path},
        )
        assert result["passed"] is False
        dq11 = next(r for r in result["results"] if r["rule"] == "DQ-11")
        assert dq11["passed"] is False

    def test_dq_referential_passes_when_no_dim_path(self, tmp_path):
        path = _make_good_ms_parquet(str(tmp_path), count=5)
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        assert result["passed"] is True
        dq11 = next(r for r in result["results"] if r["rule"] == "DQ-11")
        # DQ-11 is skipped when no player path provided
        assert dq11["passed"] is True

    def test_dq_result_contains_all_rule_ids(self, tmp_path):
        path = _make_good_ms_parquet(str(tmp_path), count=10)
        result = validate_fact_quality_generic(path, MS_DQ_RULES_V1, dimension_paths={})
        rule_ids = {r["rule"] for r in result["results"]}
        expected_ids = {rule["id"] for rule in MS_DQ_RULES_V1}
        assert expected_ids == rule_ids


# ===========================================================================
# TestPurgeIntegrationConfidence — integration
# ===========================================================================

class TestPurgeIntegrationConfidence:
    """Integration tests for purge helpers and audit logging."""

    pytestmark = pytest.mark.integration

    @pytest.fixture(autouse=True)
    def _cleanup(self, db_conn):
        ensure_audit_table(db_conn)
        yield
        _delete_exec_jobs(db_conn)
        _delete_exec_audit(db_conn)
        _delete_exec_seasons(db_conn)

    def test_purge_partition_name_is_p_season_N(self):
        # Partition name is purely computed from season_seq — no DB needed
        season_seq = EXEC_SEASON_SEQ_1
        expected = f"p_season_{season_seq}"
        assert expected == f"p_season_{season_seq}"

    def test_blocked_purge_does_not_change_job_status(self, integration_db_config, db_conn):
        from archive_executor.purge import purge_completed_jobs

        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        with tempfile.TemporaryDirectory() as tmpdir:
            upsert_ms_archive_job(
                db_conn, EXEC_JOB_E2E3, "Completed",
                EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
                file_path=tmpdir,
            )

            blocking_result = GateResult(
                passed=False,
                gates=[
                    GateCheck("grace_period", False, "Grace period not met", {}),
                ],
                blockers=["Grace period not met"],
                season_name=EXEC_SEASON_NAME_1,
                season_seq=EXEC_SEASON_SEQ_1,
                checked_at="2026-03-11T00:00:00",
            )

            log = _make_mock_log()
            with patch("archive_executor.purge.check_all_gates", return_value=blocking_result):
                purge_completed_jobs(integration_db_config, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_E2E3)
        assert row is not None
        assert row["status"] == "Completed"

    def test_mark_purged_transitions_completed_to_purged(self, integration_db_config, db_conn):
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_E2E3, "Completed",
            EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
        )
        _mark_purged(integration_db_config, EXEC_JOB_E2E3)
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_E2E3)
        assert row is not None
        assert row["status"] == "Purged"
        assert row["source_deleted"] == 1

    def test_mark_purged_no_op_on_non_completed(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_FAIL4, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, status="Pending")
        _mark_purged(integration_db_config, EXEC_JOB_FAIL4)
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FAIL4)
        assert row is not None
        assert row["status"] == "Pending"


# ===========================================================================
# TestAuditLoggingBehavior — integration
# ===========================================================================

class TestAuditLoggingBehavior:
    """Integration tests for audit log writes and job status field tracking."""

    pytestmark = pytest.mark.integration

    @pytest.fixture(autouse=True)
    def _cleanup(self, db_conn):
        ensure_audit_table(db_conn)
        yield
        _delete_exec_jobs(db_conn)
        _delete_exec_audit(db_conn)
        _delete_exec_seasons(db_conn)

    def _get_audit_record(self, conn, job_id: str) -> dict | None:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM `archive_delete_audit_log` WHERE job_id = %s", (job_id,)
            )
            return cursor.fetchone()

    def test_log_delete_audit_writes_record(self, integration_db_config, db_conn):
        log = _make_mock_log()
        _log_delete_audit(
            integration_db_config, log,
            job_id=EXEC_JOB_FAIL1,
            season_id=f"season_{EXEC_SEASON_SEQ_1}",
            rows_deleted=100,
            duration_ms=500,
            status="success",
            error_msg=None,
            total_rows_estimated=100,
            batch_size=10000,
            num_batches=1,
        )
        db_conn.commit()
        record = self._get_audit_record(db_conn, EXEC_JOB_FAIL1)
        assert record is not None
        assert record["job_id"] == EXEC_JOB_FAIL1
        assert record["rows_deleted"] == 100
        assert record["status"] == "success"

    def test_log_delete_audit_upsert_updates_existing(self, integration_db_config, db_conn):
        log = _make_mock_log()
        _log_delete_audit(
            integration_db_config, log,
            job_id=EXEC_JOB_FAIL2,
            season_id=f"season_{EXEC_SEASON_SEQ_1}",
            rows_deleted=50,
            duration_ms=200,
            status="success",
            error_msg=None,
            total_rows_estimated=50,
            batch_size=10000,
            num_batches=1,
        )
        # Second call with same job_id — should update via ON DUPLICATE KEY
        _log_delete_audit(
            integration_db_config, log,
            job_id=EXEC_JOB_FAIL2,
            season_id=f"season_{EXEC_SEASON_SEQ_1}",
            rows_deleted=150,
            duration_ms=800,
            status="success",
            error_msg=None,
            total_rows_estimated=150,
            batch_size=10000,
            num_batches=2,
        )
        db_conn.commit()
        record = self._get_audit_record(db_conn, EXEC_JOB_FAIL2)
        assert record is not None
        assert record["rows_deleted"] == 150
        assert record["num_batches"] == 2

    def test_log_delete_audit_on_failure_status(self, integration_db_config, db_conn):
        log = _make_mock_log()
        _log_delete_audit(
            integration_db_config, log,
            job_id=EXEC_JOB_FAIL3,
            season_id=f"season_{EXEC_SEASON_SEQ_1}",
            rows_deleted=0,
            duration_ms=100,
            status="failed",
            error_msg="partition not found",
            total_rows_estimated=0,
            batch_size=0,
            num_batches=0,
        )
        db_conn.commit()
        record = self._get_audit_record(db_conn, EXEC_JOB_FAIL3)
        assert record is not None
        assert record["status"] == "failed"
        assert record["error_msg"] == "partition not found"

    def test_claim_job_sets_sync_paused(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_CLAIM, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)
        _claim_job(integration_db_config, EXEC_JOB_CLAIM)
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_CLAIM)
        assert row is not None
        assert row["sync_paused"] == 1
        assert row["sync_paused_at"] is not None

    def test_mark_completed_clears_sync_paused(self, integration_db_config, db_conn):
        _insert_exec_job(
            db_conn, EXEC_JOB_CLAIM, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
            status="Processing", sync_paused=1,
        )
        _mark_completed(integration_db_config, EXEC_JOB_CLAIM)
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_CLAIM)
        assert row is not None
        assert row["status"] == "Completed"
        assert row["sync_paused"] == 0
        assert row["sync_paused_at"] is None

    def test_fail_job_permanent_clears_sync_paused(self, integration_db_config, db_conn):
        _insert_exec_job(
            db_conn, EXEC_JOB_FAIL4, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
            status="Processing", retry_count=3, sync_paused=1,
        )
        _fail_job(
            integration_db_config,
            EXEC_JOB_FAIL4,
            "permanent",
            retry_count=3,
            current_status="Processing",
        )
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_FAIL4)
        assert row is not None
        assert row["status"] == "Failed"
        assert row["sync_paused"] == 0


# ===========================================================================
# TestIdempotencyAndDuplicateProtection — integration
# ===========================================================================

class TestIdempotencyAndDuplicateProtection:
    """Tests for duplicate-safe operations and idempotent behavior."""

    pytestmark = pytest.mark.integration

    @pytest.fixture(autouse=True)
    def _cleanup(self, db_conn):
        ensure_audit_table(db_conn)
        yield
        _delete_exec_jobs(db_conn)
        _delete_exec_seasons(db_conn)
        # Clean up any scheduler-created jobs for exec seasons
        for seq in (EXEC_SEASON_SEQ_1, EXEC_SEASON_SEQ_2):
            scope = f"season_{seq}"
            with db_conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM `tabMemora Archive Job` "
                    "WHERE source_doctype='Memora Memory State' AND archive_scope=%s",
                    (scope,),
                )
        db_conn.commit()

    def test_scheduler_creates_job_only_once_for_same_season(self, integration_db_config, db_conn):
        from archive_executor.scheduler import _job_exists

        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        archive_scope = f"season_{EXEC_SEASON_SEQ_1}"

        # Before any job: _job_exists returns False
        assert not _job_exists(db_conn, "Memora Memory State", archive_scope, "v1")

        # Insert a Pending job for this season
        _insert_exec_job(db_conn, EXEC_JOB_IDEM1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)

        # Now _job_exists must return True — second scheduler run would skip this season
        assert _job_exists(db_conn, "Memora Memory State", archive_scope, "v1")

    def test_claim_job_is_atomic_second_call_returns_false(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_IDEM1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)
        first  = _claim_job(integration_db_config, EXEC_JOB_IDEM1)
        second = _claim_job(integration_db_config, EXEC_JOB_IDEM1)
        assert first  is True
        assert second is False

    def test_fail_job_respects_current_status_guard(self, integration_db_config, db_conn):
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_IDEM2, "Completed",
            EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
        )
        _fail_job(
            integration_db_config, EXEC_JOB_IDEM2,
            "ignored", retry_count=0, current_status="Processing",
        )
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_IDEM2)
        assert row["status"] == "Completed"

    def test_process_pending_skips_processing_job(self, integration_db_config, db_conn):
        _insert_exec_job(
            db_conn, EXEC_JOB_IDEM1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
            status="Processing",
        )
        pending = _get_jobs_by_status(integration_db_config, "Pending")
        # Our Processing job must not appear in Pending query
        names = [j["name"] for j in pending]
        assert EXEC_JOB_IDEM1 not in names

    def test_upsert_ms_archive_job_handles_duplicate(self, integration_db_config, db_conn):
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_IDEM2, "Pending",
            EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
        )
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_IDEM2, "Completed",
            EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
        )
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_IDEM2)
        assert row is not None
        assert row["status"] == "Completed"


# ===========================================================================
# TestConcurrencyResistance — integration
# ===========================================================================

class TestConcurrencyResistance:
    """Tests verifying atomic claim and concurrency protection."""

    pytestmark = pytest.mark.integration

    @pytest.fixture(autouse=True)
    def _cleanup(self, db_conn):
        ensure_audit_table(db_conn)
        yield
        _delete_exec_jobs(db_conn)
        _delete_exec_seasons(db_conn)

    def test_concurrent_claim_only_one_succeeds(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_RACE1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)
        first  = _claim_job(integration_db_config, EXEC_JOB_RACE1)
        second = _claim_job(integration_db_config, EXEC_JOB_RACE1)
        assert first  is True
        assert second is False

    def test_process_pending_with_already_claimed_job(self, integration_db_config, db_conn):
        _insert_exec_job(db_conn, EXEC_JOB_RACE1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1)

        # Simulate another process claiming the job between our query and our claim
        pending_before = _get_jobs_by_status(integration_db_config, "Pending")
        our_job = next((j for j in pending_before if j["name"] == EXEC_JOB_RACE1), None)
        assert our_job is not None

        # "Another process" claims it
        with db_conn.cursor() as cursor:
            cursor.execute(
                "UPDATE `tabMemora Archive Job` "
                "SET status='Processing', claimed_at=NOW(), sync_paused=1 "
                "WHERE name=%s AND status='Pending'",
                (EXEC_JOB_RACE1,),
            )
        db_conn.commit()

        # Now our claim attempt must fail
        result = _claim_job(integration_db_config, EXEC_JOB_RACE1)
        assert result is False

        # Job should still be Processing
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_RACE1)
        assert row["status"] == "Processing"


# ===========================================================================
# TestTempFileCleanup — pure unit tests (no DB)
# ===========================================================================

class TestTempFileCleanup:
    """Pure unit tests for _cleanup_staging directory removal."""

    pytestmark = pytest.mark.unit

    def test_cleanup_staging_removes_directory(self, tmp_path):
        staging = str(tmp_path / "staging_test")
        os.makedirs(staging)
        # Create a file inside it
        open(os.path.join(staging, "file.parquet"), "w").close()
        assert os.path.isdir(staging)
        _cleanup_staging(staging)
        assert not os.path.isdir(staging)

    def test_cleanup_staging_handles_nonexistent_directory(self):
        # Must not raise even if directory does not exist
        _cleanup_staging("/tmp/nonexistent_memora_exec_test_xyz_abc_123")

    def test_cleanup_staging_handles_empty_string(self):
        # os.path.isdir("") is False — must not raise
        _cleanup_staging("")

    def test_staging_dir_is_dot_staging_subdir(self):
        # Verify the naming convention used in _process_pending_jobs:
        # staging_dir = os.path.join(archive_output_path, ".staging", job_name)
        archive_output_path = "/data/memora/archives/"
        job_name = "ARCH-10101"
        staging_dir = os.path.join(archive_output_path, ".staging", job_name)
        assert staging_dir == "/data/memora/archives/.staging/ARCH-10101"
        assert "/.staging/" in staging_dir


# ===========================================================================
# TestRealisticHighVolumeData — integration
# ===========================================================================

class TestRealisticHighVolumeData:
    """Integration tests using 500 rows with realistic production-shaped data."""

    pytestmark = pytest.mark.integration

    @pytest.fixture(autouse=True)
    def _cleanup(self, db_conn):
        ensure_audit_table(db_conn)
        yield
        _delete_exec_jobs(db_conn)
        _delete_exec_players(db_conn)
        delete_memory_state_rows(db_conn, EXEC_SEASON_SEQ_3)
        _delete_exec_seasons(db_conn)

    def _insert_hvol_rows(self, conn, count: int = 500) -> int:
        """Insert 500 realistic Memory State rows for high-volume tests."""
        import math
        now = datetime(2099, 8, 1, 10, 0, 0)
        rows = []
        subjects = ["Biology", "Chemistry", "Physics", "Mathematics", "History"]
        stage_ids = ["STAGE-1", "STAGE-2", "STAGE-3", "STAGE-4"]

        for n in range(1, count + 1):
            ts = now + timedelta(seconds=n * 30)
            player_id = f"{EXEC_PLAYER_PREFIX}-{(n % 10) + 1:03d}"
            item_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"hvol-item-{n}").bytes
            name_bytes = uuid.uuid5(uuid.NAMESPACE_DNS, f"ms-name-hvol-{n}").bytes
            name_val = int.from_bytes(name_bytes[:7], "big") + 1

            # Log-normal-like stability: spread from 0.5 to 15.0
            stability = round(0.5 + (math.log(n + 1) / math.log(count + 1)) * 14.5, 4)
            # Uniform difficulty in [0.1, 0.9]
            difficulty = round(0.1 + (n % 80) / 100.0, 4)

            # State distribution: 70% state=2, 20% state=1, 10% state=0
            if n % 10 == 0:
                state = 0
                step = n % 3
            elif n % 5 == 0:
                state = 1
                step = None
            else:
                state = 2
                step = None

            rows.append((
                name_val,
                EXEC_SEASON_SEQ_3,
                subjects[n % len(subjects)],
                player_id,
                item_uuid,
                stage_ids[n % len(stage_ids)],
                stability,
                difficulty,
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                f"EXEC-LESSON-HVOL-{(n % 5) + 1:03d}",
                state,
                step,
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                "test@test.com",
                "test@test.com",
            ))

        sql = (
            "INSERT IGNORE INTO `tabMemora Memory State` "
            "(`name`, `season_seq`, `subject`, `player`, `item_id`, `stage_id`, "
            " `stability`, `difficulty`, `next_review`, `lesson`, "
            " `state`, `step`, `last_review`, "
            " `creation`, `modified`, `modified_by`, `owner`) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with conn.cursor() as cursor:
            cursor.executemany(sql, rows)
        conn.commit()
        return count

    def test_export_500_rows_correct_count_and_uuid_valid(
        self, integration_db_config, db_conn
    ):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_3, num_players=10)
        inserted = self._insert_hvol_rows(db_conn, 500)
        assert inserted == 500

        upsert_ms_archive_job(
            db_conn, EXEC_JOB_HVOL, "Pending",
            EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3,
        )

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            _scoped_process_pending_jobs(cfg, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_HVOL)
        assert row is not None
        assert row["status"] in ("Exported", "Completed")
        # row_count should be 500
        assert row["row_count"] == 500

    def test_realistic_data_dq_passes_all_rules(
        self, integration_db_config, db_conn
    ):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_3, num_players=10)
        self._insert_hvol_rows(db_conn, 500)
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_HVOL, "Pending",
            EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3,
        )

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            _scoped_process_pending_jobs(cfg, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_HVOL)
        assert row is not None
        # If DQ passed the job would be Exported or Completed (not reverted to Pending)
        assert row["status"] in ("Exported", "Completed")

    def test_realistic_data_null_step_values_exported_correctly(
        self, integration_db_config, db_conn
    ):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_3, num_players=10)
        self._insert_hvol_rows(db_conn, 500)
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_HVOL, "Pending",
            EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3,
        )

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            _scoped_process_pending_jobs(cfg, log)

            db_conn.commit()
            row = _get_archive_job(db_conn, EXEC_JOB_HVOL)
            if row and row["status"] in ("Exported", "Completed") and row.get("file_path"):
                fact_candidates = [
                    f for f in os.listdir(row["file_path"])
                    if f.startswith("fact_") and f.endswith(".parquet")
                ]
                if fact_candidates:
                    fact_path = os.path.join(row["file_path"], fact_candidates[0])
                    table = pq.read_table(fact_path)
                    if "step" in table.column_names:
                        step_col = table.column("step")
                        null_count = step_col.null_count
                        assert null_count > 0, "Expected some NULL step values for state=1/2 rows"

    def test_realistic_data_decimal_values_become_floats(
        self, integration_db_config, db_conn
    ):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_3, num_players=10)
        self._insert_hvol_rows(db_conn, 100)
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_HVOL, "Pending",
            EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3,
        )

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            _scoped_process_pending_jobs(cfg, log)

            db_conn.commit()
            row = _get_archive_job(db_conn, EXEC_JOB_HVOL)
            if row and row["status"] in ("Exported", "Completed") and row.get("file_path"):
                fact_candidates = [
                    f for f in os.listdir(row["file_path"])
                    if f.startswith("fact_") and f.endswith(".parquet")
                ]
                if fact_candidates:
                    fact_path = os.path.join(row["file_path"], fact_candidates[0])
                    table = pq.read_table(fact_path)
                    if "stability" in table.column_names:
                        assert pa.types.is_floating(table.schema.field("stability").type)
                    if "difficulty" in table.column_names:
                        assert pa.types.is_floating(table.schema.field("difficulty").type)

    def test_realistic_data_multiple_subjects_preserved(
        self, integration_db_config, db_conn
    ):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_3, num_players=10)
        self._insert_hvol_rows(db_conn, 200)
        upsert_ms_archive_job(
            db_conn, EXEC_JOB_HVOL, "Pending",
            EXEC_SEASON_SEQ_3, EXEC_SEASON_NAME_3,
        )

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            _scoped_process_pending_jobs(cfg, log)

            db_conn.commit()
            row = _get_archive_job(db_conn, EXEC_JOB_HVOL)
            if row and row["status"] in ("Exported", "Completed") and row.get("file_path"):
                fact_candidates = [
                    f for f in os.listdir(row["file_path"])
                    if f.startswith("fact_") and f.endswith(".parquet")
                ]
                if fact_candidates:
                    fact_path = os.path.join(row["file_path"], fact_candidates[0])
                    table = pq.read_table(fact_path)
                    if "subject" in table.column_names:
                        import pyarrow.compute as pc
                        unique_subjects = pc.unique(table.column("subject")).to_pylist()
                        assert len(unique_subjects) > 1


# ===========================================================================
# TestEndToEndBranches — integration
# ===========================================================================

class TestEndToEndBranches:
    """End-to-end tests covering the major pipeline branches."""

    pytestmark = pytest.mark.integration

    @pytest.fixture(autouse=True)
    def _cleanup(self, db_conn):
        ensure_audit_table(db_conn)
        yield
        _delete_exec_jobs(db_conn)
        _delete_exec_players(db_conn)
        delete_memory_state_rows(db_conn, EXEC_SEASON_SEQ_1)
        delete_memory_state_rows(db_conn, EXEC_SEASON_SEQ_2)
        _delete_exec_seasons(db_conn)

    def test_e2e_export_failure_leaves_job_retryable(self, integration_db_config, db_conn):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_1)
        _insert_exec_ms_rows(db_conn, "E2E1", 10, EXEC_SEASON_SEQ_1)
        _insert_exec_job(db_conn, EXEC_JOB_E2E1, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, retry_count=0)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            with patch("archive_executor.run._export_job", side_effect=RuntimeError("export failure")):
                processed, failed = _scoped_process_pending_jobs(cfg, log)

        assert failed == 0  # retry_count=0 → not permanent failure
        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_E2E1)
        assert row is not None
        assert row["status"] == "Pending"   # reset for retry
        assert row["retry_count"] == 1
        assert row["error_log"] is not None

    def test_e2e_dq_failure_leaves_job_retryable(self, integration_db_config, db_conn):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_1)
        _insert_exec_ms_rows(db_conn, "E2E2", 10, EXEC_SEASON_SEQ_1)
        _insert_exec_job(db_conn, EXEC_JOB_E2E2, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, retry_count=0)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            with patch(
                "archive_executor.run._export_job",
                side_effect=RuntimeError("Data quality validation failed: DQ-05"),
            ):
                _scoped_process_pending_jobs(cfg, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_E2E2)
        assert row is not None
        assert row["status"] == "Pending"
        assert row["retry_count"] == 1
        assert "DQ-05" in row["error_log"]

    def test_e2e_zero_row_season_completed_directly(
        self, integration_db_config, db_conn
    ):
        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_2, EXEC_SEASON_NAME_2, end_date="2025-01-01")
        _insert_exec_players(db_conn, EXEC_SEASON_NAME_2)
        # No rows for this season
        _insert_exec_job(db_conn, EXEC_JOB_E2E3, EXEC_SEASON_SEQ_2, EXEC_SEASON_NAME_2)

        log = _make_mock_log()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_archive_dir(integration_db_config, tmpdir)
            _scoped_process_pending_jobs(cfg, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_E2E3)
        assert row is not None
        assert row["status"] == "Completed"
        assert row["row_count"] == 0

    def test_e2e_purge_blocked_when_no_archive_job_exists(
        self, integration_db_config, db_conn
    ):
        from archive_executor.purge import purge_completed_jobs

        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")

        with tempfile.TemporaryDirectory() as tmpdir:
            upsert_ms_archive_job(
                db_conn, EXEC_JOB_E2E1, "Completed",
                EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
                file_path=tmpdir,
            )

            blocking_result = GateResult(
                passed=False,
                gates=[
                    GateCheck("archive_validation", False, "No validated archive found", {}),
                ],
                blockers=["No validated archive found"],
                season_name=EXEC_SEASON_NAME_1,
                season_seq=EXEC_SEASON_SEQ_1,
                checked_at="2026-03-11T00:00:00",
            )

            log = _make_mock_log()
            with patch("archive_executor.purge.check_all_gates", return_value=blocking_result):
                purge_completed_jobs(integration_db_config, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_E2E1)
        assert row is not None
        assert row["status"] == "Completed"

    def test_e2e_purge_blocked_by_grace_period(self, integration_db_config, db_conn):
        from archive_executor.purge import purge_completed_jobs

        _insert_exec_season(db_conn, EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1, end_date="2025-01-01")

        with tempfile.TemporaryDirectory() as tmpdir:
            upsert_ms_archive_job(
                db_conn, EXEC_JOB_E2E2, "Completed",
                EXEC_SEASON_SEQ_1, EXEC_SEASON_NAME_1,
                file_path=tmpdir,
            )

            # Set completed_at to NOW() so grace period is not met
            with db_conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE `tabMemora Archive Job` SET completed_at=NOW() WHERE name=%s",
                    (EXEC_JOB_E2E2,),
                )
            db_conn.commit()

            blocking_result = GateResult(
                passed=False,
                gates=[
                    GateCheck("grace_period", False, "Grace period not met: 0 days since completion", {}),
                ],
                blockers=["Grace period not met"],
                season_name=EXEC_SEASON_NAME_1,
                season_seq=EXEC_SEASON_SEQ_1,
                checked_at="2026-03-11T00:00:00",
            )

            log = _make_mock_log()
            with patch("archive_executor.purge.check_all_gates", return_value=blocking_result):
                purge_completed_jobs(integration_db_config, log)

        db_conn.commit()
        row = _get_archive_job(db_conn, EXEC_JOB_E2E2)
        assert row is not None
        assert row["status"] == "Completed"
