"""Pytest configuration and shared fixtures for archive executor tests.

Integration tests require a live MariaDB connection.
Set environment variables to enable them:

    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=frappe DB_PASSWORD=... DB_NAME=...

Or use TEST_DB_* prefixed overrides to target a dedicated test database:

    TEST_DB_HOST=...  TEST_DB_USER=...  TEST_DB_PASSWORD=...  TEST_DB_NAME=...

Run integration tests only:
    pytest -m integration archive_executor/tests/test_integration_pipeline.py -v

Run unit tests only (no DB required):
    pytest -m "not integration" archive_executor/tests/ -v
"""

import json
import os
import tempfile
from pathlib import Path

import pymysql
import pymysql.cursors
import pytest

from archive_executor.config import Config


# ---------------------------------------------------------------------------
# Pytest markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring a live MariaDB database connection",
    )
    config.addinivalue_line(
        "markers",
        "unit: mark test as a pure unit test (no external dependencies required)",
    )


# ---------------------------------------------------------------------------
# DB config fixture
# ---------------------------------------------------------------------------

def _get_env(key: str, fallback_key: str | None = None, default: str = "") -> str:
    return os.environ.get(key) or (os.environ.get(fallback_key) if fallback_key else None) or default


@pytest.fixture(scope="session")
def integration_db_config() -> Config:
    """Config pointing at the real (or test) MariaDB database.

    Skips entire test session if no DB credentials are found.
    """
    db_host = _get_env("TEST_DB_HOST", "DB_HOST")
    if not db_host:
        pytest.skip("Integration tests require DB_HOST or TEST_DB_HOST to be set")

    db_name = _get_env("TEST_DB_NAME", "DB_NAME")
    if not db_name:
        pytest.skip("Integration tests require DB_NAME or TEST_DB_NAME to be set")

    schema_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "archive_schemas"
    )
    schema_path = str(Path(schema_path).resolve())

    return Config(
        db_host=db_host,
        db_port=int(_get_env("TEST_DB_PORT", "DB_PORT", "3306")),
        db_user=_get_env("TEST_DB_USER", "DB_USER", "frappe"),
        db_password=_get_env("TEST_DB_PASSWORD", "DB_PASSWORD", ""),
        db_name=db_name,
        archive_output_path="/tmp/memora_inttest_archive/",
        schema_registry_path=schema_path,
        log_path="/tmp/memora_inttest_logs/",
        lock_file="/tmp/memora_inttest.lock",
        chunk_size=1000,
        stuck_timeout_hours=1,
        ssh_host="", ssh_user="", ssh_key_path="",
        ssh_port=22, ssh_timeout=300,
        remote_archive_path="", remote_live_path="",
        analytics_cmd_path="", duckdb_path="",
        live_output_path="/tmp/memora_inttest_live/",
        live_lock_file="/tmp/memora_inttest_live.lock",
        sync_state_path="/tmp/memora_inttest_sync_state/",
        sync_output_path="/tmp/memora_inttest_sync_output/",
        sync_overlap_seconds=300,
        sync_remote_path="",
        purge_grace_days=7,
        snapshot_output_path="/tmp/memora_inttest_snapshots/",
        remote_snapshot_path="",
    )


@pytest.fixture(scope="session")
def db_conn(integration_db_config: Config):
    """Session-scoped raw pymysql connection for test setup/teardown."""
    conn = pymysql.connect(
        host=integration_db_config.db_host,
        port=integration_db_config.db_port,
        user=integration_db_config.db_user,
        password=integration_db_config.db_password,
        database=integration_db_config.db_name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

# Use far-future dates so test rows never collide with real production data
TEST_DATE_FROM = "2099-01-01"
TEST_DATE_TO   = "2100-01-01"

# Sub-ranges per dataset
RANGE_A = ("2099-01-01", "2099-02-01")   # Dataset A  — 10 rows
RANGE_B = ("2099-02-01", "2099-03-01")   # Dataset B  — 100 rows
RANGE_C = ("2099-03-01", "2099-04-01")   # Dataset C  — 10,000 rows
RANGE_D = ("2099-05-01", "2099-06-01")   # Dataset D  — 100,000 rows (large test)
RANGE_X = ("2099-07-01", "2099-08-01")   # Extra dataset for cross-season isolation

# Archive job names reserved for integration tests
TEST_JOB_EXPORT   = "ARCH-99001"
TEST_JOB_PURGE    = "ARCH-99002"
TEST_JOB_RERUN    = "ARCH-99003"
TEST_JOB_TXN      = "ARCH-99004"
TEST_JOB_MULTI_A  = "ARCH-99005"
TEST_JOB_MULTI_B  = "ARCH-99006"
TEST_JOB_LARGE    = "ARCH-99010"

ALL_TEST_JOBS = [
    TEST_JOB_EXPORT, TEST_JOB_PURGE, TEST_JOB_RERUN,
    TEST_JOB_TXN, TEST_JOB_MULTI_A, TEST_JOB_MULTI_B, TEST_JOB_LARGE,
]

FRAPPE_BASE_COLS = (
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx"
)

PRACTICE_LOG_TABLE  = "tabMemora Practice Log"
PLAYER_TABLE        = "tabMemora Player Profile"
AUDIT_TABLE         = "archive_delete_audit_log"
ARCHIVE_JOB_TABLE   = "tabMemora Archive Job"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job_meta(date_from: str, date_to: str) -> str:
    """Build minimal job_meta JSON for an integration test archive job."""
    meta = {
        "query_filter": {
            "date_from": date_from,
            "date_to": date_to,
            "filter_column": "last_seen_at",
        },
        "export_columns": [
            "player_id", "item_id", "first_seen_at", "last_seen_at",
            "last_result", "attempt_count", "correct_count",
        ],
        "schema_snapshot": {
            "columns": [
                {"name": "player_id",     "type": "VARCHAR(140)"},
                {"name": "item_id",       "type": "VARCHAR(36)"},
                {"name": "first_seen_at", "type": "DATETIME"},
                {"name": "last_seen_at",  "type": "DATETIME"},
                {"name": "last_result",   "type": "VARCHAR(20)"},
                {"name": "attempt_count", "type": "INT"},
                {"name": "correct_count", "type": "INT"},
            ],
            "primary_key": ["player_id", "item_id"],
        },
        "related_tables": [],
    }
    return json.dumps(meta)


def insert_practice_log_rows(
    conn,
    prefix: str,
    count: int,
    date_from: str,
    date_to: str,
    num_players: int = 10,
    batch_size: int = 500,
) -> int:
    """Insert synthetic practice log rows into tabMemora Practice Log.

    Real schema: (player_id, item_id) composite PK — no Frappe standard columns.
    last_seen_at is spread across [date_from, date_to).
    Returns the number of rows actually inserted.
    """
    from datetime import datetime, timedelta

    dt_from = datetime.fromisoformat(date_from)
    dt_to   = datetime.fromisoformat(date_to)
    span_seconds = int((dt_to - dt_from).total_seconds())

    inserted = 0
    rows = []
    for n in range(1, count + 1):
        step = span_seconds // max(count, 1)
        first_seen = dt_from + timedelta(seconds=(n - 1) * step)
        last_seen  = first_seen + timedelta(seconds=1)

        attempt_count = (n % 9) + 1
        correct_count = n % attempt_count if attempt_count > 0 else 0

        rows.append((
            f"TEST-PLAYER-{(n % num_players) + 1:03d}",  # player_id
            f"TEST-ITEM-{prefix}-{n:08d}",               # item_id (unique per prefix+n)
            first_seen.strftime("%Y-%m-%d %H:%M:%S"),    # first_seen_at
            last_seen.strftime("%Y-%m-%d %H:%M:%S"),     # last_seen_at
            "Correct" if n % 2 == 0 else "Incorrect",   # last_result
            attempt_count,                               # attempt_count
            correct_count,                               # correct_count
        ))

        if len(rows) >= batch_size:
            inserted += _flush_rows(conn, rows)
            rows = []

    if rows:
        inserted += _flush_rows(conn, rows)

    return inserted


def _flush_rows(conn, rows: list) -> int:
    """Batch-insert rows into tabMemora Practice Log.

    Real schema has only 7 columns — no Frappe standard columns.
    """
    sql = (
        "INSERT IGNORE INTO `tabMemora Practice Log` "
        "(`player_id`, `item_id`, `first_seen_at`, `last_seen_at`, "
        " `last_result`, `attempt_count`, `correct_count`) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()
    return len(rows)


def insert_test_players(conn, num_players: int = 20):
    """Ensure test player profiles exist in tabMemora Player Profile."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Player Profile` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `grade`, `major`, `season`, `plan`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', "
        "        0, %s, %s, %s, 'SEAS-TEST-001', %s)"
    )
    rows = [
        (
            f"TEST-PLAYER-{i + 1:03d}",
            i + 1,
            f"Grade-{(i % 4) + 1}",
            "Science" if i % 2 == 0 else "Arts",
            f"PLAN-TEST-{(i % 3) + 1:03d}",
        )
        for i in range(num_players)
    ]
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()


def delete_test_practice_logs_by_prefix(conn, item_prefix: str) -> None:
    """Delete all test practice log rows whose item_id starts with the given prefix.

    Used to clean stale rows whose last_seen_at may have overflowed outside the
    expected date range (e.g. from a previous crashed test run using old code).
    """
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Practice Log` WHERE `item_id` LIKE %s",
            (f"TEST-ITEM-{item_prefix}-%",),
        )
    conn.commit()


def delete_test_practice_logs(conn, date_from: str = "2099-01-01", date_to: str = "2100-01-01"):
    """Remove all test practice log rows in the given date range."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Practice Log` "
            "WHERE `last_seen_at` >= %s AND `last_seen_at` < %s",
            (date_from, date_to),
        )
    conn.commit()


def delete_test_jobs(conn, job_names: list | None = None):
    """Remove test archive jobs."""
    names = job_names or ALL_TEST_JOBS
    if not names:
        return
    placeholders = ", ".join(["%s"] * len(names))
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `tabMemora Archive Job` WHERE `name` IN ({placeholders})",
            names,
        )
    conn.commit()


def delete_test_audit_logs(conn, job_names: list | None = None):
    """Remove test entries from archive_delete_audit_log."""
    names = job_names or ALL_TEST_JOBS
    if not names:
        return
    placeholders = ", ".join(["%s"] * len(names))
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `archive_delete_audit_log` WHERE `job_id` IN ({placeholders})",
            names,
        )
    conn.commit()


def delete_test_players(conn):
    """Remove test player profiles."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Player Profile` WHERE `name` LIKE 'TEST-PLAYER-%'"
        )
    conn.commit()


def count_practice_logs(conn, date_from: str, date_to: str) -> int:
    """Count practice log rows within the given last_seen_at range."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM `tabMemora Practice Log` "
            "WHERE `last_seen_at` >= %s AND `last_seen_at` < %s",
            (date_from, date_to),
        )
        return cursor.fetchone()["cnt"]


def upsert_archive_job(
    conn,
    name: str,
    status: str,
    date_from: str,
    date_to: str,
    file_path: str = "",
    post_archive_action: str = "Delete",
    source_deleted: int = 0,
    archive_scope: str = "SEAS-TEST-001",
) -> None:
    """Insert or replace a test archive job.

    Each job must have a unique (source_doctype, archive_scope, schema_version)
    combination due to the idx_archive_job_unique constraint. Pass distinct
    archive_scope values when creating multiple simultaneous test jobs.

    Includes all NOT NULL columns required by the real tabMemora Archive Job schema:
    duration_seconds, row_count, file_size_bytes, retry_count, source_deleted, sync_paused.
    """
    job_meta = _make_job_meta(date_from, date_to)
    sql = (
        "INSERT INTO `tabMemora Archive Job` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
        " `status`, `priority`, `retry_count`, `post_archive_action`, "
        " `source_deleted`, `sync_paused`, "
        " `duration_seconds`, `row_count`, `file_size_bytes`, "
        " `file_path`, `job_meta`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0, "
        "        'Memora Practice Log', %s, 'v1', 'practice_log', "
        "        %s, 'Normal', 0, %s, %s, 0, "
        "        0, 0, 0, "
        "        %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  status=%s, file_path=%s, post_archive_action=%s, "
        "  source_deleted=%s, job_meta=%s, modified=NOW()"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            name, archive_scope, status, post_archive_action, source_deleted,
            file_path, job_meta,
            status, file_path, post_archive_action, source_deleted, job_meta,
        ))
    conn.commit()


def ensure_audit_table(conn):
    """Create archive_delete_audit_log table if it doesn't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS `archive_delete_audit_log` (
        `id`                   INT UNSIGNED NOT NULL AUTO_INCREMENT,
        `job_id`               VARCHAR(140) NOT NULL,
        `season_id`            VARCHAR(140),
        `rows_deleted`         BIGINT NOT NULL DEFAULT 0,
        `timestamp`            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `executor_host`        VARCHAR(255),
        `executor_user`        VARCHAR(140),
        `duration_ms`          BIGINT NOT NULL DEFAULT 0,
        `status`               VARCHAR(20) NOT NULL DEFAULT 'pending',
        `error_msg`            TEXT,
        `total_rows_estimated` BIGINT NOT NULL DEFAULT 0,
        `batch_size`           INT NOT NULL DEFAULT 10000,
        `num_batches`          INT NOT NULL DEFAULT 0,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_job_id` (`job_id`),
        KEY `idx_season_id` (`season_id`),
        KEY `idx_status` (`status`),
        KEY `idx_timestamp` (`timestamp`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
    conn.commit()


# ---------------------------------------------------------------------------
# Session-level fixture: one-time audit table creation
# (NOT autouse — only integration tests that explicitly use db_conn will trigger this)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ensure_integration_audit_table(db_conn):
    """Create audit log table once per session.

    Integration test classes should list this in their autouse class-level fixture.
    """
    ensure_audit_table(db_conn)


# ---------------------------------------------------------------------------
# Archive output directory fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def archive_dir():
    """Temporary directory for archive Parquet output."""
    with tempfile.TemporaryDirectory(prefix="memora_inttest_") as tmpdir:
        yield tmpdir


# ===========================================================================
# Interaction Log test constants and helpers
# ===========================================================================

INTERACTION_LOG_TABLE = "tabMemora Interaction Log"
LESSON_TABLE          = "tabMemora Lesson"
TOPIC_TABLE           = "tabMemora Topic"

# Interaction log test job names (reserved for IL integration tests)
IL_TEST_JOB_SCHED_A   = "ARCH-IL-001"
IL_TEST_JOB_SCHED_B   = "ARCH-IL-002"
IL_TEST_JOB_EXPORT    = "ARCH-IL-003"
IL_TEST_JOB_RETRY     = "ARCH-IL-004"
IL_TEST_JOB_TRANSFER  = "ARCH-IL-005"  # Phase 4: transfer tests
IL_TEST_JOB_INGEST    = "ARCH-IL-006"  # Phase 4: ingestion tests
IL_TEST_JOB_PURGE     = "ARCH-IL-007"  # Phase 5: purge tests
IL_TEST_JOB_REFRESH   = "ARCH-IL-008"  # Phase 6: analytics refresh tests
IL_TEST_JOB_LOGGING   = "ARCH-99009"   # Phase 7: batch logging tests

ALL_IL_TEST_JOBS = [
    IL_TEST_JOB_SCHED_A, IL_TEST_JOB_SCHED_B,
    IL_TEST_JOB_EXPORT, IL_TEST_JOB_RETRY,
    IL_TEST_JOB_TRANSFER, IL_TEST_JOB_INGEST,
    IL_TEST_JOB_PURGE, IL_TEST_JOB_REFRESH, IL_TEST_JOB_LOGGING,
]

# Date ranges for IL test data (2099 range to avoid production data collision)
IL_RANGE_A = ("2099-10-01", "2099-10-02")  # 1 day, small dataset
IL_RANGE_B = ("2099-10-02", "2099-10-03")  # 1 day, second dataset

# Scheduler test uses a multi-day range
IL_SCHED_RANGE = ("2099-11-01", "2099-11-05")  # 4 days

# IL test player IDs
IL_PLAYER_PREFIX = "IL-PLAYER"


def insert_interaction_log_rows(
    conn,
    prefix: str,
    count: int,
    date_from: str,
    date_to: str,
    num_players: int = 5,
    batch_size: int = 500,
) -> int:
    """Insert synthetic interaction log rows into tabMemora Interaction Log.

    Frappe-standard table with `name` PK and Frappe standard columns.
    timestamp is spread across [date_from, date_to).
    Returns the number of rows actually inserted.
    """
    from datetime import datetime, timedelta
    import uuid

    dt_from = datetime.fromisoformat(date_from)
    dt_to   = datetime.fromisoformat(date_to)
    span_seconds = int((dt_to - dt_from).total_seconds())

    event_types = ["Started", "Completed", "Failed", "Skipped"]
    rows = []
    inserted = 0

    for n in range(1, count + 1):
        step = max(span_seconds // max(count, 1), 1)
        ts = dt_from + timedelta(seconds=(n - 1) * step)
        player_id = f"IL-PLAYER-{(n % num_players) + 1:03d}"
        lesson_id = f"IL-LESSON-{prefix}-{(n % 3) + 1:03d}"

        rows.append((
            f"IL-{prefix}-{n:08d}",               # name (PK)
            ts.strftime("%Y-%m-%d %H:%M:%S"),      # creation
            ts.strftime("%Y-%m-%d %H:%M:%S"),      # modified
            "test@test.com",                       # modified_by
            "test@test.com",                       # owner
            0,                                     # docstatus
            n,                                     # idx
            player_id,                             # player
            lesson_id,                             # lesson
            f"STAGE-{(n % 3) + 1}",                # stage_type
            event_types[n % 4],                    # event_type
            (n % 60) + 5,                          # time_spent
            n % 3,                                 # errors_count
            ts.strftime("%Y-%m-%d %H:%M:%S"),      # timestamp
        ))

        if len(rows) >= batch_size:
            inserted += _flush_interaction_log_rows(conn, rows)
            rows = []

    if rows:
        inserted += _flush_interaction_log_rows(conn, rows)

    return inserted


def _flush_interaction_log_rows(conn, rows: list) -> int:
    """Batch-insert rows into tabMemora Interaction Log."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Interaction Log` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `player`, `lesson`, `stage_type`, "
        " `event_type`, `time_spent`, `errors_count`, `timestamp`) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()
    return len(rows)


def delete_interaction_log_rows(
    conn, date_from: str = "2099-01-01", date_to: str = "2100-01-01"
) -> None:
    """Delete interaction log test rows in the given timestamp range."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Interaction Log` "
            "WHERE `timestamp` >= %s AND `timestamp` < %s",
            (date_from, date_to),
        )
    conn.commit()


def delete_interaction_log_rows_by_prefix(conn, name_prefix: str) -> None:
    """Delete interaction log rows whose name starts with the given prefix."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Interaction Log` WHERE `name` LIKE %s",
            (f"{name_prefix}%",),
        )
    conn.commit()


def count_interaction_logs(conn, date_from: str, date_to: str) -> int:
    """Count interaction log rows within the given timestamp range."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM `tabMemora Interaction Log` "
            "WHERE `timestamp` >= %s AND `timestamp` < %s",
            (date_from, date_to),
        )
        return cursor.fetchone()["cnt"]


def insert_test_lessons(conn, lesson_ids: list[str]) -> None:
    """Ensure test lesson records exist in tabMemora Lesson."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Lesson` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `lesson_title`, `is_published`, `is_reviewable`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s, %s, 1, 1)"
    )
    rows = [(lid, i + 1, f"Test Lesson {lid}") for i, lid in enumerate(lesson_ids)]
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()


def delete_test_lessons(conn, name_prefix: str = "IL-LESSON-") -> None:
    """Delete test lesson rows whose name starts with the given prefix."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Lesson` WHERE `name` LIKE %s",
            (f"{name_prefix}%",),
        )
    conn.commit()


def delete_il_test_jobs(conn, job_names: list | None = None) -> None:
    """Remove IL test archive jobs."""
    names = job_names or ALL_IL_TEST_JOBS
    if not names:
        return
    placeholders = ", ".join(["%s"] * len(names))
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `tabMemora Archive Job` WHERE `name` IN ({placeholders})",
            names,
        )
    conn.commit()


def delete_scheduler_jobs_by_scope(conn, scope_prefix: str = "2099-11-") -> None:
    """Delete Interaction Log archive jobs whose archive_scope starts with the given prefix."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Archive Job` "
            "WHERE source_doctype = 'Memora Interaction Log' "
            "  AND archive_scope LIKE %s",
            (f"{scope_prefix}%",),
        )
    conn.commit()


# ===========================================================================
# Memory State test constants and helpers (T017)
# ===========================================================================

MEMORY_STATE_TABLE = "tabMemora Memory State"
SEASON_TABLE       = "tabMemora Season"

# Season prefixes for test seasons (high numbers to avoid production collision)
MS_SEASON_SEQ_A = 9901   # Active season
MS_SEASON_SEQ_B = 9902   # Active season B
MS_SEASON_SEQ_E = 9903   # Ended season (archive-eligible)

MS_SEASON_NAME_A = "SEAS-TEST-9901"
MS_SEASON_NAME_B = "SEAS-TEST-9902"
MS_SEASON_NAME_E = "SEAS-TEST-9903"

# Memory State test archive job names
MS_TEST_JOB_ARCHIVE  = "ARCH-MS-001"
MS_TEST_JOB_PURGE    = "ARCH-MS-002"
MS_TEST_JOB_SYNC     = "ARCH-MS-003"

ALL_MS_TEST_JOBS = [
    MS_TEST_JOB_ARCHIVE, MS_TEST_JOB_PURGE, MS_TEST_JOB_SYNC,
]

ALL_MS_SEASON_NAMES = [MS_SEASON_NAME_A, MS_SEASON_NAME_B, MS_SEASON_NAME_E]
ALL_MS_SEASON_SEQS  = [MS_SEASON_SEQ_A, MS_SEASON_SEQ_B, MS_SEASON_SEQ_E]

# Player prefix for Memory State tests
MS_PLAYER_PREFIX = "MS-PLAYER"


def _make_season_job_meta(season_seq: int, season_name: str) -> str:
    """Build job_meta JSON for a Memory State season-scoped archive job."""
    meta = {
        "query_filter": {
            "season_seq": season_seq,
            "season_name": season_name,
            "filter_column": "season_seq",
            "filter_type": "season",
        },
        "export_columns": [
            "name", "season_seq", "subject", "player", "item_id",
            "stability", "difficulty", "next_review",
            "lesson", "state", "step", "last_review", "modified",
        ],
        "schema_snapshot": {
            "columns": [
                {"name": "name", "type": "BIGINT"},
                {"name": "season_seq", "type": "INT"},
                {"name": "subject", "type": "VARCHAR(140)"},
                {"name": "player", "type": "VARCHAR(140)"},
                {"name": "item_id", "type": "VARCHAR(36)"},
                {"name": "stability", "type": "FLOAT"},
                {"name": "difficulty", "type": "FLOAT"},
                {"name": "next_review", "type": "DATETIME"},
                {"name": "lesson", "type": "VARCHAR(140)"},
                {"name": "state", "type": "TINYINT"},
                {"name": "step", "type": "TINYINT"},
                {"name": "last_review", "type": "DATETIME"},
                {"name": "modified", "type": "DATETIME"},
            ],
            "primary_key": ["name", "season_seq"],
        },
        "related_tables": [
            {
                "entity": "player",
                "schema_version": "v3",
                "join_column": "player",
                "fact_column": "player",
            },
            {
                "entity": "season",
                "schema_version": "v1",
                "scope_source": "derived",
            },
        ],
        "fact_sql": {
            "filtered": (
                "SELECT ms.`name`, ms.`season_seq`, ms.`subject`, ms.`player`, "
                "BIN_TO_UUID(ms.`item_id`) AS item_id, "
                "ms.`stability`, ms.`difficulty`, ms.`next_review`, "
                "ms.`lesson`, ms.`state`, ms.`step`, ms.`last_review`, ms.`modified` "
                "FROM `tabMemora Memory State` ms "
                "WHERE ms.`season_seq` = %s ORDER BY ms.`name`"
            ),
            "incremental": (
                "SELECT ms.`name`, ms.`season_seq`, ms.`subject`, ms.`player`, "
                "BIN_TO_UUID(ms.`item_id`) AS item_id, "
                "ms.`stability`, ms.`difficulty`, ms.`next_review`, "
                "ms.`lesson`, ms.`state`, ms.`step`, ms.`last_review`, ms.`modified` "
                "FROM `tabMemora Memory State` ms "
                "WHERE ms.`season_seq` = %s AND ms.`modified` >= %s "
                "ORDER BY ms.`modified`"
            ),
        },
        "scope_column": "season_seq",
    }
    return json.dumps(meta)


def insert_memory_state_rows(
    conn,
    prefix: str,
    count: int,
    season_seq: int,
    num_players: int = 5,
    batch_size: int = 500,
) -> int:
    """Insert synthetic Memory State rows into tabMemora Memory State.

    Frappe-standard table with BIGINT `name` PK, RANGE-partitioned by season_seq.
    item_id is BINARY(16) — generated via UUID_TO_BIN(UUID()).
    Returns the number of rows actually inserted.
    """
    from datetime import datetime, timedelta
    import uuid

    now = datetime(2099, 6, 1, 12, 0, 0)  # Far-future base timestamp
    rows = []
    inserted = 0

    for n in range(1, count + 1):
        ts = now + timedelta(seconds=n * 60)
        player_id = f"{MS_PLAYER_PREFIX}-{(n % num_players) + 1:03d}"
        item_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{prefix}-{n}").bytes

        rows.append((
            season_seq,                                # season_seq (partition key)
            f"Science-{(n % 3) + 1}",                 # subject
            player_id,                                 # player
            item_uuid,                                 # item_id (BINARY(16))
            round(0.5 + (n % 100) * 0.05, 4),         # stability (>= 0)
            round((n % 100) / 100.0, 4),               # difficulty (0–1)
            ts.strftime("%Y-%m-%d %H:%M:%S"),          # next_review
            f"MS-LESSON-{prefix}-{(n % 3) + 1:03d}",  # lesson
            n % 4,                                     # state (TINYINT 0–3)
            n % 5 if n % 3 != 0 else None,            # step (nullable)
            ts.strftime("%Y-%m-%d %H:%M:%S"),          # last_review
            ts.strftime("%Y-%m-%d %H:%M:%S"),          # creation
            ts.strftime("%Y-%m-%d %H:%M:%S"),          # modified
            "test@test.com",                           # modified_by
            "test@test.com",                           # owner
        ))

        if len(rows) >= batch_size:
            inserted += _flush_memory_state_rows(conn, rows)
            rows = []

    if rows:
        inserted += _flush_memory_state_rows(conn, rows)

    return inserted


def _flush_memory_state_rows(conn, rows: list) -> int:
    """Batch-insert rows into tabMemora Memory State."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Memory State` "
        "(`season_seq`, `subject`, `player`, `item_id`, "
        " `stability`, `difficulty`, `next_review`, `lesson`, "
        " `state`, `step`, `last_review`, "
        " `creation`, `modified`, `modified_by`, `owner`) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()
    return len(rows)


def delete_memory_state_rows(conn, season_seq: int) -> None:
    """Delete all Memory State test rows for a given season_seq."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Memory State` WHERE `season_seq` = %s",
            (season_seq,),
        )
    conn.commit()


def delete_memory_state_rows_by_player_prefix(conn, prefix: str = MS_PLAYER_PREFIX) -> None:
    """Delete Memory State rows whose player starts with the given prefix."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Memory State` WHERE `player` LIKE %s",
            (f"{prefix}%",),
        )
    conn.commit()


def count_memory_state_rows(conn, season_seq: int) -> int:
    """Count Memory State rows for a given season_seq."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM `tabMemora Memory State` "
            "WHERE `season_seq` = %s",
            (season_seq,),
        )
        return cursor.fetchone()["cnt"]


def insert_test_seasons(conn) -> None:
    """Insert test season records for Memory State integration tests.

    Creates:
    - MS_SEASON_SEQ_A (9901): Active, end_date in the future
    - MS_SEASON_SEQ_B (9902): Active, end_date in the future
    - MS_SEASON_SEQ_E (9903): Ended, end_date in the past
    """
    sql = (
        "INSERT IGNORE INTO `tabMemora Season` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `season_title`, `season_seq`, "
        " `start_date`, `end_date`, `is_published`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', "
        "        0, %s, %s, %s, %s, %s, %s)"
    )
    rows = [
        (MS_SEASON_NAME_A, 1, "Test Season A (Active)", MS_SEASON_SEQ_A,
         "2099-01-01", "2099-12-31", 1),
        (MS_SEASON_NAME_B, 2, "Test Season B (Active)", MS_SEASON_SEQ_B,
         "2099-01-01", "2099-12-31", 1),
        (MS_SEASON_NAME_E, 3, "Test Season E (Ended)", MS_SEASON_SEQ_E,
         "2098-01-01", "2098-12-31", 1),
    ]
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()


def delete_test_seasons(conn) -> None:
    """Delete test season records."""
    placeholders = ", ".join(["%s"] * len(ALL_MS_SEASON_NAMES))
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `tabMemora Season` WHERE `name` IN ({placeholders})",
            ALL_MS_SEASON_NAMES,
        )
    conn.commit()


def delete_ms_test_jobs(conn, job_names: list | None = None) -> None:
    """Remove Memory State test archive jobs."""
    names = job_names or ALL_MS_TEST_JOBS
    if not names:
        return
    placeholders = ", ".join(["%s"] * len(names))
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM `tabMemora Archive Job` WHERE `name` IN ({placeholders})",
            names,
        )
    conn.commit()


def delete_ms_jobs_by_scope(conn, scope_prefix: str = "season_990") -> None:
    """Delete Memory State archive jobs whose archive_scope starts with the given prefix."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Archive Job` "
            "WHERE source_doctype = 'Memora Memory State' "
            "  AND archive_scope LIKE %s",
            (f"{scope_prefix}%",),
        )
    conn.commit()


def upsert_ms_archive_job(
    conn,
    name: str,
    status: str,
    season_seq: int,
    season_name: str,
    file_path: str = "",
    post_archive_action: str = "Delete",
    source_deleted: int = 0,
) -> None:
    """Insert or replace a Memory State test archive job.

    Uses the season-scoped job_meta format (filter_type=season).
    """
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
        "        %s, 'Normal', 0, %s, %s, 0, "
        "        0, 0, 0, "
        "        %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  status=%s, file_path=%s, post_archive_action=%s, "
        "  source_deleted=%s, job_meta=%s, modified=NOW()"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            name, archive_scope, status, post_archive_action, source_deleted,
            file_path, job_meta,
            status, file_path, post_archive_action, source_deleted, job_meta,
        ))
    conn.commit()
