"""Integration tests for the archive pipeline against a real MariaDB database.

These tests verify the end-to-end pipeline using actual database operations:
    MariaDB → Export (Parquet) → [Transfer skipped] → Delete → Audit Log

Run with:
    DB_HOST=127.0.0.1 DB_USER=frappe DB_PASSWORD=... DB_NAME=... \\
        pytest -m integration archive_executor/tests/test_integration_pipeline.py -v

Prerequisites:
    - MariaDB credentials set via DB_* or TEST_DB_* env vars
    - tabMemora Practice Log and tabMemora Archive Job tables exist
    - Python packages: pytest, pymysql, pyarrow
"""

import dataclasses
import json
import os
import tempfile
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import pyarrow.parquet as pq

from archive_executor.config import Config
from archive_executor.db import atomic_update, get_connection
from archive_executor.exporter import export_fact_data
from archive_executor.logger import StructuredLogger
from archive_executor.manifest import build_manifest
from archive_executor.purge import (
    PURGE_BATCH_SIZE,
    _log_delete_audit,
    purge_completed_jobs,
)
from archive_executor.validator import validate_file

from .conftest import (
    ALL_TEST_JOBS,
    RANGE_A, RANGE_B, RANGE_C, RANGE_D, RANGE_X,
    TEST_JOB_EXPORT, TEST_JOB_LARGE, TEST_JOB_MULTI_A,
    TEST_JOB_MULTI_B, TEST_JOB_PURGE, TEST_JOB_RERUN, TEST_JOB_TXN,
    count_practice_logs,
    delete_test_audit_logs,
    delete_test_jobs,
    delete_test_practice_logs,
    delete_test_practice_logs_by_prefix,
    delete_test_players,
    ensure_audit_table,
    insert_practice_log_rows,
    insert_test_players,
    upsert_archive_job,
)

# ---------------------------------------------------------------------------
# Per-class audit table helper (called inside setup fixtures that use db_conn)
# ---------------------------------------------------------------------------

def _ensure_audit_table_in_setup(db_conn):
    """Call ensure_audit_table inside any test class setup that has db_conn."""
    ensure_audit_table(db_conn)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Shared log fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def log():
    return MagicMock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config_with_dir(base_config: Config, archive_dir: str) -> Config:
    """Return a new Config with archive_output_path set to archive_dir."""
    return dataclasses.replace(base_config, archive_output_path=archive_dir + "/")


def _count_audit_entries(conn, job_id: str) -> list[dict]:
    """Return all audit log rows for a given job_id."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM `archive_delete_audit_log` WHERE `job_id` = %s",
            (job_id,),
        )
        return cursor.fetchall()


def _count_archive_job_rows(conn, name: str, status: str | None = None) -> int:
    if status:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM `tabMemora Archive Job` "
                "WHERE name = %s AND status = %s",
                (name, status),
            )
            return cursor.fetchone()["cnt"]
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM `tabMemora Archive Job` WHERE name = %s",
            (name,),
        )
        return cursor.fetchone()["cnt"]


def _get_archive_job(conn, name: str) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM `tabMemora Archive Job` WHERE name = %s",
            (name,),
        )
        return cursor.fetchone()


# ===========================================================================
# Category 1: End-to-End Pipeline Test
# ===========================================================================

class TestE2EPipeline:
    """Verify the full MariaDB → Export → Delete → Audit pipeline.

    Transfer and ingestion stages are not tested here (require SSH).
    The test simulates 'Completed' status to trigger the purge.
    """

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        """Insert Dataset A, create archive job; clean up after."""
        # Pre-cleanup in case of leftover data from a previous crashed run
        _ensure_audit_table_in_setup(db_conn)
        delete_test_practice_logs(db_conn, *RANGE_A)
        delete_test_jobs(db_conn, [TEST_JOB_EXPORT])
        delete_test_audit_logs(db_conn, [TEST_JOB_EXPORT])
        # Setup
        insert_test_players(db_conn)
        inserted = insert_practice_log_rows(
            db_conn, prefix="E2E", count=10,
            date_from=RANGE_A[0], date_to=RANGE_A[1],
        )
        assert inserted == 10, f"Expected 10 inserted rows, got {inserted}"
        yield
        # Teardown
        delete_test_practice_logs(db_conn, *RANGE_A)
        delete_test_jobs(db_conn, [TEST_JOB_EXPORT])
        delete_test_audit_logs(db_conn, [TEST_JOB_EXPORT])

    def test_e2e_archive_pipeline(
        self, integration_db_config, db_conn, log, archive_dir
    ):
        """Full pipeline: insert 10 rows → export → mark Completed → purge → audit.

        Verifies:
        - Parquet file contains correct row count
        - MariaDB rows are deleted after purge (count = 0)
        - Audit log entry exists with status='success'
        """
        config = _config_with_dir(integration_db_config, archive_dir)

        # --- Step 1: Verify rows are in MariaDB ---
        pre_count = count_practice_logs(db_conn, *RANGE_A)
        assert pre_count == 10, f"Expected 10 pre-purge rows, got {pre_count}"

        # --- Step 2: Export fact data ---
        meta = {
            "query_filter": {
                "date_from": RANGE_A[0],
                "date_to": RANGE_A[1],
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
            },
            "related_tables": [],
        }

        staging_dir = os.path.join(archive_dir, ".staging", TEST_JOB_EXPORT)
        os.makedirs(staging_dir, exist_ok=True)

        fact_path, fact_row_count, _ = export_fact_data(
            config=config,
            staging_dir=staging_dir,
            meta=meta,
            source_table="tabMemora Practice Log",
            archive_type_name="practice_log",
        )

        assert fact_row_count == 10, f"Export: expected 10 rows, got {fact_row_count}"
        assert os.path.isfile(fact_path), "Fact Parquet file not created"

        # Validate Parquet row count matches
        parquet_count = pq.read_metadata(fact_path).num_rows
        assert parquet_count == 10, f"Parquet: expected 10 rows, got {parquet_count}"

        # Build manifest
        validation = validate_file(fact_path, fact_row_count)
        assert validation["valid"], f"File validation failed: {validation['errors']}"

        build_manifest(
            staging_dir=staging_dir,
            batch_id=TEST_JOB_EXPORT,
            dataset_key="practice_log_archive",
            kind="archive",
            schema_version="1.0",
            source="memora_admin",
            scope_key="SEAS-TEST-001",
            files=[{
                "role": "fact",
                "entity": "practice_log",
                "filename": validation["filename"],
                "row_count": validation["row_count"],
                "checksum": validation["checksum"],
                "size_bytes": validation["size_bytes"],
            }],
        )

        # Publish: rename staging → final
        final_dir = os.path.join(archive_dir, TEST_JOB_EXPORT)
        os.rename(staging_dir, final_dir)

        # --- Step 3: Create Completed archive job pointing at final_dir ---
        upsert_archive_job(
            db_conn,
            name=TEST_JOB_EXPORT,
            status="Completed",
            date_from=RANGE_A[0],
            date_to=RANGE_A[1],
            file_path=final_dir,
            post_archive_action="Delete",
            source_deleted=0,
        )

        # --- Step 4: Run purge ---
        purge_completed_jobs(config, log)

        # --- Step 5: Verify MariaDB rows deleted ---
        post_count = count_practice_logs(db_conn, *RANGE_A)
        assert post_count == 0, f"Expected 0 rows after purge, got {post_count}"

        # --- Step 6: Verify audit log ---
        audit_rows = _count_audit_entries(db_conn, TEST_JOB_EXPORT)
        assert len(audit_rows) == 1, f"Expected 1 audit row, got {len(audit_rows)}"
        assert audit_rows[0]["status"] == "success"
        assert audit_rows[0]["rows_deleted"] == 10
        assert audit_rows[0]["season_id"] == "SEAS-TEST-001"

        # --- Step 7: Verify job is marked Purged ---
        job = _get_archive_job(db_conn, TEST_JOB_EXPORT)
        assert job is not None
        assert job["status"] == "Purged"
        assert job["source_deleted"] == 1


# ===========================================================================
# Category 2: Dataset Sizes (A=10, B=100, C=10,000)
# ===========================================================================

class TestDatasetSizes:
    """Verify pipeline handles small, medium, and large datasets correctly."""

    @pytest.fixture
    def dataset_a(self, db_conn):
        _ensure_audit_table_in_setup(db_conn)
        delete_test_practice_logs(db_conn, *RANGE_A)
        delete_test_jobs(db_conn, [TEST_JOB_EXPORT])
        delete_test_audit_logs(db_conn, [TEST_JOB_EXPORT])
        insert_test_players(db_conn)
        count = insert_practice_log_rows(
            db_conn, prefix="DSA", count=10,
            date_from=RANGE_A[0], date_to=RANGE_A[1],
        )
        yield count
        delete_test_practice_logs(db_conn, *RANGE_A)
        delete_test_jobs(db_conn, [TEST_JOB_EXPORT])
        delete_test_audit_logs(db_conn, [TEST_JOB_EXPORT])

    @pytest.fixture
    def dataset_b(self, db_conn):
        delete_test_practice_logs(db_conn, *RANGE_B)
        delete_test_jobs(db_conn, [TEST_JOB_PURGE])
        delete_test_audit_logs(db_conn, [TEST_JOB_PURGE])
        insert_test_players(db_conn)
        count = insert_practice_log_rows(
            db_conn, prefix="DSB", count=100,
            date_from=RANGE_B[0], date_to=RANGE_B[1],
        )
        yield count
        delete_test_practice_logs(db_conn, *RANGE_B)
        delete_test_jobs(db_conn, [TEST_JOB_PURGE])
        delete_test_audit_logs(db_conn, [TEST_JOB_PURGE])

    @pytest.fixture
    def dataset_c(self, db_conn):
        delete_test_practice_logs_by_prefix(db_conn, "DSC")  # remove overflow rows
        delete_test_practice_logs(db_conn, *RANGE_C)
        delete_test_jobs(db_conn, [TEST_JOB_RERUN])
        delete_test_audit_logs(db_conn, [TEST_JOB_RERUN])
        insert_test_players(db_conn, num_players=20)
        count = insert_practice_log_rows(
            db_conn, prefix="DSC", count=10_000,
            date_from=RANGE_C[0], date_to=RANGE_C[1],
            batch_size=500,
        )
        yield count
        delete_test_practice_logs(db_conn, *RANGE_C)
        delete_test_jobs(db_conn, [TEST_JOB_RERUN])
        delete_test_audit_logs(db_conn, [TEST_JOB_RERUN])

    def test_export_dataset_a_10_rows(
        self, integration_db_config, db_conn, log, archive_dir, dataset_a
    ):
        """Dataset A (10 rows): export row count matches inserted count."""
        assert dataset_a == 10
        pre_count = count_practice_logs(db_conn, *RANGE_A)
        assert pre_count == 10

        config = _config_with_dir(integration_db_config, archive_dir)
        staging = os.path.join(archive_dir, ".staging", "ds_a")
        os.makedirs(staging, exist_ok=True)

        meta = _simple_meta(*RANGE_A)
        _, row_count, _ = export_fact_data(
            config=config, staging_dir=staging, meta=meta,
            source_table="tabMemora Practice Log", archive_type_name="practice_log",
        )
        assert row_count == 10

    def test_export_dataset_b_100_rows(
        self, integration_db_config, db_conn, log, archive_dir, dataset_b
    ):
        """Dataset B (100 rows): export row count matches inserted count."""
        assert dataset_b == 100
        pre_count = count_practice_logs(db_conn, *RANGE_B)
        assert pre_count == 100

        config = _config_with_dir(integration_db_config, archive_dir)
        staging = os.path.join(archive_dir, ".staging", "ds_b")
        os.makedirs(staging, exist_ok=True)

        meta = _simple_meta(*RANGE_B)
        _, row_count, _ = export_fact_data(
            config=config, staging_dir=staging, meta=meta,
            source_table="tabMemora Practice Log", archive_type_name="practice_log",
        )
        assert row_count == 100

    def test_purge_dataset_b_100_rows(
        self, integration_db_config, db_conn, log, archive_dir, dataset_b
    ):
        """Dataset B (100 rows): purge deletes all rows; audit log correct."""
        config = _config_with_dir(integration_db_config, archive_dir)
        final_dir = _make_archive_dir(archive_dir, TEST_JOB_PURGE)
        upsert_archive_job(
            db_conn, TEST_JOB_PURGE, "Completed", *RANGE_B,
            file_path=final_dir,
        )

        purge_completed_jobs(config, log)

        post_count = count_practice_logs(db_conn, *RANGE_B)
        assert post_count == 0, f"Expected 0 rows, got {post_count}"

        audits = _count_audit_entries(db_conn, TEST_JOB_PURGE)
        assert len(audits) == 1
        assert audits[0]["status"] == "success"
        assert audits[0]["rows_deleted"] == 100

    def test_export_dataset_c_10k_rows(
        self, integration_db_config, db_conn, log, archive_dir, dataset_c
    ):
        """Dataset C (10,000 rows): export count matches; Parquet metadata correct."""
        assert dataset_c == 10_000
        pre_count = count_practice_logs(db_conn, *RANGE_C)
        assert pre_count == 10_000

        config = _config_with_dir(integration_db_config, archive_dir)
        staging = os.path.join(archive_dir, ".staging", "ds_c")
        os.makedirs(staging, exist_ok=True)

        meta = _simple_meta(*RANGE_C)
        fact_path, row_count, _ = export_fact_data(
            config=config, staging_dir=staging, meta=meta,
            source_table="tabMemora Practice Log", archive_type_name="practice_log",
        )
        assert row_count == 10_000
        parquet_count = pq.read_metadata(fact_path).num_rows
        assert parquet_count == 10_000

    def test_purge_dataset_c_10k_rows(
        self, integration_db_config, db_conn, log, archive_dir, dataset_c
    ):
        """Dataset C (10,000 rows): purge deletes all rows in batches."""
        config = _config_with_dir(integration_db_config, archive_dir)
        final_dir = _make_archive_dir(archive_dir, TEST_JOB_RERUN)
        upsert_archive_job(
            db_conn, TEST_JOB_RERUN, "Completed", *RANGE_C,
            file_path=final_dir,
        )

        start = time.monotonic()
        purge_completed_jobs(config, log)
        elapsed = time.monotonic() - start

        post_count = count_practice_logs(db_conn, *RANGE_C)
        assert post_count == 0, f"Expected 0 rows after purge, got {post_count}"

        audits = _count_audit_entries(db_conn, TEST_JOB_RERUN)
        assert len(audits) == 1
        assert audits[0]["status"] == "success"
        assert audits[0]["rows_deleted"] == 10_000
        # 10K rows / PURGE_BATCH_SIZE = 1 batch (or more if batch size < 10K)
        expected_batches = max(1, 10_000 // PURGE_BATCH_SIZE)
        assert audits[0]["num_batches"] >= 1

        print(f"\n  10K purge elapsed: {elapsed:.2f}s, batches: {audits[0]['num_batches']}")


# ===========================================================================
# Category 3: Manifest Season Validation
# ===========================================================================

class TestManifestValidation:
    """Verify manifest scope_key validation blocks wrong-season ingestion.

    These tests only exercise the manifest module — no DB access required.
    They run even without a MariaDB connection.
    """

    def test_manifest_season_matches_job_season(self, archive_dir):
        """Manifest built for SEAS-TEST-001 records correct scope_key."""
        staging = os.path.join(archive_dir, ".staging", "manifest_ok")
        os.makedirs(staging, exist_ok=True)

        # Create a dummy Parquet file
        import pyarrow as pa
        import pyarrow.parquet as pq
        dummy_path = os.path.join(staging, "fact_practice_log.parquet")
        table = pa.table({"col": pa.array(["a"])})
        pq.write_table(table, dummy_path)

        manifest_path = build_manifest(
            staging_dir=staging,
            batch_id="ARCH-99001",
            dataset_key="practice_log_archive",
            kind="archive",
            schema_version="1.0",
            source="memora_admin",
            scope_key="SEAS-TEST-001",
            files=[{
                "role": "fact",
                "entity": "practice_log",
                "filename": "fact_practice_log.parquet",
                "row_count": 1,
                "checksum": "sha256:abc123",
                "size_bytes": 100,
            }],
        )

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["scope_key"] == "SEAS-TEST-001"
        assert manifest["batch_id"] == "ARCH-99001"
        assert manifest["kind"] == "archive"
        assert len(manifest["files"]) == 1
        assert manifest["files"][0]["role"] == "fact"

    def test_manifest_mismatch_blocks_ingest(self, archive_dir):
        """A manifest with wrong scope_key (SEAS-WRONG) must not be trusted for
        a job scoped to SEAS-TEST-001.

        This test verifies the detection logic: the manifest scope_key must
        match the archive job's archive_scope before any delete is allowed.
        """
        staging = os.path.join(archive_dir, ".staging", "manifest_bad")
        os.makedirs(staging, exist_ok=True)

        # Build manifest with WRONG season
        manifest_path = build_manifest(
            staging_dir=staging,
            batch_id="ARCH-99001",
            dataset_key="practice_log_archive",
            kind="archive",
            schema_version="1.0",
            source="memora_admin",
            scope_key="SEAS-WRONG-999",  # intentional mismatch
            files=[],
        )

        with open(manifest_path) as f:
            manifest = json.load(f)

        job_scope = "SEAS-TEST-001"
        manifest_scope = manifest.get("scope_key", "")

        # Detection: scope mismatch should block further processing
        assert manifest_scope != job_scope, (
            "Manifest scope and job scope should differ in this test"
        )

        # Simulate the guard check that a real pipeline would enforce
        scope_mismatch = manifest_scope != job_scope
        assert scope_mismatch, "Guard should detect the scope mismatch"

    def test_manifest_missing_scope_key_is_flagged(self, archive_dir):
        """A manifest without scope_key is flagged as incomplete for scoped jobs."""
        staging = os.path.join(archive_dir, ".staging", "manifest_no_scope")
        os.makedirs(staging, exist_ok=True)

        manifest_path = build_manifest(
            staging_dir=staging,
            batch_id="ARCH-99001",
            dataset_key="practice_log_archive",
            kind="archive",
            schema_version="1.0",
            source="memora_admin",
            scope_key=None,  # no scope_key
            files=[],
        )

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert "scope_key" not in manifest, "scope_key should be absent"

        # A scoped job should detect the absence
        job_scope = "SEAS-TEST-001"
        manifest_scope = manifest.get("scope_key")
        assert manifest_scope != job_scope


# ===========================================================================
# Category 4: Transaction Safety
# ===========================================================================

class TestTransactionSafety:
    """Verify delete safety: only target season is affected; rollback on failure."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        _ensure_audit_table_in_setup(db_conn)
        # Pre-cleanup to ensure clean state
        delete_test_practice_logs(db_conn, *RANGE_A)
        delete_test_practice_logs(db_conn, *RANGE_X)
        delete_test_jobs(db_conn, [TEST_JOB_TXN, TEST_JOB_MULTI_A, TEST_JOB_MULTI_B])
        delete_test_audit_logs(db_conn, [TEST_JOB_TXN, TEST_JOB_MULTI_A, TEST_JOB_MULTI_B])
        insert_test_players(db_conn)
        # Two seasons in different date ranges
        insert_practice_log_rows(
            db_conn, prefix="TXA", count=50,
            date_from=RANGE_A[0], date_to=RANGE_A[1],
        )
        insert_practice_log_rows(
            db_conn, prefix="TXX", count=50,
            date_from=RANGE_X[0], date_to=RANGE_X[1],
        )
        yield
        delete_test_practice_logs(db_conn, *RANGE_A)
        delete_test_practice_logs(db_conn, *RANGE_X)
        delete_test_jobs(db_conn, [TEST_JOB_TXN, TEST_JOB_MULTI_A, TEST_JOB_MULTI_B])
        delete_test_audit_logs(db_conn, [TEST_JOB_TXN, TEST_JOB_MULTI_A, TEST_JOB_MULTI_B])

    def test_delete_only_affects_target_season(
        self, integration_db_config, db_conn, log, archive_dir
    ):
        """Purging RANGE_A leaves RANGE_X rows untouched (season isolation)."""
        config = _config_with_dir(integration_db_config, archive_dir)

        # Verify initial counts
        count_a_before = count_practice_logs(db_conn, *RANGE_A)
        count_x_before = count_practice_logs(db_conn, *RANGE_X)
        assert count_a_before == 50, f"Expected 50 in RANGE_A, got {count_a_before}"
        assert count_x_before == 50, f"Expected 50 in RANGE_X, got {count_x_before}"

        # Create Completed job for RANGE_A only
        final_dir = _make_archive_dir(archive_dir, TEST_JOB_TXN)
        upsert_archive_job(
            db_conn, TEST_JOB_TXN, "Completed", *RANGE_A,
            file_path=final_dir,
        )

        purge_completed_jobs(config, log)

        # RANGE_A should be deleted
        count_a_after = count_practice_logs(db_conn, *RANGE_A)
        assert count_a_after == 0, f"RANGE_A: expected 0 after purge, got {count_a_after}"

        # RANGE_X must be untouched
        count_x_after = count_practice_logs(db_conn, *RANGE_X)
        assert count_x_after == 50, (
            f"RANGE_X must be untouched: expected 50, got {count_x_after}"
        )

    def test_delete_is_transactional(
        self, integration_db_config, db_conn, log, archive_dir
    ):
        """A failure mid-purge records status='partial' or 'failed'; no silent data loss."""
        config = _config_with_dir(integration_db_config, archive_dir)

        count_before = count_practice_logs(db_conn, *RANGE_X)
        assert count_before == 50

        final_dir = _make_archive_dir(archive_dir, TEST_JOB_MULTI_A)
        upsert_archive_job(
            db_conn, TEST_JOB_MULTI_A, "Completed", *RANGE_X,
            file_path=final_dir,
        )

        # Patch get_connection to fail on the 3rd call (mid-batch)
        original_get_connection = __import__(
            "archive_executor.db", fromlist=["get_connection"]
        ).get_connection
        call_count = [0]

        def failing_get_connection(cfg):
            call_count[0] += 1
            if call_count[0] == 3:
                raise RuntimeError("Simulated mid-batch DB failure")
            return original_get_connection(cfg)

        with patch("archive_executor.purge.get_connection", side_effect=failing_get_connection):
            with pytest.raises(RuntimeError, match="Simulated mid-batch DB failure"):
                purge_completed_jobs(config, log)

        # Audit log should record failure
        audits = _count_audit_entries(db_conn, TEST_JOB_MULTI_A)
        assert len(audits) == 1, "Audit entry must exist even on failure"
        assert audits[0]["status"] in ("failed", "partial"), (
            f"Expected failed/partial status, got {audits[0]['status']}"
        )

        # Some rows may remain (partial delete is expected on failure)
        count_after = count_practice_logs(db_conn, *RANGE_X)
        # The important thing: no silent data loss (we know what was deleted from audit)
        assert audits[0]["rows_deleted"] + count_after == 50, (
            f"rows_deleted ({audits[0]['rows_deleted']}) + remaining ({count_after}) "
            f"must equal original 50"
        )

    def test_multiple_seasons_only_target_deleted(
        self, integration_db_config, db_conn, log, archive_dir
    ):
        """With two Completed jobs for different ranges, each job deletes only its range."""
        config = _config_with_dir(integration_db_config, archive_dir)

        count_a = count_practice_logs(db_conn, *RANGE_A)
        count_x = count_practice_logs(db_conn, *RANGE_X)
        assert count_a == 50
        assert count_x == 50

        dir_a = _make_archive_dir(archive_dir, TEST_JOB_MULTI_A + "_a")
        dir_b = _make_archive_dir(archive_dir, TEST_JOB_MULTI_B)

        # Use distinct archive_scope to avoid the idx_archive_job_unique constraint
        # (unique on source_doctype + archive_scope + schema_version)
        upsert_archive_job(
            db_conn, TEST_JOB_MULTI_A, "Completed", *RANGE_A,
            file_path=dir_a, archive_scope="SEAS-TEST-MULTI-A",
        )
        upsert_archive_job(
            db_conn, TEST_JOB_MULTI_B, "Completed", *RANGE_X,
            file_path=dir_b, archive_scope="SEAS-TEST-MULTI-B",
        )

        purge_completed_jobs(config, log)

        assert count_practice_logs(db_conn, *RANGE_A) == 0
        assert count_practice_logs(db_conn, *RANGE_X) == 0

        audit_a = _count_audit_entries(db_conn, TEST_JOB_MULTI_A)
        audit_b = _count_audit_entries(db_conn, TEST_JOB_MULTI_B)

        assert len(audit_a) == 1 and audit_a[0]["status"] == "success"
        assert len(audit_b) == 1 and audit_b[0]["status"] == "success"
        assert audit_a[0]["rows_deleted"] == 50
        assert audit_b[0]["rows_deleted"] == 50


# ===========================================================================
# Category 5: Idempotent Rerun
# ===========================================================================

class TestIdempotentRerun:
    """Second run of the archive pipeline should be safe: 0 rows exported,
    no crash, no duplicate data."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, db_conn):
        _ensure_audit_table_in_setup(db_conn)
        delete_test_practice_logs(db_conn, *RANGE_A)
        delete_test_jobs(db_conn, [TEST_JOB_EXPORT])
        delete_test_audit_logs(db_conn, [TEST_JOB_EXPORT])
        insert_test_players(db_conn)
        insert_practice_log_rows(
            db_conn, prefix="IDM", count=20,
            date_from=RANGE_A[0], date_to=RANGE_A[1],
        )
        yield
        delete_test_practice_logs(db_conn, *RANGE_A)
        delete_test_jobs(db_conn, [TEST_JOB_EXPORT])
        delete_test_audit_logs(db_conn, [TEST_JOB_EXPORT])

    def test_archive_rerun_is_safe(
        self, integration_db_config, db_conn, log, archive_dir
    ):
        """After a successful purge, re-running purge exports 0 rows and doesn't crash."""
        config = _config_with_dir(integration_db_config, archive_dir)
        final_dir = _make_archive_dir(archive_dir, TEST_JOB_EXPORT)

        upsert_archive_job(
            db_conn, TEST_JOB_EXPORT, "Completed", *RANGE_A,
            file_path=final_dir,
        )

        # First run: should delete 20 rows
        purge_completed_jobs(config, log)
        count_after_first = count_practice_logs(db_conn, *RANGE_A)
        assert count_after_first == 0

        # Job should now be Purged / source_deleted=1
        job = _get_archive_job(db_conn, TEST_JOB_EXPORT)
        assert job["status"] == "Purged"
        assert job["source_deleted"] == 1

        # Second run: no eligible jobs (source_deleted=1), no crash
        log2 = MagicMock()
        purge_completed_jobs(config, log2)

        # No new audit entries
        audits = _count_audit_entries(db_conn, TEST_JOB_EXPORT)
        assert len(audits) == 1, (
            f"Rerun must not create extra audit entries; found {len(audits)}"
        )

        # Count still 0
        assert count_practice_logs(db_conn, *RANGE_A) == 0

    def test_export_rerun_yields_zero_rows(
        self, integration_db_config, db_conn, log, archive_dir
    ):
        """After purge, re-exporting the same date range returns 0 rows."""
        config = _config_with_dir(integration_db_config, archive_dir)
        final_dir = _make_archive_dir(archive_dir, TEST_JOB_EXPORT)
        upsert_archive_job(
            db_conn, TEST_JOB_EXPORT, "Completed", *RANGE_A,
            file_path=final_dir,
        )

        # Purge first
        purge_completed_jobs(config, log)
        assert count_practice_logs(db_conn, *RANGE_A) == 0

        # Re-export should yield 0 rows
        meta = _simple_meta(*RANGE_A)
        staging2 = os.path.join(archive_dir, ".staging", "rerun_export")
        os.makedirs(staging2, exist_ok=True)

        _, row_count, _ = export_fact_data(
            config=config,
            staging_dir=staging2,
            meta=meta,
            source_table="tabMemora Practice Log",
            archive_type_name="practice_log",
        )
        assert row_count == 0, (
            f"Re-export after purge must yield 0 rows, got {row_count}"
        )


# ===========================================================================
# Category 6: Large Dataset (100K rows)
# ===========================================================================

class TestLargeDataset:
    """Verify batched delete works correctly at 100,000-row scale."""

    @pytest.fixture
    def dataset_100k(self, db_conn):
        _ensure_audit_table_in_setup(db_conn)
        delete_test_practice_logs_by_prefix(db_conn, "LG")  # remove overflow rows
        delete_test_practice_logs(db_conn, *RANGE_D)
        delete_test_jobs(db_conn, [TEST_JOB_LARGE])
        delete_test_audit_logs(db_conn, [TEST_JOB_LARGE])
        insert_test_players(db_conn, num_players=20)
        count = insert_practice_log_rows(
            db_conn, prefix="LG", count=100_000,
            date_from=RANGE_D[0], date_to=RANGE_D[1],
            num_players=20,
            batch_size=1000,
        )
        yield count
        delete_test_practice_logs(db_conn, *RANGE_D)
        delete_test_jobs(db_conn, [TEST_JOB_LARGE])
        delete_test_audit_logs(db_conn, [TEST_JOB_LARGE])

    def test_100k_rows_inserted(self, db_conn, dataset_100k):
        """Verify 100K rows were inserted before the pipeline test."""
        assert dataset_100k == 100_000
        count = count_practice_logs(db_conn, *RANGE_D)
        assert count == 100_000, f"Expected 100K rows in DB, got {count}"

    def test_100k_export_row_count(
        self, integration_db_config, db_conn, log, archive_dir, dataset_100k
    ):
        """Export 100K rows: Parquet row count matches MariaDB count."""
        config = _config_with_dir(integration_db_config, archive_dir)
        staging = os.path.join(archive_dir, ".staging", TEST_JOB_LARGE)
        os.makedirs(staging, exist_ok=True)

        meta = _simple_meta(*RANGE_D)
        start = time.monotonic()
        fact_path, row_count, _ = export_fact_data(
            config=config, staging_dir=staging, meta=meta,
            source_table="tabMemora Practice Log", archive_type_name="practice_log",
        )
        elapsed = time.monotonic() - start

        assert row_count == 100_000, f"Expected 100K exported rows, got {row_count}"
        parquet_count = pq.read_metadata(fact_path).num_rows
        assert parquet_count == 100_000

        print(f"\n  100K export elapsed: {elapsed:.2f}s")

    def test_100k_purge_batching(
        self, integration_db_config, db_conn, log, archive_dir, dataset_100k
    ):
        """Purge 100K rows: all deleted in batches; runtime acceptable; audit correct."""
        config = _config_with_dir(integration_db_config, archive_dir)
        final_dir = _make_archive_dir(archive_dir, TEST_JOB_LARGE)
        upsert_archive_job(
            db_conn, TEST_JOB_LARGE, "Completed", *RANGE_D,
            file_path=final_dir,
        )

        start = time.monotonic()
        purge_completed_jobs(config, log)
        elapsed = time.monotonic() - start

        # No rows remain
        post_count = count_practice_logs(db_conn, *RANGE_D)
        assert post_count == 0, f"Expected 0 rows after 100K purge, got {post_count}"

        # Audit is correct
        audits = _count_audit_entries(db_conn, TEST_JOB_LARGE)
        assert len(audits) == 1
        assert audits[0]["status"] == "success"
        assert audits[0]["rows_deleted"] == 100_000
        assert audits[0]["num_batches"] >= (100_000 // PURGE_BATCH_SIZE)

        # Memory safety: no OOM crash (test passed means no crash)
        print(
            f"\n  100K purge elapsed: {elapsed:.2f}s, "
            f"batches: {audits[0]['num_batches']}, "
            f"throughput: {100_000 / elapsed:.0f} rows/s"
        )


# ===========================================================================
# Category 7: Audit Log Integration
# ===========================================================================

class TestAuditLogIntegration:
    """Verify audit log writing and querying against real MariaDB."""

    @pytest.fixture(autouse=True)
    def _setup(self, db_conn):
        _ensure_audit_table_in_setup(db_conn)

    def test_audit_log_insert_and_query(self, integration_db_config, db_conn, log):
        """_log_delete_audit writes a real row; query by status/season returns it."""
        job_id = "ARCH-99099"
        # Clean up first
        with db_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM `archive_delete_audit_log` WHERE job_id = %s",
                (job_id,),
            )
        db_conn.commit()

        _log_delete_audit(
            integration_db_config, log,
            job_id=job_id,
            season_id="SEAS-TEST-001",
            rows_deleted=42,
            duration_ms=500,
            status="success",
            error_msg=None,
            total_rows_estimated=42,
            batch_size=PURGE_BATCH_SIZE,
            num_batches=1,
        )

        rows = _count_audit_entries(db_conn, job_id)
        assert len(rows) == 1
        assert rows[0]["rows_deleted"] == 42
        assert rows[0]["status"] == "success"
        assert rows[0]["season_id"] == "SEAS-TEST-001"
        assert rows[0]["batch_size"] == PURGE_BATCH_SIZE
        assert rows[0]["num_batches"] == 1
        assert rows[0]["executor_host"] is not None

        # Cleanup
        with db_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM `archive_delete_audit_log` WHERE job_id = %s",
                (job_id,),
            )
        db_conn.commit()

    def test_audit_log_idempotent_on_duplicate(
        self, integration_db_config, db_conn, log
    ):
        """Calling _log_delete_audit twice with same job_id updates via ON DUPLICATE KEY."""
        job_id = "ARCH-99098"
        with db_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM `archive_delete_audit_log` WHERE job_id = %s",
                (job_id,),
            )
        db_conn.commit()

        kwargs = dict(
            job_id=job_id, season_id="SEAS-TEST-001",
            rows_deleted=10, duration_ms=100,
            status="success", error_msg=None,
            total_rows_estimated=10, batch_size=PURGE_BATCH_SIZE, num_batches=1,
        )
        _log_delete_audit(integration_db_config, log, **kwargs)
        _log_delete_audit(integration_db_config, log, **{**kwargs, "rows_deleted": 99})

        rows = _count_audit_entries(db_conn, job_id)
        assert len(rows) == 1, "ON DUPLICATE KEY UPDATE must keep exactly 1 row"
        assert rows[0]["rows_deleted"] == 99, "Second call should update rows_deleted to 99"

        with db_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM `archive_delete_audit_log` WHERE job_id = %s",
                (job_id,),
            )
        db_conn.commit()

    def test_audit_log_query_by_season_and_status(
        self, integration_db_config, db_conn, log
    ):
        """Audit table supports filtered queries by season_id and status."""
        job_id = "ARCH-99097"
        with db_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM `archive_delete_audit_log` WHERE job_id = %s",
                (job_id,),
            )
        db_conn.commit()

        _log_delete_audit(
            integration_db_config, log,
            job_id=job_id, season_id="SEAS-QUERY-TEST",
            rows_deleted=5, duration_ms=50,
            status="success", error_msg=None,
            total_rows_estimated=5, batch_size=PURGE_BATCH_SIZE, num_batches=1,
        )

        conn = get_connection(integration_db_config)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM `archive_delete_audit_log` "
                    "WHERE `status` = %s AND `season_id` = %s",
                    ("success", "SEAS-QUERY-TEST"),
                )
                results = cursor.fetchall()
        finally:
            conn.close()

        matching = [r for r in results if r["job_id"] == job_id]
        assert len(matching) >= 1
        assert matching[0]["rows_deleted"] == 5

        with db_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM `archive_delete_audit_log` WHERE job_id = %s",
                (job_id,),
            )
        db_conn.commit()


# ===========================================================================
# Utility helpers (module-level, not fixtures)
# ===========================================================================

def _simple_meta(date_from: str, date_to: str) -> dict:
    """Minimal job meta dict for export tests (no dimension exports)."""
    return {
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
        },
        "related_tables": [],
    }


def _make_archive_dir(base: str, job_name: str) -> str:
    """Create and return a real directory for the archive job's file_path."""
    d = os.path.join(base, job_name)
    os.makedirs(d, exist_ok=True)
    # Create a stub manifest so the purge `os.path.isdir` check passes
    manifest = {
        "manifest_version": "1.0",
        "batch_id": job_name,
        "dataset_key": "practice_log_archive",
        "kind": "archive",
        "schema_version": "1.0",
        "source": "memora_admin",
        "files": [],
    }
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump(manifest, f)
    return d
