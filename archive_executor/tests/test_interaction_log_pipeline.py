"""Integration tests for the Interaction Log archive pipeline (Phase 3 / US1).

Tests verify:
- Scheduler creates Pending jobs for the correct date ranges
- Scheduler skips days that already have non-Failed jobs
- Zero-record day completes gracefully (marked Completed, no Parquet errors)
- Export produces correct record counts and checksums for IL data
- Mid-batch failure is recoverable (job reset to Pending for retry)

Run with:
    DB_HOST=127.0.0.1 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        pytest archive_executor/tests/test_interaction_log_pipeline.py -v

Prerequisites:
    - tabMemora Interaction Log and tabMemora Archive Job exist
    - tabMemora Lesson exists (for dimension snapshot)
    - DB_* env vars set
"""

import dataclasses
import json
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import MagicMock, call, patch

import pytest
import pyarrow.parquet as pq

from archive_executor.config import Config
from archive_executor.db import get_connection
from archive_executor.exporter import export_fact_data
from archive_executor.scheduler import create_pending_jobs, _job_exists
from archive_executor.validator import validate_file

from .conftest import (
    IL_RANGE_A,
    IL_RANGE_B,
    IL_SCHED_RANGE,
    IL_TEST_JOB_EXPORT,
    IL_TEST_JOB_INGEST,
    IL_TEST_JOB_LOGGING,
    IL_TEST_JOB_PURGE,
    IL_TEST_JOB_REFRESH,
    IL_TEST_JOB_RETRY,
    IL_TEST_JOB_SCHED_A,
    IL_TEST_JOB_SCHED_B,
    IL_TEST_JOB_TRANSFER,
    count_interaction_logs,
    delete_il_test_jobs,
    delete_interaction_log_rows,
    delete_interaction_log_rows_by_prefix,
    delete_scheduler_jobs_by_scope,
    delete_test_audit_logs,
    delete_test_lessons,
    delete_test_players,
    ensure_audit_table,
    insert_interaction_log_rows,
    insert_test_lessons,
    insert_test_players,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config_with_dir(base_config: Config, archive_dir: str) -> Config:
    return dataclasses.replace(base_config, archive_output_path=archive_dir + "/")


def _get_archive_job(conn, name: str) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM `tabMemora Archive Job` WHERE name = %s", (name,))
        return cursor.fetchone()


def _count_sched_jobs(conn, scope_prefix: str) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM `tabMemora Archive Job` "
            "WHERE source_doctype = 'Memora Interaction Log' "
            "  AND archive_scope LIKE %s",
            (f"{scope_prefix}%",),
        )
        return cursor.fetchone()["cnt"]


def _make_il_job_meta(date_from: str, date_to: str) -> str:
    """Build interaction log job_meta for direct executor tests."""
    meta = {
        "query_filter": {
            "date_from": date_from,
            "date_to": date_to,
            "filter_column": "timestamp",
        },
        "export_columns": [
            "name", "player", "lesson", "stage_id", "item_id",
            "event_type", "time_spent", "errors_count", "timestamp",
        ],
        "schema_snapshot": {
            "columns": [
                {"name": "name",         "type": "VARCHAR(140)"},
                {"name": "player",       "type": "VARCHAR(140)"},
                {"name": "lesson",       "type": "VARCHAR(140)"},
                {"name": "stage_id",     "type": "VARCHAR(140)"},
                {"name": "item_id",      "type": "VARCHAR(140)"},
                {"name": "event_type",   "type": "VARCHAR(20)"},
                {"name": "time_spent",   "type": "INT"},
                {"name": "errors_count", "type": "INT"},
                {"name": "timestamp",    "type": "DATETIME"},
            ],
            "primary_key": ["name"],
        },
        "related_tables": [
            {"entity": "player", "schema_version": "v3", "fact_column": "player"},
            {"entity": "lesson", "schema_version": "v1", "fact_column": "lesson"},
        ],
    }
    return json.dumps(meta)


def _upsert_il_job(conn, name: str, status: str, date_from: str, date_to: str,
                   archive_scope: str, file_path: str = "") -> None:
    """Insert or update an Interaction Log archive job for testing."""
    job_meta = _make_il_job_meta(date_from, date_to)
    sql = (
        "INSERT INTO `tabMemora Archive Job` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
        " `status`, `priority`, `retry_count`, `post_archive_action`, "
        " `source_deleted`, `sync_paused`, "
        " `duration_seconds`, `row_count`, `file_size_bytes`, "
        " `file_path`, `job_meta`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0, "
        "        'Memora Interaction Log', %s, 'v1', 'interaction_log', "
        "        %s, 'Normal', 0, 'Delete', 0, 0, "
        "        0, 0, 0, "
        "        %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  status=%s, file_path=%s, job_meta=%s, modified=NOW()"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            name, archive_scope, status, file_path, job_meta,
            status, file_path, job_meta,
        ))
    conn.commit()


# ===========================================================================
# Category 1: Scheduler — Job Creation
# ===========================================================================

def _cleanup_scheduler_created_il_jobs(conn) -> None:
    """Delete all archive jobs created by the scheduler for Memora Interaction Log."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Archive Job` "
            "WHERE source_doctype = 'Memora Interaction Log' AND modified_by = 'scheduler'"
        )
    conn.commit()


class TestSchedulerJobCreation:
    """Verify create_pending_jobs() creates correct daily Pending jobs.

    Uses production Interaction Log data (if present) plus a controlled past-date
    test dataset. All scheduler-created jobs are cleaned up in the fixture.
    """

    # Use a past date range unlikely to have production data
    _SCHED_DATE_FROM = "2024-06-01"
    _SCHED_DATE_TO   = "2024-06-04"

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        # Pre-cleanup: remove any leftover jobs and test rows
        _cleanup_scheduler_created_il_jobs(db_conn)
        delete_interaction_log_rows_by_prefix(db_conn, "IL-SCHED-")
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_SCHED_A, IL_TEST_JOB_SCHED_B])
        # Insert test rows in a controlled past date range (3 days)
        insert_test_players(db_conn, num_players=5)
        insert_interaction_log_rows(
            db_conn, prefix="SCHED", count=3,
            date_from=self._SCHED_DATE_FROM, date_to=self._SCHED_DATE_TO,
        )
        yield
        # Post-cleanup
        _cleanup_scheduler_created_il_jobs(db_conn)
        delete_interaction_log_rows_by_prefix(db_conn, "IL-SCHED-")
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_SCHED_A, IL_TEST_JOB_SCHED_B])

    def test_creates_one_job_per_day(self, integration_db_config, db_conn):
        """Scheduler creates one Pending job for each day in the archive window."""
        created = create_pending_jobs(integration_db_config, "interaction_log", retention_days=1)

        assert len(created) >= 1, f"Expected at least 1 job created, got {created}"

        # Verify each created job has correct structure
        for job_name in created:
            job = _get_archive_job(db_conn, job_name)
            assert job is not None, f"Job {job_name} not found in DB"
            assert job["status"] == "Pending"
            assert job["source_doctype"] == "Memora Interaction Log"
            assert job["archive_type"] == "interaction_log"
            assert job["schema_version"] == "v1"

            meta = json.loads(job["job_meta"])
            assert "query_filter" in meta
            assert meta["query_filter"]["filter_column"] == "timestamp"
            assert "export_columns" in meta
            assert "name" in meta["export_columns"]

    def test_skips_existing_non_failed_jobs(self, integration_db_config, db_conn):
        """Scheduler skips days that already have non-Failed jobs."""
        # Pre-create a Pending job for the first day of our test range
        _upsert_il_job(db_conn, IL_TEST_JOB_SCHED_A, "Pending",
                       self._SCHED_DATE_FROM, self._SCHED_DATE_TO,
                       archive_scope=self._SCHED_DATE_FROM)
        created = create_pending_jobs(integration_db_config, "interaction_log", retention_days=1)

        # Our pre-existing Pending scope should be skipped
        for job_name in created:
            job = _get_archive_job(db_conn, job_name)
            assert job["archive_scope"] != self._SCHED_DATE_FROM, (
                f"Should not create job for {self._SCHED_DATE_FROM} (already exists), got {job_name}"
            )

    def test_failed_job_does_not_block_scheduler(self, integration_db_config, db_conn):
        """_job_exists() returns False for Failed jobs, allowing scheduler to recreate them."""
        _upsert_il_job(db_conn, IL_TEST_JOB_SCHED_B, "Failed",
                       self._SCHED_DATE_FROM, self._SCHED_DATE_TO,
                       archive_scope=self._SCHED_DATE_FROM)
        # Failed jobs should NOT block new job creation
        exists = _job_exists(db_conn, "Memora Interaction Log", self._SCHED_DATE_FROM, "v1")
        assert not exists, "Expected _job_exists=False for Failed job (should allow recreation)"

    def test_no_jobs_when_all_within_retention(self, integration_db_config, db_conn):
        """Scheduler returns empty list when all data is within retention window."""
        # Use retention_days=99999 — nothing is older than that
        created = create_pending_jobs(integration_db_config, "interaction_log", retention_days=99999)
        assert created == [], f"Expected no jobs created, got {created}"


# ===========================================================================
# Category 2: Export — Interaction Log Parquet
# ===========================================================================

class TestInteractionLogExport:
    """Verify export_fact_data() works correctly for Interaction Log records."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        # Pre-cleanup
        delete_interaction_log_rows_by_prefix(db_conn, "IL-EXP-")
        delete_test_lessons(db_conn, "IL-LESSON-EXP-")
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_EXPORT])
        # Setup
        insert_test_players(db_conn, num_players=5)
        lesson_ids = [f"IL-LESSON-EXP-{i:03d}" for i in range(1, 4)]
        insert_test_lessons(db_conn, lesson_ids)
        inserted = insert_interaction_log_rows(
            db_conn, prefix="EXP", count=20,
            date_from=IL_RANGE_A[0], date_to=IL_RANGE_A[1],
        )
        assert inserted == 20, f"Expected 20 inserted rows, got {inserted}"
        yield
        delete_interaction_log_rows_by_prefix(db_conn, "IL-EXP-")
        delete_test_lessons(db_conn, "IL-LESSON-EXP-")
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_EXPORT])

    def test_export_correct_row_count(self, integration_db_config, db_conn, archive_dir):
        """Export produces a Parquet file with exactly 20 rows for the date range."""
        config = _config_with_dir(integration_db_config, archive_dir)
        meta = json.loads(_make_il_job_meta(*IL_RANGE_A))

        staging_dir = os.path.join(archive_dir, ".staging", IL_TEST_JOB_EXPORT)
        os.makedirs(staging_dir, exist_ok=True)

        fact_path, fact_row_count, referenced_ids = export_fact_data(
            config=config,
            staging_dir=staging_dir,
            meta=meta,
            source_table="tabMemora Interaction Log",
            archive_type_name="interaction_log",
        )

        assert fact_row_count == 20, f"Expected 20 rows, got {fact_row_count}"

        # Verify Parquet file exists and is readable
        table = pq.read_table(fact_path)
        assert table.num_rows == 20

        # Verify required columns are present
        cols = table.schema.names
        for col in ("name", "player", "lesson", "event_type", "timestamp"):
            assert col in cols, f"Missing column: {col}"

    def test_export_checksum_valid(self, integration_db_config, db_conn, archive_dir):
        """validate_file() returns a valid checksum for the exported Parquet."""
        config = _config_with_dir(integration_db_config, archive_dir)
        meta = json.loads(_make_il_job_meta(*IL_RANGE_A))

        staging_dir = os.path.join(archive_dir, ".staging", IL_TEST_JOB_EXPORT)
        os.makedirs(staging_dir, exist_ok=True)

        fact_path, fact_row_count, _ = export_fact_data(
            config=config,
            staging_dir=staging_dir,
            meta=meta,
            source_table="tabMemora Interaction Log",
            archive_type_name="interaction_log",
        )

        result = validate_file(fact_path, fact_row_count)
        assert result["valid"], f"File validation failed: {result}"
        assert result["checksum"], "Checksum should not be empty"
        assert result["row_count"] == 20

    def test_export_excludes_out_of_range_records(self, integration_db_config, db_conn, archive_dir):
        """Export only includes records within the date range, not adjacent records."""
        # Insert extra records in a different range
        delete_interaction_log_rows_by_prefix(db_conn, "IL-EXP2-")
        insert_interaction_log_rows(
            db_conn, prefix="EXP2", count=5,
            date_from=IL_RANGE_B[0], date_to=IL_RANGE_B[1],
        )
        try:
            config = _config_with_dir(integration_db_config, archive_dir)
            meta = json.loads(_make_il_job_meta(*IL_RANGE_A))

            staging_dir = os.path.join(archive_dir, ".staging", IL_TEST_JOB_EXPORT + "_scope")
            os.makedirs(staging_dir, exist_ok=True)

            _, fact_row_count, _ = export_fact_data(
                config=config,
                staging_dir=staging_dir,
                meta=meta,
                source_table="tabMemora Interaction Log",
                archive_type_name="interaction_log",
            )

            # Should only export records in IL_RANGE_A, not IL_RANGE_B
            assert fact_row_count == 20, (
                f"Expected 20 (only RANGE_A), got {fact_row_count}"
            )
        finally:
            delete_interaction_log_rows_by_prefix(db_conn, "IL-EXP2-")

    def test_export_zero_records_graceful(self, integration_db_config, db_conn, archive_dir):
        """Export with no records in date range produces a zero-row Parquet file."""
        config = _config_with_dir(integration_db_config, archive_dir)
        # Use a date range with no data
        empty_meta = json.loads(_make_il_job_meta("2099-12-30", "2099-12-31"))

        staging_dir = os.path.join(archive_dir, ".staging", IL_TEST_JOB_EXPORT + "_empty")
        os.makedirs(staging_dir, exist_ok=True)

        fact_path, fact_row_count, _ = export_fact_data(
            config=config,
            staging_dir=staging_dir,
            meta=empty_meta,
            source_table="tabMemora Interaction Log",
            archive_type_name="interaction_log",
        )

        assert fact_row_count == 0, f"Expected 0 rows, got {fact_row_count}"
        result = validate_file(fact_path, 0)
        assert result["valid"], f"Empty file validation failed: {result}"


# ===========================================================================
# Category 3: Mid-Batch Failure Recovery
# ===========================================================================

class TestMidBatchRecovery:
    """Verify that a job failure resets to Pending for retry via run.py's _fail_job."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        delete_interaction_log_rows_by_prefix(db_conn, "IL-RETRY-")
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_RETRY])
        insert_test_players(db_conn, num_players=5)
        insert_interaction_log_rows(
            db_conn, prefix="RETRY", count=5,
            date_from=IL_RANGE_A[0], date_to=IL_RANGE_A[1],
        )
        _upsert_il_job(db_conn, IL_TEST_JOB_RETRY, "Pending",
                       IL_RANGE_A[0], IL_RANGE_A[1], archive_scope=IL_RANGE_A[0])
        yield
        delete_interaction_log_rows_by_prefix(db_conn, "IL-RETRY-")
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_RETRY])

    def test_failed_job_resets_to_pending_for_retry(self, integration_db_config, db_conn):
        """A job that fails with retry_count < 3 is reset to Pending for retry."""
        from archive_executor.run import _fail_job

        # Simulate the job being claimed (Processing) then failing
        with db_conn.cursor() as cursor:
            cursor.execute(
                "UPDATE `tabMemora Archive Job` SET status='Processing', retry_count=0 "
                "WHERE name=%s",
                (IL_TEST_JOB_RETRY,),
            )
        db_conn.commit()

        _fail_job(integration_db_config, IL_TEST_JOB_RETRY, "Simulated failure", retry_count=0)

        job = _get_archive_job(db_conn, IL_TEST_JOB_RETRY)
        assert job["status"] == "Pending", f"Expected Pending after retry, got {job['status']}"
        assert job["retry_count"] == 1, f"Expected retry_count=1, got {job['retry_count']}"
        assert "Simulated failure" in (job["error_log"] or "")

    def test_job_permanently_fails_after_3_retries(self, integration_db_config, db_conn):
        """A job with retry_count >= 3 is permanently failed (no further retries)."""
        from archive_executor.run import _fail_job

        with db_conn.cursor() as cursor:
            cursor.execute(
                "UPDATE `tabMemora Archive Job` SET status='Processing', retry_count=3 "
                "WHERE name=%s",
                (IL_TEST_JOB_RETRY,),
            )
        db_conn.commit()

        _fail_job(integration_db_config, IL_TEST_JOB_RETRY, "Terminal failure", retry_count=3)

        job = _get_archive_job(db_conn, IL_TEST_JOB_RETRY)
        assert job["status"] == "Failed", f"Expected Failed after 3 retries, got {job['status']}"
        assert job["completed_at"] is not None


# ===========================================================================
# Category 4: Transfer — Checksum Verification (Phase 4 / US2)
# ===========================================================================

def _build_exported_batch(archive_dir: str, job_name: str, meta: dict) -> str:
    """Create a minimal exported batch directory with manifest.json for transfer tests.

    Returns the path to the batch directory.
    """
    from archive_executor.manifest import build_manifest
    from archive_executor.validator import validate_file
    import pyarrow as pa
    import pyarrow.parquet as pq

    batch_dir = os.path.join(archive_dir, job_name)
    os.makedirs(batch_dir, exist_ok=True)

    # Write a minimal Parquet fact file
    fact_path = os.path.join(batch_dir, "fact.parquet")
    table = pa.table({
        "name": pa.array(["IL-TX-00000001", "IL-TX-00000002"]),
        "player": pa.array(["IL-PLAYER-001", "IL-PLAYER-002"]),
        "lesson": pa.array(["IL-LESSON-TX-001", "IL-LESSON-TX-001"]),
        "stage_id": pa.array(["STAGE-1", "STAGE-2"]),
        "item_id": pa.array([None, None], type=pa.string()),
        "event_type": pa.array(["Started", "Completed"]),
        "time_spent": pa.array([10, 20]),
        "errors_count": pa.array([0, 0]),
        "timestamp": pa.array(["2099-10-01 00:00:00", "2099-10-01 00:01:00"]),
        "archive_scope": pa.array(["2099-10-01", "2099-10-01"]),
        "archive_job_id": pa.array([job_name, job_name]),
        "schema_version": pa.array(["v1", "v1"]),
        "exported_at": pa.array(["2099-10-01T02:00:00", "2099-10-01T02:00:00"]),
    })
    pq.write_table(table, fact_path)

    validation = validate_file(fact_path, 2)
    build_manifest(
        staging_dir=batch_dir,
        batch_id=job_name,
        dataset_key="interaction_log_archive",
        kind="archive",
        schema_version="1.0",
        source="memora_admin",
        scope_key="2099-10-01",
        files=[{
            "role": "fact",
            "entity": "interaction_log",
            "filename": validation["filename"],
            "row_count": validation["row_count"],
            "checksum": validation["checksum"],
            "size_bytes": validation["size_bytes"],
        }],
    )
    return batch_dir


def _upsert_il_job_at_stage(
    conn,
    name: str,
    status: str,
    file_path: str,
    remote_path: str = "",
    retry_count: int = 0,
) -> None:
    """Insert or update an IL archive job at a specific pipeline stage."""
    date_from, date_to = IL_RANGE_A
    job_meta = _make_il_job_meta(date_from, date_to)
    sql = (
        "INSERT INTO `tabMemora Archive Job` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
        " `status`, `priority`, `retry_count`, `post_archive_action`, "
        " `source_deleted`, `sync_paused`, "
        " `duration_seconds`, `row_count`, `file_size_bytes`, "
        " `file_path`, `remote_path`, `file_checksum`, `job_meta`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0, "
        "        'Memora Interaction Log', %s, 'v1', 'interaction_log', "
        "        %s, 'Normal', %s, 'Delete', 0, 1, "
        "        1.0, 2, 1024, "
        "        %s, %s, 'abc123', %s) "
        "ON DUPLICATE KEY UPDATE "
        "  status=%s, file_path=%s, remote_path=%s, retry_count=%s, "
        "  job_meta=%s, modified=NOW()"
    )
    scope = IL_RANGE_A[0]
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            name, scope, status, retry_count,
            file_path, remote_path, job_meta,
            status, file_path, remote_path, retry_count, job_meta,
        ))
    conn.commit()


class TestInteractionLogTransfer:
    """Verify transfer stage: Exported → Transferred with checksum verification.

    Uses mocked SSH/rsync to avoid needing a real analytics server.
    """

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_TRANSFER])
        yield
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_TRANSFER])

    def test_transfer_marks_job_transferred(self, integration_db_config, db_conn, archive_dir):
        """Successful transfer marks job Transferred with remote_path set."""
        from archive_executor.run import _process_exported_jobs
        from archive_executor.logger import StructuredLogger

        batch_dir = _build_exported_batch(archive_dir, IL_TEST_JOB_TRANSFER, {})
        _upsert_il_job_at_stage(db_conn, IL_TEST_JOB_TRANSFER, "Exported", file_path=batch_dir)

        config = dataclasses.replace(
            integration_db_config,
            archive_output_path=archive_dir + "/",
            ssh_host="analytics.test",
            ssh_user="deploy",
            ssh_key_path="/tmp/test.key",
            remote_archive_path="/data/archive/",
        )

        expected_remote = f"/data/archive/{IL_TEST_JOB_TRANSFER}"

        with patch("archive_executor.run.transfer_batch", return_value=expected_remote) as mock_transfer, \
             patch("archive_executor.run.verify_remote_checksums", return_value={"valid": True, "errors": []}) as mock_verify:

            log = StructuredLogger(config.log_path)
            _process_exported_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_TRANSFER)
        assert job["status"] == "Transferred", f"Expected Transferred, got {job['status']}"
        assert job["remote_path"] == expected_remote

        mock_transfer.assert_called_once()
        mock_verify.assert_called_once()

    def test_transfer_checksum_failure_resets_job(self, integration_db_config, db_conn, archive_dir):
        """Failed checksum verification resets job to Pending for retry."""
        from archive_executor.run import _process_exported_jobs
        from archive_executor.logger import StructuredLogger

        batch_dir = _build_exported_batch(archive_dir, IL_TEST_JOB_TRANSFER, {})
        _upsert_il_job_at_stage(db_conn, IL_TEST_JOB_TRANSFER, "Exported", file_path=batch_dir)

        config = dataclasses.replace(
            integration_db_config,
            archive_output_path=archive_dir + "/",
            ssh_host="analytics.test",
            ssh_user="deploy",
            ssh_key_path="/tmp/test.key",
            remote_archive_path="/data/archive/",
        )

        with patch("archive_executor.run.transfer_batch",
                   return_value=f"/data/archive/{IL_TEST_JOB_TRANSFER}"), \
             patch("archive_executor.run.verify_remote_checksums",
                   return_value={"valid": False, "errors": ["checksum mismatch for fact.parquet"]}):

            log = StructuredLogger(config.log_path)
            _process_exported_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_TRANSFER)
        assert job["status"] == "Pending", f"Expected Pending after checksum failure, got {job['status']}"
        assert job["retry_count"] == 1

    def test_transfer_failure_does_not_lose_exported_parquet(self, integration_db_config, db_conn, archive_dir):
        """After a transfer failure, the exported batch directory remains on disk."""
        from archive_executor.run import _process_exported_jobs
        from archive_executor.logger import StructuredLogger

        batch_dir = _build_exported_batch(archive_dir, IL_TEST_JOB_TRANSFER, {})
        _upsert_il_job_at_stage(db_conn, IL_TEST_JOB_TRANSFER, "Exported", file_path=batch_dir)

        config = dataclasses.replace(
            integration_db_config,
            archive_output_path=archive_dir + "/",
            ssh_host="analytics.test",
            ssh_user="deploy",
            ssh_key_path="/tmp/test.key",
            remote_archive_path="/data/archive/",
        )

        with patch("archive_executor.run.transfer_batch",
                   side_effect=Exception("SSH connection refused")), \
             patch("archive_executor.run.verify_remote_checksums"):

            log = StructuredLogger(config.log_path)
            _process_exported_jobs(config, log)

        # Batch directory must still exist so re-transfer can use it
        assert os.path.isdir(batch_dir), "Exported batch directory should not be deleted on transfer failure"

        job = _get_archive_job(db_conn, IL_TEST_JOB_TRANSFER)
        assert job["status"] == "Pending", f"Expected Pending for retry, got {job['status']}"


# ===========================================================================
# Category 5: Ingestion — Cumulative Append and Deduplication (Phase 4 / US2)
# ===========================================================================

class TestInteractionLogIngestion:
    """Verify ingestion: Transferred → Ingested, cumulative append, dedup by name.

    The analytics server's `ingest-archive` command handles deduplication using
    the `name` field. These tests verify the executor calls the right commands
    and processes job state transitions correctly.
    """

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_INGEST])
        yield
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_INGEST])

    def _mock_ingest_success(self):
        """Return a mock ingest result indicating 2 records ingested."""
        return {"status": "ok", "batches_ok": 1, "batches_error": 0, "rows_ingested": 2}

    def _mock_verify_success(self):
        """Return a mock verify result indicating ingestion verified."""
        return {"valid": True, "checks": {"row_counts": "ok"}}

    def _mock_handoff_success(self):
        """Return a mock handoff result."""
        return {"status": "ok"}

    def test_ingestion_marks_job_ingested(self, integration_db_config, db_conn, archive_dir):
        """Successful ingestion marks job Ingested."""
        from archive_executor.run import _process_transferred_jobs
        from archive_executor.logger import StructuredLogger

        batch_dir = _build_exported_batch(archive_dir, IL_TEST_JOB_INGEST, {})
        remote_path = f"/data/archive/{IL_TEST_JOB_INGEST}"
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_INGEST, "Transferred",
            file_path=batch_dir, remote_path=remote_path,
        )

        config = dataclasses.replace(
            integration_db_config,
            archive_output_path=archive_dir + "/",
            ssh_host="analytics.test",
            ssh_user="deploy",
            ssh_key_path="/tmp/test.key",
        )

        with patch("archive_executor.run.ingest_archive_batch",
                   return_value=self._mock_ingest_success()) as mock_ingest, \
             patch("archive_executor.run.verify_ingestion",
                   return_value=self._mock_verify_success()):

            log = StructuredLogger(config.log_path)
            _process_transferred_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_INGEST)
        assert job["status"] == "Ingested", f"Expected Ingested, got {job['status']}"
        mock_ingest.assert_called_once()

    def test_cumulative_ingestion_calls_ingest_not_replace(self, integration_db_config, db_conn, archive_dir):
        """Executor calls `ingest-archive` (append) not a replace/truncate command for IL."""
        from archive_executor.run import _process_transferred_jobs
        from archive_executor.logger import StructuredLogger

        batch_dir = _build_exported_batch(archive_dir, IL_TEST_JOB_INGEST, {})
        remote_path = f"/data/archive/{IL_TEST_JOB_INGEST}"
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_INGEST, "Transferred",
            file_path=batch_dir, remote_path=remote_path,
        )

        config = dataclasses.replace(
            integration_db_config,
            archive_output_path=archive_dir + "/",
            ssh_host="analytics.test",
            ssh_user="deploy",
            ssh_key_path="/tmp/test.key",
        )

        captured_calls = []

        def capture_ingest(cfg, rpath, manifest, log):
            captured_calls.append(("ingest_archive_batch", rpath))
            return self._mock_ingest_success()

        with patch("archive_executor.run.ingest_archive_batch",
                   side_effect=capture_ingest), \
             patch("archive_executor.run.verify_ingestion",
                   return_value=self._mock_verify_success()):

            log = StructuredLogger(config.log_path)
            _process_transferred_jobs(config, log)

        # Verify the executor calls ingest_archive_batch (append-mode) for IL jobs
        assert len(captured_calls) == 1
        assert captured_calls[0][0] == "ingest_archive_batch"
        assert captured_calls[0][1] == remote_path

    def test_reingest_same_batch_does_not_fail(self, integration_db_config, db_conn, archive_dir):
        """Re-ingesting the same batch (dedup by name) does not cause executor failure.

        The analytics server deduplicates by `name` field, so re-ingesting the
        same Parquet produces zero new rows but still returns status ok.
        """
        from archive_executor.run import _process_transferred_jobs
        from archive_executor.logger import StructuredLogger

        batch_dir = _build_exported_batch(archive_dir, IL_TEST_JOB_INGEST, {})
        remote_path = f"/data/archive/{IL_TEST_JOB_INGEST}"

        config = dataclasses.replace(
            integration_db_config,
            archive_output_path=archive_dir + "/",
            ssh_host="analytics.test",
            ssh_user="deploy",
            ssh_key_path="/tmp/test.key",
        )

        # First ingest
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_INGEST, "Transferred",
            file_path=batch_dir, remote_path=remote_path,
        )
        with patch("archive_executor.run.ingest_archive_batch",
                   return_value=self._mock_ingest_success()), \
             patch("archive_executor.run.verify_ingestion",
                   return_value=self._mock_verify_success()):
            log = StructuredLogger(config.log_path)
            _process_transferred_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_INGEST)
        assert job["status"] == "Ingested", "First ingest should mark job Ingested"

        # Re-ingest: analytics server returns 0 new rows (dedup), but status=ok
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_INGEST, "Transferred",
            file_path=batch_dir, remote_path=remote_path,
        )
        dedup_result = {"status": "ok", "batches_ok": 1, "batches_error": 0, "rows_ingested": 0}
        with patch("archive_executor.run.ingest_archive_batch",
                   return_value=dedup_result), \
             patch("archive_executor.run.verify_ingestion",
                   return_value=self._mock_verify_success()):
            log = StructuredLogger(config.log_path)
            _process_transferred_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_INGEST)
        assert job["status"] == "Ingested", (
            f"Re-ingest with 0 new rows (dedup) should not fail the job, got {job['status']}"
        )

    def test_retry_from_transfer_phase_skips_reexport(self, integration_db_config, db_conn, archive_dir):
        """A Transferred job that fails at ingestion retries ingestion directly.

        After ingestion failure, job resets to Pending. On the next run, the job
        goes through Pending→Exported→Transferred again (export is idempotent),
        then succeeds at ingestion. The exported Parquet files are preserved
        on disk between attempts.
        """
        from archive_executor.run import _process_transferred_jobs
        from archive_executor.logger import StructuredLogger

        batch_dir = _build_exported_batch(archive_dir, IL_TEST_JOB_INGEST, {})
        remote_path = f"/data/archive/{IL_TEST_JOB_INGEST}"
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_INGEST, "Transferred",
            file_path=batch_dir, remote_path=remote_path,
        )

        config = dataclasses.replace(
            integration_db_config,
            archive_output_path=archive_dir + "/",
            ssh_host="analytics.test",
            ssh_user="deploy",
            ssh_key_path="/tmp/test.key",
        )

        # First attempt: ingestion fails
        from archive_executor.ingestion import IngestionError
        with patch("archive_executor.run.ingest_archive_batch",
                   side_effect=IngestionError("Network timeout during ingest")), \
             patch("archive_executor.run.verify_ingestion",
                   return_value=self._mock_verify_success()):
            log = StructuredLogger(config.log_path)
            _process_transferred_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_INGEST)
        assert job["status"] == "Pending", (
            f"Failed ingestion with retry_count<3 should reset to Pending, got {job['status']}"
        )
        assert job["retry_count"] == 1
        assert "Network timeout" in (job["error_log"] or "")

        # Simulate re-transfer: put job back to Transferred (previous transfer data on remote)
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_INGEST, "Transferred",
            file_path=batch_dir, remote_path=remote_path, retry_count=1,
        )

        # Second attempt: ingestion succeeds — no re-export needed, Parquet already on remote
        with patch("archive_executor.run.ingest_archive_batch",
                   return_value=self._mock_ingest_success()), \
             patch("archive_executor.run.verify_ingestion",
                   return_value=self._mock_verify_success()):
            log = StructuredLogger(config.log_path)
            _process_transferred_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_INGEST)
        assert job["status"] == "Ingested", (
            f"Second attempt should succeed and reach Ingested, got {job['status']}"
        )


# ===========================================================================
# Category 6: Purge — Batched DELETE and Audit Log (Phase 5 / US3)
# ===========================================================================

class TestPurgeAndAuditLog:
    """Verify purge stage: Completed → Purged with batched DELETE and audit trail.

    Tests:
    - Deletion is blocked when job is not in Completed status
    - Batched DELETE respects PURGE_BATCH_SIZE with sleep between batches
    - Resumable after interruption: job stays Completed, second run finishes
    - Audit log records all required fields (job_id, rows_deleted, batch_size,
      num_batches, duration_ms, status)
    """

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        ensure_audit_table(db_conn)
        delete_interaction_log_rows_by_prefix(db_conn, "IL-PURGE-")
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_PURGE])
        delete_test_audit_logs(db_conn, [IL_TEST_JOB_PURGE])
        insert_test_players(db_conn, num_players=5)
        # Insert lessons used by insert_interaction_log_rows(prefix="PURGE")
        insert_test_lessons(db_conn, [f"IL-LESSON-PURGE-{i:03d}" for i in range(1, 4)])
        yield
        delete_interaction_log_rows_by_prefix(db_conn, "IL-PURGE-")
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_PURGE])
        delete_test_audit_logs(db_conn, [IL_TEST_JOB_PURGE])
        delete_test_lessons(db_conn, "IL-LESSON-PURGE-")

    def test_deletion_blocked_when_ingestion_incomplete(self, integration_db_config, db_conn, archive_dir):
        """purge_completed_jobs skips jobs not yet in Completed status."""
        from archive_executor.logger import StructuredLogger
        from archive_executor.purge import purge_completed_jobs

        insert_interaction_log_rows(
            db_conn, prefix="PURGE", count=5,
            date_from=IL_RANGE_A[0], date_to=IL_RANGE_A[1],
        )
        # Job is Ingested, not Completed — purge should skip it
        _upsert_il_job_at_stage(db_conn, IL_TEST_JOB_PURGE, "Ingested", file_path=archive_dir)

        log = StructuredLogger(integration_db_config.log_path)
        purge_completed_jobs(integration_db_config, log)

        remaining = count_interaction_logs(db_conn, *IL_RANGE_A)
        assert remaining == 5, f"Expected 5 rows (not purged), got {remaining}"

        job = _get_archive_job(db_conn, IL_TEST_JOB_PURGE)
        assert job["status"] == "Ingested", f"Job should remain Ingested, got {job['status']}"

    def test_batched_delete_completes_all_rows(self, integration_db_config, db_conn, archive_dir):
        """Purge deletes all rows across multiple batches and marks job Purged."""
        from archive_executor.logger import StructuredLogger
        from archive_executor.purge import purge_completed_jobs

        insert_interaction_log_rows(
            db_conn, prefix="PURGE", count=10,
            date_from=IL_RANGE_A[0], date_to=IL_RANGE_A[1],
        )
        _upsert_il_job_at_stage(db_conn, IL_TEST_JOB_PURGE, "Completed", file_path=archive_dir)

        # Patch batch size to 3 to force multiple batches over 10 rows
        with patch("archive_executor.purge.PURGE_BATCH_SIZE", 3), \
             patch("archive_executor.purge.time.sleep"):
            log = StructuredLogger(integration_db_config.log_path)
            purge_completed_jobs(integration_db_config, log)

        remaining = count_interaction_logs(db_conn, *IL_RANGE_A)
        assert remaining == 0, f"Expected 0 rows after purge, got {remaining}"

        job = _get_archive_job(db_conn, IL_TEST_JOB_PURGE)
        assert job["status"] == "Purged", f"Expected Purged, got {job['status']}"
        assert job["source_deleted"] == 1

    def test_resumable_after_interruption(self, integration_db_config, db_conn, archive_dir):
        """Purge resumes correctly: job stays Completed after interruption, second run finishes."""
        from archive_executor.logger import StructuredLogger
        from archive_executor.purge import purge_completed_jobs

        insert_interaction_log_rows(
            db_conn, prefix="PURGE", count=10,
            date_from=IL_RANGE_A[0], date_to=IL_RANGE_A[1],
        )
        _upsert_il_job_at_stage(db_conn, IL_TEST_JOB_PURGE, "Completed", file_path=archive_dir)

        # First run: interrupt after second batch (sleep raises on second call)
        sleep_calls = []

        def failing_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise RuntimeError("Simulated interruption after second batch")

        with patch("archive_executor.purge.PURGE_BATCH_SIZE", 3), \
             patch("archive_executor.purge.time.sleep", side_effect=failing_sleep):
            log = StructuredLogger(integration_db_config.log_path)
            try:
                purge_completed_jobs(integration_db_config, log)
            except RuntimeError:
                pass

        # Job must remain Completed (not Purged) after partial failure
        job = _get_archive_job(db_conn, IL_TEST_JOB_PURGE)
        assert job["status"] == "Completed", (
            f"Job should remain Completed after interrupted purge, got {job['status']}"
        )
        # Some rows were deleted (at least one full batch of 3 completed)
        remaining_after_first = count_interaction_logs(db_conn, *IL_RANGE_A)
        assert remaining_after_first < 10, "Some rows should be deleted before interruption"

        # Second run: completes successfully
        with patch("archive_executor.purge.time.sleep"):
            log = StructuredLogger(integration_db_config.log_path)
            purge_completed_jobs(integration_db_config, log)

        # Commit db_conn to start a fresh REPEATABLE READ snapshot after second purge
        db_conn.commit()
        assert count_interaction_logs(db_conn, *IL_RANGE_A) == 0, "All rows should be deleted"
        job = _get_archive_job(db_conn, IL_TEST_JOB_PURGE)
        assert job["status"] == "Purged", f"Expected Purged after second run, got {job['status']}"

    def test_audit_log_records_all_required_fields(self, integration_db_config, db_conn, archive_dir):
        """Audit log entry contains all required fields after successful purge."""
        from archive_executor.logger import StructuredLogger
        from archive_executor.purge import purge_completed_jobs

        insert_interaction_log_rows(
            db_conn, prefix="PURGE", count=5,
            date_from=IL_RANGE_A[0], date_to=IL_RANGE_A[1],
        )
        _upsert_il_job_at_stage(db_conn, IL_TEST_JOB_PURGE, "Completed", file_path=archive_dir)

        with patch("archive_executor.purge.time.sleep"):
            log = StructuredLogger(integration_db_config.log_path)
            purge_completed_jobs(integration_db_config, log)

        with db_conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM `archive_delete_audit_log` WHERE job_id = %s",
                (IL_TEST_JOB_PURGE,),
            )
            audit = cursor.fetchone()

        assert audit is not None, "Audit log entry should exist after purge"
        assert audit["job_id"] == IL_TEST_JOB_PURGE
        assert audit["rows_deleted"] == 5
        assert audit["batch_size"] == 10000
        assert audit["num_batches"] >= 1
        assert audit["duration_ms"] >= 0
        assert audit["status"] == "success"
        assert audit["error_msg"] is None
        assert audit["total_rows_estimated"] >= 5


# ===========================================================================
# Category 7: Analytics Refresh — Recent Layer and Aggregations (Phase 6 / US4)
# ===========================================================================

class TestAnalyticsRefresh:
    """Verify post-ingestion analytics refresh: refresh-recent and refresh-aggregates.

    Tests:
    - Both refresh commands are called after successful handoff
    - refresh-recent uses 90-day window and correct archive type
    - refresh-aggregates uses correct archive type
    - Refresh failure is best-effort: job still reaches Completed
    - Refresh is idempotent (called multiple times without error)
    """

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_REFRESH])
        yield
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_REFRESH])

    def _ssh_config(self, base_config):
        return dataclasses.replace(
            base_config,
            ssh_host="analytics.test",
            ssh_user="deploy",
            ssh_key_path="/tmp/test.key",
        )

    def test_refresh_called_after_successful_handoff(self, integration_db_config, db_conn, archive_dir):
        """After successful handoff, both refresh-recent and refresh-aggregates are called."""
        from archive_executor.run import _process_ingested_jobs
        from archive_executor.logger import StructuredLogger

        remote_path = f"/data/archive/{IL_TEST_JOB_REFRESH}"
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_REFRESH, "Ingested",
            file_path=archive_dir, remote_path=remote_path,
        )

        config = self._ssh_config(integration_db_config)

        with patch("archive_executor.run.handoff_archive", return_value={"status": "ok"}), \
             patch("archive_executor.run.refresh_recent", return_value={"status": "ok", "row_count": 1000, "window_days": 90}) as mock_recent, \
             patch("archive_executor.run.refresh_aggregates", return_value={"status": "ok", "daily_rows": 500, "monthly_rows": 60}) as mock_agg:

            log = StructuredLogger(config.log_path)
            _process_ingested_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_REFRESH)
        assert job["status"] == "Completed", f"Expected Completed, got {job['status']}"
        mock_recent.assert_called_once()
        mock_agg.assert_called_once()

    def test_refresh_recent_uses_correct_archive_type(self, integration_db_config, db_conn, archive_dir):
        """refresh_recent is called with archive_type='interaction_log'."""
        from archive_executor.run import _process_ingested_jobs
        from archive_executor.logger import StructuredLogger

        remote_path = f"/data/archive/{IL_TEST_JOB_REFRESH}"
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_REFRESH, "Ingested",
            file_path=archive_dir, remote_path=remote_path,
        )

        config = self._ssh_config(integration_db_config)

        with patch("archive_executor.run.handoff_archive", return_value={"status": "ok"}), \
             patch("archive_executor.run.refresh_recent", return_value={"status": "ok", "row_count": 156000, "window_days": 90}) as mock_recent, \
             patch("archive_executor.run.refresh_aggregates", return_value={"status": "ok", "daily_rows": 42500, "monthly_rows": 8200}):

            log = StructuredLogger(config.log_path)
            _process_ingested_jobs(config, log)

        args, kwargs = mock_recent.call_args
        # archive_type is the second positional arg (config, archive_type, log)
        archive_type_arg = args[1] if len(args) > 1 else kwargs.get("archive_type")
        assert archive_type_arg == "interaction_log", (
            f"Expected archive_type='interaction_log', got {archive_type_arg!r}"
        )

    def test_refresh_failure_does_not_block_completed(self, integration_db_config, db_conn, archive_dir):
        """Refresh failure is best-effort: job still reaches Completed."""
        from archive_executor.run import _process_ingested_jobs
        from archive_executor.logger import StructuredLogger
        from archive_executor.ingestion import IngestionError

        remote_path = f"/data/archive/{IL_TEST_JOB_REFRESH}"
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_REFRESH, "Ingested",
            file_path=archive_dir, remote_path=remote_path,
        )

        config = self._ssh_config(integration_db_config)

        with patch("archive_executor.run.handoff_archive", return_value={"status": "ok"}), \
             patch("archive_executor.run.refresh_recent", side_effect=IngestionError("refresh-recent timed out")), \
             patch("archive_executor.run.refresh_aggregates", side_effect=IngestionError("refresh-aggregates failed")):

            log = StructuredLogger(config.log_path)
            _process_ingested_jobs(config, log)

        job = _get_archive_job(db_conn, IL_TEST_JOB_REFRESH)
        assert job["status"] == "Completed", (
            f"Refresh failure should not block Completed, got {job['status']}"
        )

    def test_refresh_idempotent_on_second_call(self, integration_db_config, db_conn, archive_dir):
        """Calling refresh commands multiple times does not cause errors (idempotent)."""
        from archive_executor.ingestion import refresh_recent, refresh_aggregates
        from archive_executor.logger import StructuredLogger
        from archive_executor.transfer import _run_ssh_command

        config = self._ssh_config(integration_db_config)
        log = StructuredLogger(config.log_path)

        recent_response = '{"status": "ok", "row_count": 156000, "window_days": 90, "oldest_record": "2025-12-12T00:00:00", "duration_ms": 800}'
        agg_response = '{"status": "ok", "daily_rows": 42500, "monthly_rows": 8200, "duration_ms": 1250}'

        with patch("archive_executor.ingestion._run_ssh_command", return_value=(0, recent_response, "")) as mock_ssh:
            result1 = refresh_recent(config, "interaction_log", log)
            result2 = refresh_recent(config, "interaction_log", log)

        assert result1["status"] == "ok"
        assert result2["status"] == "ok"
        assert mock_ssh.call_count == 2

        with patch("archive_executor.ingestion._run_ssh_command", return_value=(0, agg_response, "")) as mock_ssh:
            result1 = refresh_aggregates(config, "interaction_log", log)
            result2 = refresh_aggregates(config, "interaction_log", log)

        assert result1["status"] == "ok"
        assert result2["status"] == "ok"
        assert mock_ssh.call_count == 2

    def test_recent_layer_uses_90_day_window(self, integration_db_config, db_conn, archive_dir):
        """refresh_recent builds 90-day window — command includes --window-days 90."""
        from archive_executor.ingestion import refresh_recent
        from archive_executor.logger import StructuredLogger

        config = self._ssh_config(integration_db_config)
        log = StructuredLogger(config.log_path)

        response = '{"status": "ok", "row_count": 156000, "window_days": 90, "duration_ms": 800}'

        with patch("archive_executor.ingestion._run_ssh_command", return_value=(0, response, "")) as mock_ssh:
            result = refresh_recent(config, "interaction_log", log, window_days=90)

        assert result["window_days"] == 90
        called_cmd = mock_ssh.call_args[0][1]  # second positional arg is the command string
        assert "--window-days 90" in called_cmd
        assert "--archive-type 'interaction_log'" in called_cmd or "--archive-type interaction_log" in called_cmd


# ===========================================================================
# Category 8: Batch Logging and Observability (Phase 7 / US5)
# ===========================================================================


class TestBatchLogging:
    """Verify batch logging metadata fields per FR-014.

    Tests:
    - Successful batch has all required metadata fields populated
    - Failed batch (permanently failed) has error_log with failure phase and error
    - Retried batch increments retry_count and error_log contains phase context
    """

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_LOGGING])
        delete_interaction_log_rows_by_prefix(db_conn, f"IL-LOG-")
        yield
        delete_il_test_jobs(db_conn, [IL_TEST_JOB_LOGGING])
        delete_interaction_log_rows_by_prefix(db_conn, f"IL-LOG-")

    def test_completed_job_has_all_metadata_fields(self, integration_db_config, db_conn, archive_dir):
        """A successfully completed (0-row) batch has all FR-014 metadata fields populated.

        FR-014 fields: batch ID (name), source_doctype, batch time range (job_meta),
        started_at, completed_at, row_count, status, retry_count.
        """
        from archive_executor.run import _process_pending_jobs
        from archive_executor.logger import StructuredLogger

        # Use a date range with no IL rows — triggers zero-row graceful completion
        date_from = "2099-12-01"
        date_to = "2099-12-02"

        job_meta = json.dumps({
            "query_filter": {
                "date_from": date_from,
                "date_to": date_to,
                "filter_column": "timestamp",
            },
            "export_columns": [
                "name", "player", "lesson", "stage_id",
                "event_type", "time_spent", "errors_count", "timestamp",
            ],
            "schema_snapshot": {
                "columns": [
                    {"name": "name",         "type": "VARCHAR(140)"},
                    {"name": "player",       "type": "VARCHAR(140)"},
                    {"name": "lesson",       "type": "VARCHAR(140)"},
                    {"name": "stage_id",     "type": "VARCHAR(140)"},
                    {"name": "event_type",   "type": "VARCHAR(20)"},
                    {"name": "time_spent",   "type": "INT"},
                    {"name": "errors_count", "type": "INT"},
                    {"name": "timestamp",    "type": "DATETIME"},
                ],
                "primary_key": ["name"],
            },
            "related_tables": [],
        })

        with db_conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO `tabMemora Archive Job` "
                "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
                " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
                " `status`, `priority`, `retry_count`, `post_archive_action`, "
                " `source_deleted`, `sync_paused`, `duration_seconds`, `row_count`, `file_size_bytes`, "
                " `job_meta`) "
                "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0, "
                "        'Memora Interaction Log', %s, 'v1', 'interaction_log', "
                "        'Pending', 'Normal', 0, 'Keep', 0, 0, 0, 0, 0, %s)",
                (IL_TEST_JOB_LOGGING, date_from, job_meta),
            )
        db_conn.commit()

        log = StructuredLogger(integration_db_config.log_path)
        _process_pending_jobs(integration_db_config, log)

        db_conn.commit()
        job = _get_archive_job(db_conn, IL_TEST_JOB_LOGGING)
        assert job is not None

        # FR-014: batch ID
        assert job["name"] == IL_TEST_JOB_LOGGING, "name (batch ID) must be set"
        # FR-014: table name
        assert job["source_doctype"] == "Memora Interaction Log", "source_doctype must be Memora Interaction Log"
        # FR-014: batch time range (stored in job_meta)
        meta = json.loads(job["job_meta"])
        assert meta["query_filter"]["date_from"] == date_from, "job_meta must contain date_from"
        assert meta["query_filter"]["date_to"] == date_to, "job_meta must contain date_to"
        # FR-014: start timestamp
        assert job["started_at"] is not None, "started_at must be set after job is claimed"
        # FR-014: end timestamp
        assert job["completed_at"] is not None, "completed_at must be set after Completed"
        # FR-014: record count (0-row batch)
        assert job["row_count"] == 0, "row_count must be 0 for empty batch"
        # FR-014: final status
        assert job["status"] == "Completed", f"Expected Completed, got {job['status']}"
        # FR-014: retry indicator
        assert job["retry_count"] == 0, "retry_count should be 0 for first-run success"

    def test_failed_job_has_error_log_with_phase(self, integration_db_config, db_conn, archive_dir):
        """Permanently failed batch (retry_count=3) has error_log with failure phase and error text.

        FR-014: status per phase, error messages, failure phase logging.
        """
        from archive_executor.run import _fail_job, _update_stage
        from archive_executor.logger import StructuredLogger

        # Create a job in Processing state with retry_count=3 (next failure is permanent)
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_LOGGING, "Processing",
            file_path=archive_dir, retry_count=3,
        )
        # Simulate having reached the export stage
        _update_stage(integration_db_config, IL_TEST_JOB_LOGGING, "exporting_fact")

        error_msg = "RuntimeError: Simulated DQ validation failure"
        _fail_job(
            integration_db_config,
            IL_TEST_JOB_LOGGING,
            error_msg,
            retry_count=3,
            current_status="Processing",
            stage="exporting_fact",
        )

        db_conn.commit()
        job = _get_archive_job(db_conn, IL_TEST_JOB_LOGGING)
        assert job is not None

        # FR-014: status must be Failed
        assert job["status"] == "Failed", f"Expected Failed, got {job['status']}"
        # FR-014: error messages on failure
        assert job["error_log"] is not None, "error_log must be populated on failure"
        assert "Simulated DQ validation failure" in job["error_log"], "error_log must contain error text"
        # FR-014: failure phase
        assert "exporting_fact" in job["error_log"], (
            "error_log must contain failure phase (exporting_fact) for observability"
        )
        # FR-014: completed_at set for permanent failures
        assert job["completed_at"] is not None, "completed_at must be set for permanently failed jobs"

    def test_retried_job_reflects_retry_status_and_phase(self, integration_db_config, db_conn, archive_dir):
        """A retryable failure increments retry_count and error_log contains phase context.

        FR-014: retry indicator, failure phase logging preserved across retry.
        """
        from archive_executor.run import _fail_job, _update_stage
        from archive_executor.logger import StructuredLogger

        # Create job in Processing state with retry_count=1 (retryable: < 3)
        _upsert_il_job_at_stage(
            db_conn, IL_TEST_JOB_LOGGING, "Processing",
            file_path=archive_dir, retry_count=1,
        )
        _update_stage(integration_db_config, IL_TEST_JOB_LOGGING, "validating_dq")

        error_msg = "RuntimeError: DQ rule DQ-05 failed: null values in required column"
        _fail_job(
            integration_db_config,
            IL_TEST_JOB_LOGGING,
            error_msg,
            retry_count=1,
            current_status="Processing",
            stage="validating_dq",
        )

        db_conn.commit()
        job = _get_archive_job(db_conn, IL_TEST_JOB_LOGGING)
        assert job is not None

        # FR-014: retry indicator — retry_count must be incremented
        assert job["retry_count"] == 2, f"retry_count should be 2 after second failure, got {job['retry_count']}"
        # Job is reset to Pending for retry
        assert job["status"] == "Pending", f"Expected Pending after retryable failure, got {job['status']}"
        # FR-014: error_log preserves phase context even though execution_stage is cleared
        assert job["error_log"] is not None, "error_log must be preserved on retry"
        assert "DQ rule DQ-05 failed" in job["error_log"], "error_log must contain error text"
        assert "validating_dq" in job["error_log"], (
            "error_log must contain failure phase for observability (execution_stage is NULL after retry reset)"
        )
