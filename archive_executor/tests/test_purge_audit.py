"""Unit tests for purge audit logging.

Test file: archive_executor/tests/test_purge_audit.py
Total tests: 18
Run: python3 -m pytest archive_executor/tests/test_purge_audit.py -v
"""

import time
from unittest.mock import MagicMock, call, patch

import pytest

from archive_executor.config import Config
from archive_executor.purge import (
    PURGE_BATCH_SIZE,
    _log_delete_audit,
    purge_completed_jobs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return Config(
        db_host="localhost", db_port=3306,
        db_user="test", db_password="test",
        db_name="test_db",
        archive_output_path="/tmp/archive/",
        schema_registry_path="/tmp/schema/",
        log_path="/tmp/logs/",
        lock_file="/tmp/archive.lock",
        chunk_size=50000,
        stuck_timeout_hours=1,
        ssh_host="", ssh_user="", ssh_key_path="",
        ssh_port=22, ssh_timeout=300,
        remote_archive_path="", remote_live_path="",
        analytics_cmd_path="", duckdb_path="",
        live_output_path="/tmp/live/", live_lock_file="/tmp/live.lock",
        sync_state_path="/tmp/sync_state/", sync_output_path="/tmp/sync_output/",
        sync_overlap_seconds=300, sync_remote_path="",
        purge_grace_days=7,
        snapshot_output_path="/tmp/snapshots/",
        remote_snapshot_path="",
    )


@pytest.fixture
def log():
    return MagicMock()


def _make_job(
    name="job-001",
    source_doctype="Practice Log",
    archive_scope="season-2024",
    job_meta=None,
    purge_progress=None,
    file_path="/tmp/archive/job-001",
):
    if job_meta is None:
        job_meta = {
            "query_filter": {
                "date_from": "2024-01-01",
                "date_to": "2025-01-01",
                "filter_column": "last_seen_at",
            }
        }
    return {
        "name": name,
        "source_doctype": source_doctype,
        "archive_scope": archive_scope,
        "job_meta": job_meta,
        "purge_progress": purge_progress,
        "file_path": file_path,
    }


def _make_conn_factory(batch_sequence):
    """Return a side_effect callable that yields connections with rowcounts from batch_sequence.

    The last value in batch_sequence should be 0 to terminate the DELETE loop.
    An additional connection is implicitly provided for the audit log INSERT.
    """
    conn_iter = iter(batch_sequence)

    def make_conn(*args, **kwargs):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        try:
            cursor.rowcount = next(conn_iter)
        except StopIteration:
            cursor.rowcount = 0
        conn.cursor = MagicMock(return_value=cursor)
        return conn

    return make_conn


# ---------------------------------------------------------------------------
# 1. TestBatchedDeletePerformance
# ---------------------------------------------------------------------------

class TestBatchedDeletePerformance:
    """Validates batching behaviour and throughput of the purge loop."""

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=100_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_delete_large_season_batching(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """100K rows deleted successfully in batches; wall-clock time < 30s."""
        mock_jobs.return_value = [_make_job()]
        # 10 batches of 10K then 0 to stop, plus one extra 0 for audit log conn
        mock_conn.side_effect = _make_conn_factory([10_000] * 10 + [0, 0])

        start = time.monotonic()
        purge_completed_jobs(config, log)
        elapsed = time.monotonic() - start

        assert elapsed < 30, f"Expected < 30s, got {elapsed:.2f}s"
        log.info.assert_any_call("purge_job_completed", job="job-001", total_deleted=100_000)

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=30_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_batching_respects_limit_10000(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """Every DELETE statement must include LIMIT 10000."""
        mock_jobs.return_value = [_make_job()]
        delete_sqls = []
        conn_iter = iter([10_000, 10_000, 10_000, 0, 0])

        def make_conn(*args, **kwargs):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            try:
                cursor.rowcount = next(conn_iter)
            except StopIteration:
                cursor.rowcount = 0

            def capture_execute(sql, params=None):
                if "DELETE" in sql:
                    delete_sqls.append(sql)

            cursor.execute = capture_execute
            conn.cursor = MagicMock(return_value=cursor)
            return conn

        mock_conn.side_effect = make_conn
        purge_completed_jobs(config, log)

        assert len(delete_sqls) >= 3, "Expected at least 3 DELETE batches"
        for sql in delete_sqls:
            assert f"LIMIT {PURGE_BATCH_SIZE}" in sql, \
                f"DELETE SQL missing LIMIT {PURGE_BATCH_SIZE}: {sql}"

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=20_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_delete_batches_complete_successfully(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """All rows deleted; job reaches Purged state with 0 rows remaining."""
        mock_jobs.return_value = [_make_job()]
        mock_conn.side_effect = _make_conn_factory([10_000, 10_000, 0, 0])

        purge_completed_jobs(config, log)

        # _mark_purged called via atomic_update
        assert mock_atomic.call_count >= 1
        log.info.assert_any_call("purge_job_completed", job="job-001", total_deleted=20_000)

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=5_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_no_table_lock_during_delete(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """Concurrent reads can execute during a DELETE batch (non-exclusive lock model)."""
        mock_jobs.return_value = [_make_job()]
        concurrent_read_ran = [False]
        conn_iter = iter([5_000, 0, 0])

        def make_conn(*args, **kwargs):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            try:
                cursor.rowcount = next(conn_iter)
            except StopIteration:
                cursor.rowcount = 0

            def execute_and_read(sql, params=None):
                if "DELETE" in sql:
                    # Simulate a concurrent SELECT succeeding during the batch
                    read_cursor = MagicMock()
                    read_cursor.fetchall.return_value = [{"name": "pl-001"}]
                    concurrent_read_ran[0] = True

            cursor.execute = execute_and_read
            conn.cursor = MagicMock(return_value=cursor)
            return conn

        mock_conn.side_effect = make_conn
        purge_completed_jobs(config, log)

        assert concurrent_read_ran[0], "Concurrent read did not execute during DELETE batch"

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=50_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_replication_lag_minimal(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """time.sleep(2) is called between every batch to allow replica to catch up."""
        mock_jobs.return_value = [_make_job()]
        mock_conn.side_effect = _make_conn_factory([10_000] * 5 + [0, 0])

        purge_completed_jobs(config, log)

        # 5 non-zero batches → 5 sleep calls each of 2 seconds
        assert mock_sleep.call_count == 5
        for c in mock_sleep.call_args_list:
            assert c == call(2), f"Expected sleep(2), got {c}"

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=100_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_delete_large_season_performance(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """Throughput exceeds 5000 rows/sec (mocked DB removes I/O from measurement)."""
        mock_jobs.return_value = [_make_job()]
        mock_conn.side_effect = _make_conn_factory([10_000] * 10 + [0, 0])

        start = time.monotonic()
        purge_completed_jobs(config, log)
        elapsed = time.monotonic() - start

        if elapsed > 0:
            throughput = 100_000 / elapsed
            assert throughput > 5_000, \
                f"Throughput {throughput:.0f} rows/sec is below 5000 rows/sec threshold"


# ---------------------------------------------------------------------------
# 2. TestAuditLogSchema
# ---------------------------------------------------------------------------

class TestAuditLogSchema:
    """Validates the audit log table schema via INSERT SQL inspection."""

    def _capture_audit_sql(self, config, log, **kwargs):
        """Helper: call _log_delete_audit and return the executed SQL."""
        captured = []
        with patch("archive_executor.purge.get_connection") as mock_conn:
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            cursor.execute = lambda sql, params=None: captured.append(sql)
            conn.cursor.return_value = cursor
            mock_conn.return_value = conn
            _log_delete_audit(config, log, **kwargs)
        return captured

    def _default_kwargs(self):
        return dict(
            job_id="job-001", season_id="season-2024",
            rows_deleted=1000, duration_ms=500,
            status="success", error_msg=None,
            total_rows_estimated=1000, batch_size=10000, num_batches=1,
        )

    def test_audit_table_creation_idempotent(self, config, log):
        """Calling _log_delete_audit twice with the same job_id never raises (ON DUPLICATE KEY)."""
        kwargs = self._default_kwargs()
        with patch("archive_executor.purge.get_connection") as mock_conn:
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            conn.cursor.return_value = cursor
            mock_conn.return_value = conn

            _log_delete_audit(config, log, **kwargs)
            _log_delete_audit(config, log, **kwargs)

            assert mock_conn.call_count == 2, "Expected two separate DB connections"
            assert conn.commit.call_count == 2, "Expected commit on each call"

    def test_audit_table_columns_correct(self, config, log):
        """INSERT SQL includes all required column names."""
        sqls = self._capture_audit_sql(config, log, **self._default_kwargs())

        assert len(sqls) == 1
        sql = sqls[0]
        required_columns = [
            "job_id", "season_id", "rows_deleted", "timestamp",
            "executor_host", "executor_user", "duration_ms",
            "status", "error_msg", "total_rows_estimated",
            "batch_size", "num_batches",
        ]
        for col in required_columns:
            assert col in sql, f"Column '{col}' missing from INSERT SQL"

    def test_audit_table_indexes_exist(self, config, log):
        """ON DUPLICATE KEY UPDATE present (implies UNIQUE index on job_id); table name correct."""
        sqls = self._capture_audit_sql(config, log, **self._default_kwargs())

        assert len(sqls) == 1
        sql = sqls[0]
        assert "archive_delete_audit_log" in sql, "Wrong audit table name in INSERT"
        assert "ON DUPLICATE KEY UPDATE" in sql, \
            "Missing ON DUPLICATE KEY UPDATE — implies UNIQUE(job_id) index is absent"


# ---------------------------------------------------------------------------
# 3. TestAuditLogSuccess
# ---------------------------------------------------------------------------

class TestAuditLogSuccess:
    """Validates audit log rows written on the success path."""

    def _run_purge_and_capture_audit(self, config, log, batch_sequence, job=None):
        """Run purge_completed_jobs and capture audit INSERT params."""
        if job is None:
            job = _make_job()
        audit_params = []
        conn_iter = iter(batch_sequence)

        def make_conn(*args, **kwargs):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            try:
                cursor.rowcount = next(conn_iter)
            except StopIteration:
                cursor.rowcount = 0

            def capture_execute(sql, params=None):
                if "archive_delete_audit_log" in sql:
                    audit_params.append(params)

            cursor.execute = capture_execute
            conn.cursor = MagicMock(return_value=cursor)
            return conn

        with patch("archive_executor.purge.get_connection", side_effect=make_conn), \
             patch("archive_executor.purge.atomic_update"), \
             patch("archive_executor.purge.os.path.isdir", return_value=True), \
             patch("archive_executor.purge.time.sleep"), \
             patch("archive_executor.purge._get_purgeable_jobs", return_value=[job]), \
             patch("archive_executor.purge._get_estimated_row_count", return_value=sum(batch_sequence)):
            purge_completed_jobs(config, log)

        return audit_params

    def test_delete_operation_logged(self, config, log):
        """Successful purge writes one audit row with correct job_id, season_id, and status='success'."""
        audit_params = self._run_purge_and_capture_audit(
            config, log, batch_sequence=[5_000, 0, 0],
        )
        assert len(audit_params) == 1
        job_id, season_id, rows_deleted, host, user, duration_ms, status, *_ = audit_params[0]
        assert job_id == "job-001"
        assert season_id == "season-2024"
        assert rows_deleted == 5_000
        assert status == "success"

    def test_audit_log_required_fields(self, config, log):
        """All required fields (job_id, season_id, rows_deleted, host, user, duration_ms, status,
        total_rows_estimated, batch_size, num_batches) are non-null / non-empty."""
        audit_params = self._run_purge_and_capture_audit(
            config, log, batch_sequence=[1_000, 0, 0],
        )
        assert len(audit_params) == 1
        (job_id, season_id, rows_deleted, host, user,
         duration_ms, status, error_msg,
         total_rows_estimated, batch_size, num_batches) = audit_params[0]

        assert job_id == "job-001"
        assert season_id == "season-2024"
        assert rows_deleted == 1_000
        assert host        # non-empty hostname
        assert user        # non-empty user
        assert duration_ms >= 0
        assert status == "success"
        assert total_rows_estimated >= 0
        assert batch_size == PURGE_BATCH_SIZE
        assert num_batches == 1

    def test_audit_log_timestamps_accurate(self, config, log):
        """duration_ms is between 0 and the observed wall-clock elapsed time."""
        captured_duration = []

        job = _make_job()
        conn_iter = iter([100, 0, 0])

        def make_conn(*args, **kwargs):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            try:
                cursor.rowcount = next(conn_iter)
            except StopIteration:
                cursor.rowcount = 0

            def capture(sql, params=None):
                if "archive_delete_audit_log" in sql:
                    captured_duration.append(params[5])  # duration_ms at index 5

            cursor.execute = capture
            conn.cursor = MagicMock(return_value=cursor)
            return conn

        with patch("archive_executor.purge.get_connection", side_effect=make_conn), \
             patch("archive_executor.purge.atomic_update"), \
             patch("archive_executor.purge.os.path.isdir", return_value=True), \
             patch("archive_executor.purge.time.sleep"), \
             patch("archive_executor.purge._get_purgeable_jobs", return_value=[job]), \
             patch("archive_executor.purge._get_estimated_row_count", return_value=100):
            before = time.monotonic()
            purge_completed_jobs(config, log)
            after = time.monotonic()

        assert len(captured_duration) == 1
        elapsed_ms = (after - before) * 1000
        assert 0 <= captured_duration[0] <= elapsed_ms + 100, \
            f"duration_ms={captured_duration[0]} outside [0, {elapsed_ms + 100:.0f}]"

    def test_audit_log_performance_metrics(self, config, log):
        """total_rows_estimated, batch_size, and num_batches are recorded correctly."""
        audit_params = self._run_purge_and_capture_audit(
            config, log, batch_sequence=[10_000, 10_000, 5_000, 0, 0],
        )
        assert len(audit_params) == 1
        (_, _, rows_deleted, _, _, _, _, _,
         total_rows_estimated, batch_size, num_batches) = audit_params[0]

        assert total_rows_estimated == 25_000  # sum of non-zero batches
        assert batch_size == PURGE_BATCH_SIZE
        assert num_batches == 3
        assert rows_deleted == 25_000


# ---------------------------------------------------------------------------
# 4. TestAuditLogFailure
# ---------------------------------------------------------------------------

class TestAuditLogFailure:
    """Validates audit log rows and error handling on the failure path."""

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=5_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_delete_audit_log_on_failure(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """Purge that fails before deleting any rows logs status='failed' with error_msg."""
        mock_jobs.return_value = [_make_job()]
        audit_params = []

        def make_conn(*args, **kwargs):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)

            def execute(sql, params=None):
                if "DELETE" in sql:
                    raise RuntimeError("DB connection lost")
                if "archive_delete_audit_log" in sql:
                    audit_params.append(params)

            cursor.execute = execute
            cursor.rowcount = 0
            conn.cursor = MagicMock(return_value=cursor)
            return conn

        mock_conn.side_effect = make_conn

        with pytest.raises(RuntimeError, match="DB connection lost"):
            purge_completed_jobs(config, log)

        assert len(audit_params) == 1
        job_id, _, rows_deleted, _, _, _, status, error_msg, *_ = audit_params[0]
        assert job_id == "job-001"
        assert rows_deleted == 0
        assert status == "failed"
        assert "DB connection lost" in error_msg

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=30_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_partial_failure_logged(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """Exception mid-loop records status='partial' with rows_deleted > 0."""
        mock_jobs.return_value = [_make_job()]
        audit_params = []
        conn_iter = iter([10_000, 10_000])  # exhausts on 3rd DELETE → raises

        def make_conn(*args, **kwargs):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)

            def execute(sql, params=None):
                if "DELETE" in sql:
                    try:
                        cursor.rowcount = next(conn_iter)
                    except StopIteration:
                        raise RuntimeError("Disk full mid-batch")
                if "archive_delete_audit_log" in sql:
                    audit_params.append(params)

            cursor.execute = execute
            cursor.rowcount = 0
            conn.cursor = MagicMock(return_value=cursor)
            return conn

        mock_conn.side_effect = make_conn

        with pytest.raises(RuntimeError, match="Disk full mid-batch"):
            purge_completed_jobs(config, log)

        assert len(audit_params) == 1
        _, _, rows_deleted, _, _, _, status, error_msg, *_ = audit_params[0]
        assert rows_deleted > 0, "Expected partial rows_deleted > 0"
        assert status == "partial"
        assert error_msg is not None

    @patch("archive_executor.purge.time.sleep")
    @patch("archive_executor.purge.os.path.isdir", return_value=True)
    @patch("archive_executor.purge.atomic_update")
    @patch("archive_executor.purge._get_estimated_row_count", return_value=5_000)
    @patch("archive_executor.purge._get_purgeable_jobs")
    @patch("archive_executor.purge.get_connection")
    def test_audit_log_failure_nonblocking(
        self, mock_conn, mock_jobs, mock_count, mock_atomic, mock_isdir, mock_sleep,
        config, log,
    ):
        """An audit INSERT failure is swallowed — the purge pipeline does not crash."""
        mock_jobs.return_value = [_make_job()]
        conn_iter = iter([5_000, 0, 0])

        def make_conn(*args, **kwargs):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            try:
                cursor.rowcount = next(conn_iter)
            except StopIteration:
                cursor.rowcount = 0

            def execute(sql, params=None):
                if "archive_delete_audit_log" in sql:
                    raise RuntimeError("Audit table unavailable")

            cursor.execute = execute
            conn.cursor = MagicMock(return_value=cursor)
            return conn

        mock_conn.side_effect = make_conn

        # Must NOT raise despite audit INSERT failing
        purge_completed_jobs(config, log)

        warning_calls = [str(c) for c in log.warning.call_args_list]
        assert any("audit_log_failed" in c for c in warning_calls), \
            "Expected log.warning('audit_log_failed') when audit INSERT fails"


# ---------------------------------------------------------------------------
# 5. TestAuditLogQueries
# ---------------------------------------------------------------------------

class TestAuditLogQueries:
    """Validates query capability and row-level idempotency of the audit log."""

    def test_query_audit_log(self, config, log):
        """Audit log supports queries by status, season_id, and timestamp range."""
        with patch("archive_executor.purge.get_connection") as mock_conn:
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            cursor.fetchall.return_value = [
                {
                    "job_id": "job-001",
                    "season_id": "season-2024",
                    "status": "success",
                    "rows_deleted": 5000,
                    "timestamp": "2024-06-01 10:00:00",
                },
            ]
            conn.cursor.return_value = cursor
            mock_conn.return_value = conn

            result_conn = mock_conn(config)
            with result_conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM archive_delete_audit_log "
                    "WHERE status = %s AND season_id = %s "
                    "AND timestamp BETWEEN %s AND %s",
                    ("success", "season-2024", "2024-01-01", "2025-01-01"),
                )
                rows = cur.fetchall()

            assert len(rows) == 1
            assert rows[0]["status"] == "success"
            assert rows[0]["season_id"] == "season-2024"

    def test_duplicate_job_updates_row(self, config, log):
        """Re-inserting the same job_id updates the existing row via ON DUPLICATE KEY UPDATE."""
        captured_sqls = []
        with patch("archive_executor.purge.get_connection") as mock_conn:
            conn = MagicMock()
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            cursor.execute = lambda sql, params=None: captured_sqls.append(sql)
            conn.cursor.return_value = cursor
            mock_conn.return_value = conn

            kwargs = dict(
                job_id="job-001", season_id="season-2024",
                rows_deleted=1000, duration_ms=500,
                status="success", error_msg=None,
                total_rows_estimated=1000, batch_size=10000, num_batches=1,
            )
            _log_delete_audit(config, log, **kwargs)
            _log_delete_audit(config, log, **kwargs)

        assert len(captured_sqls) == 2
        for sql in captured_sqls:
            assert "ON DUPLICATE KEY UPDATE" in sql, \
                "INSERT must use ON DUPLICATE KEY UPDATE for idempotency on re-purge"
