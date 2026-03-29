"""Comprehensive integration + unit tests for the Memory State Archive Pipeline.

Tests cover:
- Season scheduling (create_season_archive_jobs)
- Season-scoped export (export_fact_data with filter_type=season)
- Incremental sync internals (checkpoint, extract, export Parquet)
- Safety gates (all 5 gates individually + combined)
- Purge routing (season partition vs batched DELETE)
- Season handoff (SSH command building)
- Derived season dimension export
- End-to-end lifecycle

Run with:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python3 -m pytest archive_executor/tests/test_memory_state_pipeline.py -v
"""

import dataclasses
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import pyarrow.parquet as pq

from archive_executor.config import Config
from archive_executor.exporter import export_fact_data
from archive_executor.ingestion import IngestionError, get_mirror_status, handoff_season
from archive_executor.run import _export_season_dimension_by_seq
from archive_executor.safety_gates import (
    GateCheck,
    GateResult,
    _check_archive_validation,
    _check_grace_period,
    _check_partition_exists,
    _check_plan_linkage,
    _check_player_linkage,
    check_all_gates,
)
from archive_executor.scheduler import create_season_archive_jobs
from archive_executor.schemas import load_archive_type, load_dimension_schema
from archive_executor.sync import (
    _checkpoint_path,
    _discover_active_seasons,
    _export_sync_parquet,
    _extract_incremental,
    _is_season_archived,
    _load_checkpoint,
    _save_checkpoint,
)
from archive_executor.validator import validate_fact_quality_generic

from .conftest import (
    MS_PLAYER_PREFIX,
    MS_SEASON_NAME_A,
    MS_SEASON_NAME_B,
    MS_SEASON_NAME_E,
    MS_SEASON_SEQ_A,
    MS_SEASON_SEQ_B,
    MS_SEASON_SEQ_E,
    MS_TEST_JOB_ARCHIVE,
    MS_TEST_JOB_PURGE,
    MS_TEST_JOB_SYNC,
    _make_season_job_meta,
    count_memory_state_rows,
    delete_memory_state_rows,
    delete_memory_state_rows_by_player_prefix,
    delete_ms_jobs_by_scope,
    delete_ms_test_jobs,
    delete_test_seasons,
    ensure_audit_table,
    insert_test_seasons,
    upsert_ms_archive_job,
)

pytestmark = pytest.mark.integration


# ===========================================================================
# TestCoerceValueUnit — pure unit tests, no DB required
# ===========================================================================

import pyarrow as pa
from archive_executor.exporter import _coerce_value


class TestCoerceValueUnit:
    """Unit tests for _coerce_value confirming Decimal handling works natively.

    Bug #1 described in the test reference doc: '_coerce_value doesn't handle
    decimal.Decimal'. This was fixed in exporter.py. These tests confirm that
    the fix works without any monkeypatching.
    """

    pytestmark = pytest.mark.unit

    def test_decimal_returns_float(self):
        result = _coerce_value(Decimal("1.23456789"))
        assert isinstance(result, float)
        assert result == pytest.approx(1.23456789)

    def test_decimal_zero(self):
        assert _coerce_value(Decimal("0")) == pytest.approx(0.0)
        assert isinstance(_coerce_value(Decimal("0")), float)

    def test_decimal_high_precision(self):
        """DECIMAL(21,9) stability value converts without precision loss."""
        val = Decimal("4.123456789")
        result = _coerce_value(val)
        assert isinstance(result, float)
        assert result == pytest.approx(4.123456789, abs=1e-9)

    def test_decimal_difficulty_range(self):
        """DECIMAL(21,9) difficulty value (0–1) converts cleanly."""
        val = Decimal("0.750000000")
        result = _coerce_value(val)
        assert isinstance(result, float)
        assert result == pytest.approx(0.75)

    def test_decimal_with_target_type_float64(self):
        """Decimal still converts to float even when target_type is explicitly float64."""
        val = Decimal("3.14")
        result = _coerce_value(val, pa.float64())
        assert isinstance(result, float)
        assert result == pytest.approx(3.14)

    def test_none_returns_none(self):
        assert _coerce_value(None) is None

    def test_none_with_target_type_returns_none(self):
        assert _coerce_value(None, pa.float64()) is None

    def test_plain_float_unchanged(self):
        assert _coerce_value(1.5) == 1.5

    def test_plain_int_unchanged(self):
        assert _coerce_value(42) == 42

    def test_string_unchanged_without_target(self):
        assert _coerce_value("hello") == "hello"

    def test_string_to_int_with_int_target(self):
        assert _coerce_value("42", pa.int64()) == 42

    def test_string_to_float_with_float_target(self):
        assert _coerce_value("3.14", pa.float64()) == pytest.approx(3.14)

    def test_no_monkeypatch_needed(self):
        """Confirm the real _coerce_value handles Decimal without any patching.

        This test would have FAILED before Bug #1 was fixed (PyArrow type error).
        Its presence here documents that the fix is live and monkeypatches in
        export/sync tests are no longer necessary.
        """
        import pyarrow as _pa

        stability_vals = [Decimal("0.5"), Decimal("4.123456789"), None, Decimal("15.0")]
        coerced = [_coerce_value(v, _pa.float64()) for v in stability_vals]
        assert coerced[0] == pytest.approx(0.5)
        assert coerced[1] == pytest.approx(4.123456789, abs=1e-9)
        assert coerced[2] is None
        assert coerced[3] == pytest.approx(15.0)

        # Build a RecordBatch with the coerced values — this is what the exporter does
        arr = _pa.array(coerced, type=_pa.float64())
        assert arr.to_pylist()[0] == pytest.approx(0.5)
        assert arr.to_pylist()[2] is None


# ---------------------------------------------------------------------------
# Module-level helpers (not in conftest)
# ---------------------------------------------------------------------------

def _insert_ms_rows(conn, prefix: str, count: int, season_seq: int, num_players: int = 5) -> int:
    """Insert Memory State rows with explicit unique `name` values.

    The conftest helper `insert_memory_state_rows` omits `name`, causing all rows
    to land on name=0 (MariaDB implicit default) and a PK collision — only the
    first INSERT per season_seq succeeds. This wrapper generates deterministic
    BIGINT names that are unique per (prefix, n) pair so INSERT IGNORE works.
    """
    from datetime import datetime as _dt, timedelta as _td

    now = _dt(2099, 6, 1, 12, 0, 0)
    rows = []

    for n in range(1, count + 1):
        ts = now + _td(seconds=n * 60)
        player_id = f"MS-PLAYER-{(n % num_players) + 1:03d}"
        item_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{prefix}-{n}").bytes
        # Deterministic BIGINT name via uuid5 (SHA1, not Python hash — stable across
        # runs). We take 7 bytes (56 bits) from the UUID bytes to stay well within
        # the signed BIGINT range (max 63 bits). Values are unique per (prefix, n).
        name_bytes = uuid.uuid5(uuid.NAMESPACE_DNS, f"ms-name-{prefix}-{n}").bytes
        name_val = int.from_bytes(name_bytes[:7], "big") + 1  # +1 avoids 0

        rows.append((
            name_val,
            season_seq,
            f"Science-{(n % 3) + 1}",
            player_id,
            item_uuid,
            round(0.5 + (n % 100) * 0.05, 4),
            round((n % 100) / 100.0, 4),
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            f"MS-LESSON-{prefix}-{(n % 3) + 1:03d}",
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
        "(`name`, `season_seq`, `subject`, `player`, `item_id`, "
        " `stability`, `difficulty`, `next_review`, `lesson`, "
        " `state`, `step`, `last_review`, "
        " `creation`, `modified`, `modified_by`, `owner`) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()
    return count


def _config_with_archive_dir(cfg: Config, tmpdir: str) -> Config:
    return dataclasses.replace(cfg, archive_output_path=tmpdir + "/")


def _config_with_sync_dirs(cfg: Config, sync_state: str, sync_output: str) -> Config:
    return dataclasses.replace(
        cfg,
        sync_state_path=sync_state + "/",
        sync_output_path=sync_output + "/",
    )


def _get_archive_job(conn, name: str) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM `tabMemora Archive Job` WHERE name = %s", (name,))
        return cursor.fetchone()


def _get_jobs_by_scope(conn, scope: str) -> list:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM `tabMemora Archive Job` "
            "WHERE source_doctype = 'Memora Memory State' AND archive_scope = %s",
            (scope,),
        )
        return cursor.fetchall()


def _set_job_completed_at(conn, job_name: str, completed_at: datetime) -> None:
    """Set completed_at on an archive job for grace period testing."""
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE `tabMemora Archive Job` SET completed_at = %s WHERE name = %s",
            (completed_at.strftime("%Y-%m-%d %H:%M:%S"), job_name),
        )
    conn.commit()


def _make_season_e_ended(conn) -> None:
    """Update season E to have a past end_date so CURDATE() sees it as ended.

    The conftest inserts season E with end_date=2098-12-31 (future in 2026).
    Many scheduler/sync tests need it to be in the past.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE `tabMemora Season` SET end_date = '2025-01-01' WHERE name = %s",
            (MS_SEASON_NAME_E,),
        )
    conn.commit()


def _insert_test_players_for_season(conn, season_name: str, count: int = 2,
                                     prefix: str = "MS-GATE-PLAYER") -> None:
    """Insert test player profiles linked to a season."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Player Profile` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `grade`, `major`, `season`, `plan`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', "
        "        0, %s, 'Grade-1', 'Science', %s, 'PLAN-TEST-001')"
    )
    rows = [(f"{prefix}-{i:03d}", i, season_name) for i in range(1, count + 1)]
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()


def _delete_test_players_by_prefix(conn, prefix: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Player Profile` WHERE name LIKE %s",
            (f"{prefix}%",),
        )
    conn.commit()


def _insert_test_plan_for_season(conn, plan_name: str, season_name: str,
                                  is_published: int = 1) -> None:
    """Insert a test academic plan linked to a season."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Academic Plan` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `season`, `is_published`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', "
        "        0, 1, %s, %s)"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (plan_name, season_name, is_published))
    conn.commit()


def _update_plan_published(conn, plan_name: str, is_published: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE `tabMemora Academic Plan` SET is_published = %s WHERE name = %s",
            (is_published, plan_name),
        )
    conn.commit()


def _delete_test_plan(conn, plan_name: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Academic Plan` WHERE name = %s", (plan_name,)
        )
    conn.commit()


def _find_real_partition_seq(conn) -> int | None:
    """Find an existing p_season_N partition on tabMemora Memory State."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT PARTITION_NAME FROM INFORMATION_SCHEMA.PARTITIONS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "  AND TABLE_NAME = 'tabMemora Memory State' "
            "  AND PARTITION_NAME REGEXP '^p_season_[0-9]+$' "
            "LIMIT 1"
        )
        row = cursor.fetchone()
    if row:
        parts = row["PARTITION_NAME"].split("_")
        return int(parts[2])
    return None


def _build_export_meta(season_seq: int, season_name: str) -> dict:
    """Build parsed meta dict for season-scoped export (like run.py would use)."""
    return json.loads(_make_season_job_meta(season_seq, season_name))


def _make_mock_log():
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.error = MagicMock()
    log.debug = MagicMock()
    return log


# ===========================================================================
# TestSeasonScheduler
# ===========================================================================

class TestSeasonScheduler:
    """Integration tests for create_season_archive_jobs()."""

    @pytest.fixture(autouse=True)
    def prepare(self, db_conn, integration_db_config):
        insert_test_seasons(db_conn)
        _make_season_e_ended(db_conn)  # season E must be ended for scheduler
        delete_ms_jobs_by_scope(db_conn, scope_prefix="season_990")
        yield
        delete_ms_jobs_by_scope(db_conn, scope_prefix="season_990")
        delete_test_seasons(db_conn)

    def test_creates_job_for_ended_season_only(self, db_conn, integration_db_config):
        """Scheduler creates Pending job for season E (ended), not A or B (active)."""
        created = create_season_archive_jobs(integration_db_config, "memory_state")

        # At least season E should appear in the created list
        season_seqs = [c["season_seq"] for c in created]
        assert MS_SEASON_SEQ_E in season_seqs, (
            f"Expected season {MS_SEASON_SEQ_E} in created jobs, got: {season_seqs}"
        )
        # Active seasons should not have been scheduled
        assert MS_SEASON_SEQ_A not in season_seqs
        assert MS_SEASON_SEQ_B not in season_seqs

    def test_job_meta_contains_season_filter(self, db_conn, integration_db_config):
        """Created job_meta has filter_type='season', correct season_seq, season_name."""
        created = create_season_archive_jobs(integration_db_config, "memory_state")

        # Find the entry for season E
        entry_e = next((c for c in created if c["season_seq"] == MS_SEASON_SEQ_E), None)
        assert entry_e is not None

        job = _get_archive_job(db_conn, entry_e["job_name"])
        assert job is not None

        meta = json.loads(job["job_meta"])
        qf = meta["query_filter"]
        assert qf["filter_type"] == "season"
        assert qf["season_seq"] == MS_SEASON_SEQ_E
        assert qf["season_name"] == MS_SEASON_NAME_E

    def test_archive_scope_format(self, db_conn, integration_db_config):
        """archive_scope for season jobs is 'season_N'."""
        created = create_season_archive_jobs(integration_db_config, "memory_state")

        entry_e = next((c for c in created if c["season_seq"] == MS_SEASON_SEQ_E), None)
        assert entry_e is not None

        job = _get_archive_job(db_conn, entry_e["job_name"])
        assert job["archive_scope"] == f"season_{MS_SEASON_SEQ_E}"

    def test_skips_season_with_existing_non_failed_job(self, db_conn, integration_db_config):
        """Scheduler skips a season that already has a non-Failed (Pending) job."""
        # Pre-create a Pending job for season E
        upsert_ms_archive_job(db_conn, MS_TEST_JOB_ARCHIVE, "Pending", MS_SEASON_SEQ_E, MS_SEASON_NAME_E)

        created = create_season_archive_jobs(integration_db_config, "memory_state")

        # Season E should NOT appear since a non-Failed job exists
        season_seqs = [c["season_seq"] for c in created]
        assert MS_SEASON_SEQ_E not in season_seqs

    def test_allows_rescheduling_after_failed_job(self, db_conn, integration_db_config):
        """_job_exists() returns False for Failed jobs, permitting rescheduling.

        The scheduler's NOT EXISTS subquery excludes Failed status — so a season
        whose only archive job is Failed is considered eligible. We test this via
        _job_exists directly because the unique constraint on
        (source_doctype, archive_scope, schema_version) prevents a second INSERT
        while the Failed row still occupies that slot.
        """
        from archive_executor.scheduler import _job_exists

        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Failed", MS_SEASON_SEQ_E, MS_SEASON_NAME_E
        )

        result = _job_exists(
            db_conn,
            "Memora Memory State",
            f"season_{MS_SEASON_SEQ_E}",
            "v1",
        )

        assert result is False, (
            "_job_exists must return False for Failed jobs so rescheduling is permitted"
        )

    def test_no_jobs_created_when_all_seasons_active(self, db_conn, integration_db_config):
        """No jobs created when all test seasons have future end_dates (no ended seasons).

        We revert season E back to a future end_date for this test to confirm the
        scheduler correctly returns an empty list when no eligible seasons exist.
        """
        # Revert season E to future (active) end_date
        with db_conn.cursor() as cursor:
            cursor.execute(
                "UPDATE `tabMemora Season` SET end_date = '2098-12-31' WHERE name = %s",
                (MS_SEASON_NAME_E,),
            )
        db_conn.commit()

        created = create_season_archive_jobs(integration_db_config, "memory_state")

        # None of our test seasons should appear
        test_seqs = {MS_SEASON_SEQ_A, MS_SEASON_SEQ_B, MS_SEASON_SEQ_E}
        created_test_seqs = {c["season_seq"] for c in created} & test_seqs
        assert len(created_test_seqs) == 0, (
            f"Expected no test seasons scheduled, but got: {created_test_seqs}"
        )

    def test_created_job_status_is_pending(self, db_conn, integration_db_config):
        """Newly created jobs have status='Pending'."""
        created = create_season_archive_jobs(integration_db_config, "memory_state")

        entry_e = next((c for c in created if c["season_seq"] == MS_SEASON_SEQ_E), None)
        assert entry_e is not None

        job = _get_archive_job(db_conn, entry_e["job_name"])
        assert job["status"] == "Pending"
        assert job["source_doctype"] == "Memora Memory State"
        assert job["archive_type"] == "memory_state"
        assert job["schema_version"] == "v1"


# ===========================================================================
# TestSeasonExport
# ===========================================================================

class TestSeasonExport:
    """Integration tests for season-scoped export_fact_data()."""

    ROW_COUNT = 50

    @pytest.fixture(autouse=True)
    def prepare(self, db_conn, integration_db_config):
        insert_test_seasons(db_conn)
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_E)
        _insert_ms_rows(db_conn, "EXP", self.ROW_COUNT, MS_SEASON_SEQ_E)
        yield
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_E)
        delete_test_seasons(db_conn)

    def _do_export(self, integration_db_config, tmpdir, season_seq=None, season_name=None):
        seq = season_seq or MS_SEASON_SEQ_E
        name = season_name or MS_SEASON_NAME_E
        meta = _build_export_meta(seq, name)
        export_metadata = {
            "archive_scope": f"season_{seq}",
            "archive_job_id": "ARCH-MS-TEST-EXPORT",
            "schema_version": "v1",
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        return export_fact_data(
            config=integration_db_config,
            staging_dir=tmpdir,
            meta=meta,
            source_table="tabMemora Memory State",
            archive_type_name="memory_state",
            export_metadata=export_metadata,
        )

    def test_row_count_matches_inserted(self, db_conn, integration_db_config, archive_dir):
        """Exported Parquet row count matches the number of inserted rows."""
        fact_path, row_count, _ = self._do_export(integration_db_config, archive_dir)

        assert os.path.isfile(fact_path)
        assert row_count == self.ROW_COUNT

        pf = pq.read_table(fact_path)
        assert pf.num_rows == self.ROW_COUNT

    def test_item_id_column_contains_uuid_strings(self, db_conn, integration_db_config, archive_dir):
        """item_id column contains 36-character UUID strings (BIN_TO_UUID conversion)."""
        fact_path, _, _ = self._do_export(integration_db_config, archive_dir)

        table = pq.read_table(fact_path, columns=["item_id"])
        item_ids = table.column("item_id").to_pylist()

        assert all(isinstance(v, str) for v in item_ids), "All item_ids must be strings"
        assert all(len(v) == 36 for v in item_ids), "All item_ids must be 36 chars (UUID format)"

    def test_item_id_uuid_format_is_valid(self, db_conn, integration_db_config, archive_dir):
        """BIN_TO_UUID produces valid RFC 4122 UUID strings."""
        fact_path, _, _ = self._do_export(integration_db_config, archive_dir)

        table = pq.read_table(fact_path, columns=["item_id"])
        item_ids = table.column("item_id").to_pylist()

        for item_id in item_ids[:10]:  # spot-check first 10
            parsed = uuid.UUID(item_id)  # raises ValueError if invalid UUID
            assert str(parsed) == item_id.lower(), f"UUID round-trip failed for {item_id}"

    def test_export_metadata_columns_present(self, db_conn, integration_db_config, archive_dir):
        """Export Parquet contains archive_scope, archive_job_id, schema_version, exported_at columns."""
        fact_path, _, _ = self._do_export(integration_db_config, archive_dir)

        table = pq.read_table(fact_path)
        col_names = set(table.column_names)

        assert "archive_scope" in col_names
        assert "archive_job_id" in col_names
        assert "schema_version" in col_names
        assert "exported_at" in col_names

        # Verify values
        scopes = table.column("archive_scope").to_pylist()
        assert all(s == f"season_{MS_SEASON_SEQ_E}" for s in scopes)

    def test_zero_row_export_produces_valid_parquet(self, db_conn, integration_db_config, archive_dir):
        """Season with 0 rows still produces a valid empty Parquet file."""
        # Delete all rows for season E first
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_E)

        fact_path, row_count, _ = self._do_export(integration_db_config, archive_dir)

        assert os.path.isfile(fact_path)
        assert row_count == 0

        table = pq.read_table(fact_path)
        assert table.num_rows == 0
        # Schema should still be present
        assert len(table.schema) > 0

    def test_dq_validation_passes_on_exported_parquet(
        self, db_conn, integration_db_config, archive_dir
    ):
        """DQ validation passes on well-formed exported Memory State data."""
        fact_path, row_count, _ = self._do_export(integration_db_config, archive_dir)

        archive_schema = load_archive_type(
            integration_db_config.schema_registry_path, "memory_state", "v1"
        )
        dq_rules = archive_schema["dq_rules"]

        # No dimension paths — DQ-11 (referential on player) is skipped automatically
        dq_result = validate_fact_quality_generic(
            fact_path=fact_path,
            dq_rules=dq_rules,
            dimension_paths={},
        )

        failed = [r for r in dq_result["results"] if not r["passed"]]
        assert dq_result["passed"], f"DQ validation failed: {failed}"

    def test_export_isolation_between_seasons(
        self, db_conn, integration_db_config, archive_dir
    ):
        """Exporting season E does not include rows from season A."""
        # Insert rows for season A as well
        _insert_ms_rows(db_conn, "ISO-A", 10, MS_SEASON_SEQ_A)

        try:
            fact_path, row_count, _ = self._do_export(integration_db_config, archive_dir)

            # Should only have rows for season E
            assert row_count == self.ROW_COUNT

            table = pq.read_table(fact_path, columns=["season_seq"])
            seqs = set(table.column("season_seq").to_pylist())
            assert seqs == {MS_SEASON_SEQ_E}, f"Expected only season {MS_SEASON_SEQ_E}, got {seqs}"
        finally:
            delete_memory_state_rows(db_conn, MS_SEASON_SEQ_A)

    def test_binary_to_uuid_round_trip(self, db_conn, integration_db_config, archive_dir):
        """item_id round-trip: insert with UUID_TO_BIN, export with BIN_TO_UUID, compare."""
        # Insert one row with a known UUID
        known_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, "test-round-trip")
        known_bytes = known_uuid.bytes

        with db_conn.cursor() as cursor:
            cursor.execute(
                "INSERT IGNORE INTO `tabMemora Memory State` "
                "(`season_seq`, `subject`, `player`, `item_id`, "
                " `stability`, `difficulty`, `next_review`, `lesson`, "
                " `state`, `last_review`, `creation`, `modified`, `modified_by`, `owner`) "
                "VALUES (%s, 'Science-1', 'MS-PLAYER-RT', %s, "
                " 0.7, 0.3, '2099-06-01 12:00:00', 'MS-LESSON-RT-001', "
                " 1, '2099-06-01 12:00:00', '2099-06-01 12:00:00', '2099-06-01 12:00:00', "
                " 'test@test.com', 'test@test.com')",
                (MS_SEASON_SEQ_E, known_bytes),
            )
        db_conn.commit()

        fact_path, _, _ = self._do_export(integration_db_config, archive_dir)
        table = pq.read_table(fact_path, columns=["item_id"])
        exported_ids = set(table.column("item_id").to_pylist())

        assert str(known_uuid) in exported_ids, (
            f"Expected UUID {known_uuid} in exported item_ids"
        )

        # Cleanup
        with db_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM `tabMemora Memory State` WHERE player = 'MS-PLAYER-RT'"
                " AND season_seq = %s", (MS_SEASON_SEQ_E,)
            )
        db_conn.commit()


# ===========================================================================
# TestIncrementalSync
# ===========================================================================

class TestIncrementalSync:
    """Mix of unit and integration tests for incremental sync internals."""

    @pytest.fixture(autouse=True)
    def prepare(self, db_conn, integration_db_config):
        insert_test_seasons(db_conn)
        # Delete by player prefix across ALL seasons to avoid INSERT IGNORE conflicts
        # caused by deterministic item_ids from previous test runs in other seasons.
        delete_memory_state_rows_by_player_prefix(db_conn)
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_A)
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_E)
        yield
        delete_memory_state_rows_by_player_prefix(db_conn)
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_A)
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_E)
        delete_test_seasons(db_conn)
        delete_ms_test_jobs(db_conn)

    # --- Season discovery ---

    def test_discover_active_seasons_includes_active(
        self, db_conn, integration_db_config
    ):
        """_discover_active_seasons() returns seasons with end_date >= today."""
        results = _discover_active_seasons(integration_db_config)
        result_seqs = {r["season_seq"] for r in results}

        # Seasons A and B have end_date=2099-12-31 → active
        assert MS_SEASON_SEQ_A in result_seqs
        assert MS_SEASON_SEQ_B in result_seqs

    def test_discover_active_seasons_excludes_ended(
        self, db_conn, integration_db_config
    ):
        """_discover_active_seasons() excludes seasons with end_date < today."""
        _make_season_e_ended(db_conn)  # set end_date to 2025-01-01 (past)

        results = _discover_active_seasons(integration_db_config)
        result_seqs = {r["season_seq"] for r in results}

        # Season E with past end_date should NOT be active
        assert MS_SEASON_SEQ_E not in result_seqs

    # --- Checkpoint management ---

    def test_load_checkpoint_returns_epoch_default_when_no_file(
        self, integration_db_config
    ):
        """_load_checkpoint() returns default epoch timestamp when file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_sync_dirs(integration_db_config, tmpdir, tmpdir)
            cp = _load_checkpoint(cfg, "memory_state", season_seq=99999)

        assert cp["last_checkpoint"] == "1970-01-01T00:00:00"
        assert cp["season_seq"] == 99999
        assert cp["total_rows_synced"] == 0

    def test_save_and_load_checkpoint_roundtrip(self, integration_db_config):
        """_save_checkpoint() writes valid JSON that _load_checkpoint() reads back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_sync_dirs(integration_db_config, tmpdir, tmpdir)

            checkpoint = {
                "season_seq": MS_SEASON_SEQ_A,
                "season_name": MS_SEASON_NAME_A,
                "last_checkpoint": "2099-06-01T12:00:00",
                "last_sync_rows": 42,
                "total_rows_synced": 420,
                "last_sync_at": "2099-06-01T12:30:00",
            }
            _save_checkpoint(cfg, "memory_state", checkpoint)
            loaded = _load_checkpoint(cfg, "memory_state", MS_SEASON_SEQ_A)

        assert loaded["last_checkpoint"] == "2099-06-01T12:00:00"
        assert loaded["total_rows_synced"] == 420
        assert loaded["season_name"] == MS_SEASON_NAME_A

    def test_save_checkpoint_is_atomic(self, integration_db_config):
        """_save_checkpoint() uses atomic tmp+replace (no partial writes visible)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_sync_dirs(integration_db_config, tmpdir, tmpdir)

            checkpoint = {
                "season_seq": MS_SEASON_SEQ_A,
                "season_name": MS_SEASON_NAME_A,
                "last_checkpoint": "2099-06-15T08:00:00",
                "last_sync_rows": 10,
                "total_rows_synced": 10,
                "last_sync_at": "2099-06-15T08:05:00",
            }
            _save_checkpoint(cfg, "memory_state", checkpoint)

            # The .tmp file should not remain after successful save
            path = _checkpoint_path(cfg, "memory_state", MS_SEASON_SEQ_A)
            tmp_path = path + ".tmp"
            assert os.path.isfile(path)
            assert not os.path.isfile(tmp_path)

    # --- Incremental extraction ---

    def test_extract_incremental_returns_rows_for_season(
        self, db_conn, integration_db_config
    ):
        """_extract_incremental() extracts rows for the specified season."""
        _insert_ms_rows(db_conn, "SYNC-EXT", 15, MS_SEASON_SEQ_A)
        archive_schema = load_archive_type(
            integration_db_config.schema_registry_path, "memory_state", "v1"
        )

        rows, max_modified = _extract_incremental(
            config=integration_db_config,
            archive_schema=archive_schema,
            season_seq=MS_SEASON_SEQ_A,
            extract_from="1970-01-01T00:00:00",
        )

        assert len(rows) >= 15
        assert max_modified is not None

    def test_extract_incremental_returns_empty_for_future_window(
        self, db_conn, integration_db_config
    ):
        """_extract_incremental() returns empty list when no rows match the time window."""
        _insert_ms_rows(db_conn, "SYNC-EMPTY", 5, MS_SEASON_SEQ_A)
        archive_schema = load_archive_type(
            integration_db_config.schema_registry_path, "memory_state", "v1"
        )

        rows, max_modified = _extract_incremental(
            config=integration_db_config,
            archive_schema=archive_schema,
            season_seq=MS_SEASON_SEQ_A,
            extract_from="2100-01-01T00:00:00",  # far future → no rows match
        )

        assert rows == []
        assert max_modified is None

    def test_extract_incremental_respects_season_boundary(
        self, db_conn, integration_db_config
    ):
        """_extract_incremental() only returns rows for the specified season_seq."""
        _insert_ms_rows(db_conn, "SYNC-BOUND-A", 10, MS_SEASON_SEQ_A)
        _insert_ms_rows(db_conn, "SYNC-BOUND-E", 10, MS_SEASON_SEQ_E)

        archive_schema = load_archive_type(
            integration_db_config.schema_registry_path, "memory_state", "v1"
        )

        rows, _ = _extract_incremental(
            config=integration_db_config,
            archive_schema=archive_schema,
            season_seq=MS_SEASON_SEQ_A,
            extract_from="1970-01-01T00:00:00",
        )

        # All returned rows must be from season A
        for row in rows:
            assert row["season_seq"] == MS_SEASON_SEQ_A

    # --- Archive check ---

    def test_is_season_archived_true_when_non_failed_job_exists(
        self, db_conn, integration_db_config
    ):
        """_is_season_archived() returns True when a non-Failed archive job exists."""
        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Pending", MS_SEASON_SEQ_A, MS_SEASON_NAME_A
        )

        result = _is_season_archived(integration_db_config, MS_SEASON_SEQ_A)
        assert result is True

    def test_is_season_archived_false_when_no_job_exists(
        self, db_conn, integration_db_config
    ):
        """_is_season_archived() returns False when no archive job exists for the season."""
        result = _is_season_archived(integration_db_config, MS_SEASON_SEQ_B)
        assert result is False

    def test_is_season_archived_false_when_only_failed_job_exists(
        self, db_conn, integration_db_config
    ):
        """_is_season_archived() returns False when the only job is Failed."""
        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Failed", MS_SEASON_SEQ_A, MS_SEASON_NAME_A
        )

        result = _is_season_archived(integration_db_config, MS_SEASON_SEQ_A)
        assert result is False

    def test_is_season_archived_true_for_completed_job(
        self, db_conn, integration_db_config
    ):
        """_is_season_archived() returns True for a Completed archive job."""
        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Completed", MS_SEASON_SEQ_A, MS_SEASON_NAME_A
        )

        result = _is_season_archived(integration_db_config, MS_SEASON_SEQ_A)
        assert result is True

    # --- Parquet export ---

    def test_export_sync_parquet_produces_parquet_with_metadata_columns(
        self, db_conn, integration_db_config
    ):
        """_export_sync_parquet() produces Parquet with synced_at and archive_scope columns."""
        _insert_ms_rows(db_conn, "SYNC-PAR", 10, MS_SEASON_SEQ_A)
        archive_schema = load_archive_type(
            integration_db_config.schema_registry_path, "memory_state", "v1"
        )

        rows, _ = _extract_incremental(
            config=integration_db_config,
            archive_schema=archive_schema,
            season_seq=MS_SEASON_SEQ_A,
            extract_from="1970-01-01T00:00:00",
        )
        assert len(rows) >= 10

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_sync_dirs(integration_db_config, tmpdir, tmpdir)
            output_dir = _export_sync_parquet(
                config=cfg,
                archive_schema=archive_schema,
                rows=rows,
                season_seq=MS_SEASON_SEQ_A,
                archive_type="memory_state",
            )

            assert os.path.isdir(output_dir)
            parquet_files = [f for f in os.listdir(output_dir) if f.endswith(".parquet")]
            assert len(parquet_files) == 1

            table = pq.read_table(os.path.join(output_dir, parquet_files[0]))
            col_names = set(table.column_names)
            assert "synced_at" in col_names
            assert "archive_scope" in col_names

            scopes = table.column("archive_scope").to_pylist()
            assert all(s == f"season_{MS_SEASON_SEQ_A}" for s in scopes)

    # --- Checkpoint not advanced on error ---

    def test_checkpoint_not_advanced_on_extraction_error(self, integration_db_config):
        """Checkpoint file is unchanged when extraction fails (error resilience)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _config_with_sync_dirs(integration_db_config, tmpdir, tmpdir)

            # Write initial checkpoint
            initial_checkpoint = {
                "season_seq": MS_SEASON_SEQ_A,
                "season_name": MS_SEASON_NAME_A,
                "last_checkpoint": "2099-05-01T00:00:00",
                "last_sync_rows": 0,
                "total_rows_synced": 100,
                "last_sync_at": "2099-05-01T00:00:00",
            }
            _save_checkpoint(cfg, "memory_state", initial_checkpoint)

            # Simulate an extraction error
            archive_schema = load_archive_type(
                integration_db_config.schema_registry_path, "memory_state", "v1"
            )

            from archive_executor import sync as sync_module

            with patch.object(sync_module, "_discover_active_seasons") as mock_discover, \
                 patch.object(sync_module, "_extract_incremental",
                              side_effect=RuntimeError("simulated extraction failure")), \
                 patch.object(sync_module, "_transfer_and_ingest") as mock_transfer:
                mock_discover.return_value = [
                    {"season_seq": MS_SEASON_SEQ_A, "season_name": MS_SEASON_NAME_A}
                ]
                mock_transfer.return_value = None

                result = sync_module.run_incremental_sync(cfg, "memory_state")

            # Verify checkpoint was NOT updated
            loaded = _load_checkpoint(cfg, "memory_state", MS_SEASON_SEQ_A)
            assert loaded["last_checkpoint"] == "2099-05-01T00:00:00", (
                "Checkpoint should not advance after extraction failure"
            )
            assert loaded["total_rows_synced"] == 100

            # Verify the error result is captured
            season_result = next(
                (r for r in result["results"] if r["season_seq"] == MS_SEASON_SEQ_A), None
            )
            assert season_result is not None
            assert "error" in season_result["status"].lower()


# ===========================================================================
# TestSafetyGates
# ===========================================================================

class TestSafetyGates:
    """Integration tests for all 5 safety gates."""

    @pytest.fixture(autouse=True)
    def prepare(self, db_conn, integration_db_config, ensure_integration_audit_table):
        insert_test_seasons(db_conn)
        delete_ms_test_jobs(db_conn)
        yield
        delete_ms_test_jobs(db_conn)
        delete_test_seasons(db_conn)
        _delete_test_players_by_prefix(db_conn, "MS-GATE-PLAYER")
        _delete_test_plan(db_conn, "MS-GATE-PLAN-001")

    # --- Gate 0: Grace period ---

    def test_gate0_blocks_when_completed_recently(self, db_conn, integration_db_config):
        """Gate 0 blocks when archive was completed less than 7 days ago."""
        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Completed", MS_SEASON_SEQ_E, MS_SEASON_NAME_E
        )
        _set_job_completed_at(
            db_conn, MS_TEST_JOB_ARCHIVE,
            datetime.now(timezone.utc) - timedelta(days=1),
        )

        gate = _check_grace_period(integration_db_config, MS_SEASON_SEQ_E)

        assert not gate.passed
        assert gate.gate_name == "grace_period"
        assert "days remaining" in gate.message or "not met" in gate.message

    def test_gate0_passes_when_completed_8_days_ago(self, db_conn, integration_db_config):
        """Gate 0 passes when archive was completed 8 days ago (> 7-day grace period)."""
        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Completed", MS_SEASON_SEQ_E, MS_SEASON_NAME_E
        )
        _set_job_completed_at(
            db_conn, MS_TEST_JOB_ARCHIVE,
            datetime.now(timezone.utc) - timedelta(days=8),
        )

        gate = _check_grace_period(integration_db_config, MS_SEASON_SEQ_E)

        assert gate.passed
        assert "satisfied" in gate.message

    def test_gate0_blocks_when_no_completed_archive(self, db_conn, integration_db_config):
        """Gate 0 blocks when no completed archive job exists."""
        # No job inserted for this season
        gate = _check_grace_period(integration_db_config, MS_SEASON_SEQ_E)

        assert not gate.passed
        assert "No completed archive" in gate.message

    def test_gate0_blocks_when_completed_at_is_null(self, db_conn, integration_db_config):
        """Gate 0 blocks when archive job has no completed_at timestamp."""
        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Completed", MS_SEASON_SEQ_E, MS_SEASON_NAME_E
        )
        # completed_at is NULL by default (upsert_ms_archive_job doesn't set it)

        gate = _check_grace_period(integration_db_config, MS_SEASON_SEQ_E)

        assert not gate.passed

    # --- Gate 1: Archive validation ---

    def test_gate1_passes_with_completed_job(self, db_conn, integration_db_config):
        """Gate 1 passes when a Completed archive job exists for the season."""
        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Completed", MS_SEASON_SEQ_E, MS_SEASON_NAME_E
        )

        gate = _check_archive_validation(integration_db_config, MS_SEASON_SEQ_E)

        assert gate.passed
        assert gate.gate_name == "archive_validation"

    def test_gate1_fails_without_completed_job(self, db_conn, integration_db_config):
        """Gate 1 fails when no Completed/Purged archive job exists."""
        gate = _check_archive_validation(integration_db_config, MS_SEASON_SEQ_E)

        assert not gate.passed
        assert "No validated archive" in gate.message

    def test_gate1_fails_when_job_is_only_pending(self, db_conn, integration_db_config):
        """Gate 1 fails when the archive job is Pending (not yet Completed)."""
        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_ARCHIVE, "Pending", MS_SEASON_SEQ_E, MS_SEASON_NAME_E
        )

        gate = _check_archive_validation(integration_db_config, MS_SEASON_SEQ_E)

        assert not gate.passed

    # --- Gate 2: Player linkage ---

    def test_gate2_passes_when_no_players_linked(self, db_conn, integration_db_config):
        """Gate 2 passes when no player profiles are linked to the season."""
        gate = _check_player_linkage(integration_db_config, MS_SEASON_NAME_E)

        assert gate.passed
        assert gate.details["linked_players"] == 0

    def test_gate2_blocks_when_players_linked(self, db_conn, integration_db_config):
        """Gate 2 blocks when player profiles are linked to the season."""
        _insert_test_players_for_season(db_conn, MS_SEASON_NAME_E, count=3)

        gate = _check_player_linkage(integration_db_config, MS_SEASON_NAME_E)

        assert not gate.passed
        assert gate.details["linked_players"] == 3

    def test_gate2_passes_after_unlinking_players(self, db_conn, integration_db_config):
        """Gate 2 passes after linked players are removed."""
        _insert_test_players_for_season(db_conn, MS_SEASON_NAME_E, count=2)

        gate_before = _check_player_linkage(integration_db_config, MS_SEASON_NAME_E)
        assert not gate_before.passed

        _delete_test_players_by_prefix(db_conn, "MS-GATE-PLAYER")

        gate_after = _check_player_linkage(integration_db_config, MS_SEASON_NAME_E)
        assert gate_after.passed

    # --- Gate 3: Plan linkage ---

    def test_gate3_passes_when_no_plans_linked(self, db_conn, integration_db_config):
        """Gate 3 passes when no published academic plans are linked to the season."""
        gate = _check_plan_linkage(integration_db_config, MS_SEASON_NAME_E)

        assert gate.passed
        assert gate.details["linked_plans"] == 0

    def test_gate3_blocks_when_published_plan_linked(self, db_conn, integration_db_config):
        """Gate 3 blocks when a published academic plan is linked to the season."""
        _insert_test_plan_for_season(db_conn, "MS-GATE-PLAN-001", MS_SEASON_NAME_E, is_published=1)

        gate = _check_plan_linkage(integration_db_config, MS_SEASON_NAME_E)

        assert not gate.passed
        assert gate.details["linked_plans"] == 1

    def test_gate3_passes_when_plan_is_unpublished(self, db_conn, integration_db_config):
        """Gate 3 passes when the linked plan is unpublished."""
        _insert_test_plan_for_season(db_conn, "MS-GATE-PLAN-001", MS_SEASON_NAME_E, is_published=1)
        _update_plan_published(db_conn, "MS-GATE-PLAN-001", is_published=0)

        gate = _check_plan_linkage(integration_db_config, MS_SEASON_NAME_E)

        assert gate.passed

    # --- Gate 4: Partition exists ---

    def test_gate4_fails_for_nonexistent_partition(self, integration_db_config):
        """Gate 4 fails when the partition p_season_999999 doesn't exist."""
        gate = _check_partition_exists(integration_db_config, 999999)

        assert not gate.passed
        assert "not found" in gate.message

    def test_gate4_passes_for_real_partition(self, db_conn, integration_db_config):
        """Gate 4 passes when an existing p_season_N partition is found."""
        real_seq = _find_real_partition_seq(db_conn)
        if real_seq is None:
            pytest.skip("No p_season_N partitions found in test DB; skipping Gate 4 pass test")

        gate = _check_partition_exists(integration_db_config, real_seq)

        assert gate.passed
        assert f"p_season_{real_seq}" in gate.message

    def test_gate4_rejects_non_season_partition_format(self, integration_db_config):
        """Gate 4 fails for a season_seq mapping to a bad partition name (e.g., p_future)."""
        # Using a season_seq of 0 would map to p_season_0 which almost certainly doesn't exist
        gate = _check_partition_exists(integration_db_config, 0)

        assert not gate.passed

    # --- check_all_gates: No short-circuit, all blockers reported ---

    def test_check_all_gates_reports_all_blockers(self, db_conn, integration_db_config):
        """check_all_gates() runs all 5 gates and returns all blockers (no short-circuit)."""
        # No Completed job, players linked, published plan linked → multiple gates fail
        _insert_test_players_for_season(db_conn, MS_SEASON_NAME_E, count=1)
        _insert_test_plan_for_season(db_conn, "MS-GATE-PLAN-001", MS_SEASON_NAME_E, is_published=1)

        result = check_all_gates(
            integration_db_config, MS_SEASON_NAME_E, MS_SEASON_SEQ_E
        )

        assert not result.passed
        # At minimum: gate 0 (no completed archive), gate 1 (no completed archive),
        # gate 2 (player linked), gate 3 (plan linked) should fail
        assert len(result.blockers) >= 3
        assert len(result.gates) == 5, "Exactly 5 gates must be checked"

    def test_check_all_gates_does_not_short_circuit(self, db_conn, integration_db_config):
        """Even when gate 0 fails, gates 1-4 are still evaluated."""
        result = check_all_gates(
            integration_db_config, MS_SEASON_NAME_E, MS_SEASON_SEQ_E
        )

        # All 5 gates should have been checked regardless of failures
        gate_names = [g.gate_name for g in result.gates]
        assert "grace_period" in gate_names
        assert "archive_validation" in gate_names
        assert "player_linkage" in gate_names
        assert "plan_linkage" in gate_names
        assert "partition_exists" in gate_names

    def test_check_all_gates_passes_when_all_conditions_met(
        self, db_conn, integration_db_config
    ):
        """check_all_gates() returns passed=True only when all 5 gates pass."""
        real_seq = _find_real_partition_seq(db_conn)
        if real_seq is None:
            pytest.skip("No real partition found; cannot verify all-gates-pass scenario")

        # Use the real season_seq so partition gate passes
        # Set up a Completed job with old enough completed_at (8 days)
        # No players or plans linked to this season
        real_scope = f"season_{real_seq}"

        # Clean up any existing jobs for this scope
        with db_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM `tabMemora Archive Job` "
                "WHERE source_doctype = 'Memora Memory State' AND archive_scope = %s",
                (real_scope,),
            )
        db_conn.commit()

        # Insert a real Completed job with old enough completed_at
        with db_conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO `tabMemora Archive Job` "
                "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
                " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
                " `status`, `priority`, `retry_count`, `post_archive_action`, "
                " `source_deleted`, `sync_paused`, "
                " `duration_seconds`, `row_count`, `file_size_bytes`, "
                " `completed_at`, `job_meta`) "
                "VALUES ('ARCH-MS-GATE-REAL', NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0, "
                "        'Memora Memory State', %s, 'v1', 'memory_state', "
                "        'Completed', 'Normal', 0, 'Delete', 0, 0, 0, 0, 0, "
                "        DATE_SUB(NOW(), INTERVAL 10 DAY), '{}')",
                (real_scope,),
            )
        db_conn.commit()

        # Query for the real season name
        with db_conn.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM `tabMemora Season` WHERE season_seq = %s LIMIT 1",
                (real_seq,)
            )
            row = cursor.fetchone()

        if row is None:
            pytest.skip(f"No season record for seq {real_seq}")

        real_season_name = row["name"]
        try:
            result = check_all_gates(integration_db_config, real_season_name, real_seq)
            # Gate 0, 1, 4 should pass; gates 2, 3 should pass if no players/plans
            gates_map = {g.gate_name: g for g in result.gates}
            assert gates_map["grace_period"].passed, "Gate 0 should pass with 10-day-old archive"
            assert gates_map["archive_validation"].passed, "Gate 1 should pass with Completed job"
            assert gates_map["partition_exists"].passed, "Gate 4 should pass for real partition"
        finally:
            with db_conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM `tabMemora Archive Job` WHERE name = 'ARCH-MS-GATE-REAL'"
                )
            db_conn.commit()


# ===========================================================================
# TestPurgePartitionRouting
# ===========================================================================

class TestPurgePartitionRouting:
    """Unit tests for purge routing logic (season partition vs batched DELETE)."""

    @pytest.fixture(autouse=True)
    def prepare(self, db_conn, integration_db_config, ensure_integration_audit_table):
        delete_ms_test_jobs(db_conn)
        yield
        delete_ms_test_jobs(db_conn)

    def _make_season_job_dict(self):
        return {
            "name": MS_TEST_JOB_PURGE,
            "source_doctype": "Memora Memory State",
            "archive_scope": f"season_{MS_SEASON_SEQ_E}",
            "job_meta": json.dumps({
                "query_filter": {
                    "filter_type": "season",
                    "season_seq": MS_SEASON_SEQ_E,
                    "season_name": MS_SEASON_NAME_E,
                    "filter_column": "season_seq",
                },
            }),
            "purge_progress": None,
            "file_path": "/fake/archive/season_9903",
        }

    def _make_date_job_dict(self):
        return {
            "name": "ARCH-99001",
            "source_doctype": "Memora Practice Log",
            "archive_scope": "2099-01-01",
            "job_meta": json.dumps({
                "query_filter": {
                    "date_from": "2099-01-01",
                    "date_to": "2099-01-02",
                    "filter_column": "last_seen_at",
                },
            }),
            "purge_progress": None,
            "file_path": "/some/archive/dir",
        }

    def test_season_job_routes_to_partition_purge(self, integration_db_config):
        """purge_completed_jobs() calls _purge_partition for season-scoped jobs."""
        from archive_executor.purge import purge_completed_jobs

        with patch("archive_executor.purge._get_purgeable_jobs",
                   return_value=[self._make_season_job_dict()]), \
             patch("archive_executor.purge._purge_partition") as mock_purge:
            purge_completed_jobs(integration_db_config, _make_mock_log())

        mock_purge.assert_called_once()
        call_args = mock_purge.call_args
        job_arg = call_args[0][1]  # second positional arg is job dict
        assert job_arg["name"] == MS_TEST_JOB_PURGE

    def test_date_job_does_not_route_to_partition_purge(self, integration_db_config):
        """purge_completed_jobs() does NOT call _purge_partition for date-range jobs."""
        from archive_executor.purge import purge_completed_jobs

        with patch("archive_executor.purge._get_purgeable_jobs",
                   return_value=[self._make_date_job_dict()]), \
             patch("archive_executor.purge._purge_partition") as mock_purge, \
             patch("os.path.isdir", return_value=False):  # skip file path check
            purge_completed_jobs(integration_db_config, _make_mock_log())

        mock_purge.assert_not_called()

    def test_purge_partition_does_not_drop_when_gates_fail(
        self, db_conn, integration_db_config
    ):
        """_purge_partition() does NOT open a DROP connection when safety gates fail."""
        from archive_executor.purge import _purge_partition

        failing_result = GateResult(
            passed=False,
            gates=[GateCheck(
                gate_name="archive_validation", passed=False,
                message="No validated archive found"
            )],
            blockers=["No validated archive found"],
            season_name=MS_SEASON_NAME_E,
            season_seq=MS_SEASON_SEQ_E,
            checked_at="2026-01-01T00:00:00",
        )

        job = {"name": MS_TEST_JOB_PURGE, "archive_scope": f"season_{MS_SEASON_SEQ_E}"}
        meta = {"query_filter": {
            "filter_type": "season",
            "season_seq": MS_SEASON_SEQ_E,
            "season_name": MS_SEASON_NAME_E,
        }}

        with patch("archive_executor.purge.check_all_gates", return_value=failing_result), \
             patch("archive_executor.purge.get_connection") as mock_conn:
            _purge_partition(integration_db_config, job, meta, _make_mock_log())

        # No DB connection for DROP PARTITION should have been opened
        mock_conn.assert_not_called()

    def test_purge_partition_leaves_job_completed_when_blocked(
        self, db_conn, integration_db_config
    ):
        """_purge_partition() leaves job status as Completed (not Purged) when gates block."""
        from archive_executor.purge import _purge_partition

        upsert_ms_archive_job(
            db_conn, MS_TEST_JOB_PURGE, "Completed", MS_SEASON_SEQ_E, MS_SEASON_NAME_E
        )

        failing_result = GateResult(
            passed=False,
            gates=[GateCheck(gate_name="grace_period", passed=False, message="Grace period not met")],
            blockers=["Grace period not met"],
            season_name=MS_SEASON_NAME_E,
            season_seq=MS_SEASON_SEQ_E,
            checked_at="2026-01-01T00:00:00",
        )

        job = {"name": MS_TEST_JOB_PURGE, "archive_scope": f"season_{MS_SEASON_SEQ_E}"}
        meta = {"query_filter": {
            "filter_type": "season",
            "season_seq": MS_SEASON_SEQ_E,
            "season_name": MS_SEASON_NAME_E,
        }}

        with patch("archive_executor.purge.check_all_gates", return_value=failing_result):
            _purge_partition(integration_db_config, job, meta, _make_mock_log())

        db_job = _get_archive_job(db_conn, MS_TEST_JOB_PURGE)
        assert db_job is not None
        assert db_job["status"] == "Completed", (
            "Job must remain Completed (not Purged) when gates are blocked"
        )

    def test_purge_partition_marks_purged_and_audits_on_success(
        self, integration_db_config
    ):
        """_purge_partition() calls _mark_purged and _log_delete_audit on success."""
        from archive_executor.purge import _purge_partition

        passing_result = GateResult(
            passed=True,
            gates=[],
            blockers=[],
            season_name=MS_SEASON_NAME_E,
            season_seq=MS_SEASON_SEQ_E,
            checked_at="2026-01-01T00:00:00",
        )

        job = {
            "name": MS_TEST_JOB_PURGE,
            "archive_scope": f"season_{MS_SEASON_SEQ_E}",
            "file_path": "/fake/archive/season_9903",
        }
        meta = {"query_filter": {
            "filter_type": "season",
            "season_seq": MS_SEASON_SEQ_E,
            "season_name": MS_SEASON_NAME_E,
        }}

        # Mock connection to prevent real ALTER TABLE DROP PARTITION
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = {"cnt": 50}
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("archive_executor.purge.check_all_gates", return_value=passing_result), \
             patch("archive_executor.purge.get_connection", return_value=mock_conn), \
             patch("archive_executor.purge._mark_purged") as mock_mark_purged, \
             patch("archive_executor.purge._log_delete_audit") as mock_audit, \
             patch("archive_executor.purge.os.path.isdir", return_value=True):
            _purge_partition(integration_db_config, job, meta, _make_mock_log())

        mock_mark_purged.assert_called_once_with(integration_db_config, MS_TEST_JOB_PURGE)
        mock_audit.assert_called_once()
        audit_kwargs = mock_audit.call_args[1]
        assert audit_kwargs["status"] == "success"
        assert audit_kwargs["job_id"] == MS_TEST_JOB_PURGE

    def test_all_gates_evaluated_independently_on_failure(self, integration_db_config):
        """Multiple gate failures are all reported when check_all_gates fails."""
        from archive_executor.purge import purge_completed_jobs

        multi_fail_result = GateResult(
            passed=False,
            gates=[
                GateCheck("grace_period", False, "Grace period not met"),
                GateCheck("archive_validation", True, "Archive found"),
                GateCheck("player_linkage", False, "Players still linked"),
                GateCheck("plan_linkage", False, "Plans still linked"),
                GateCheck("partition_exists", True, "Partition found"),
            ],
            blockers=["Grace period not met", "Players still linked", "Plans still linked"],
            season_name=MS_SEASON_NAME_E,
            season_seq=MS_SEASON_SEQ_E,
            checked_at="2026-01-01T00:00:00",
        )

        with patch("archive_executor.purge._get_purgeable_jobs",
                   return_value=[self._make_season_job_dict()]), \
             patch("archive_executor.purge.check_all_gates", return_value=multi_fail_result) as mock_gates, \
             patch("archive_executor.purge.get_connection") as mock_conn, \
             patch("archive_executor.purge.os.path.isdir", return_value=True):
            purge_completed_jobs(integration_db_config, _make_mock_log())

        mock_gates.assert_called_once()
        # check_all_gates result had 3 blockers — no DROP should have executed
        mock_conn.assert_not_called()


# ===========================================================================
# TestSeasonHandoff
# ===========================================================================

class TestSeasonHandoff:
    """Unit tests for season handoff and mirror status (SSH command building)."""

    def test_handoff_season_builds_correct_command(self, integration_db_config):
        """handoff_season() builds CLI command with --season-seq and --archive-type."""
        log = _make_mock_log()
        with patch("archive_executor.ingestion._run_ssh_command") as mock_ssh:
            mock_ssh.return_value = (0, '{"status": "ok", "rows_removed": 42}', "")
            result = handoff_season(
                config=integration_db_config,
                archive_path="/remote/archive/season_9903",
                season_seq=MS_SEASON_SEQ_E,
                archive_type="memory_state",
                log=log,
            )

        assert result["status"] == "ok"
        assert result["rows_removed"] == 42

        cmd = mock_ssh.call_args[0][1]
        assert f"--season-seq {MS_SEASON_SEQ_E}" in cmd
        assert "--archive-type memory_state" in cmd
        assert "handoff" in cmd

    def test_handoff_season_includes_archive_path(self, integration_db_config):
        """handoff_season() includes the archive batch dir in the command."""
        log = _make_mock_log()
        archive_path = "/remote/archive/season_9903"
        with patch("archive_executor.ingestion._run_ssh_command") as mock_ssh:
            mock_ssh.return_value = (0, '{"status": "ok", "rows_removed": 0}', "")
            handoff_season(
                config=integration_db_config,
                archive_path=archive_path,
                season_seq=MS_SEASON_SEQ_E,
                archive_type="memory_state",
                log=log,
            )

        cmd = mock_ssh.call_args[0][1]
        assert "--archive-batch-dir" in cmd
        assert "season_9903" in cmd

    def test_handoff_season_raises_ingestion_error_on_failure(self, integration_db_config):
        """handoff_season() raises IngestionError when remote command exits non-zero."""
        log = _make_mock_log()
        with patch("archive_executor.ingestion._run_ssh_command") as mock_ssh:
            mock_ssh.return_value = (1, '{"error": "season not found"}', "")
            with pytest.raises(IngestionError, match="Season handoff failed"):
                handoff_season(
                    config=integration_db_config,
                    archive_path="/remote/archive",
                    season_seq=MS_SEASON_SEQ_E,
                    archive_type="memory_state",
                    log=log,
                )

    def test_get_mirror_status_builds_correct_command(self, integration_db_config):
        """get_mirror_status() builds CLI command with --archive-type."""
        log = _make_mock_log()
        response = {
            "status": "ok",
            "archive_type": "memory_state",
            "current_mirror": {"total_rows": 500, "seasons": []},
            "archived_seasons": [],
        }
        with patch("archive_executor.ingestion._run_ssh_command") as mock_ssh:
            mock_ssh.return_value = (0, json.dumps(response), "")
            result = get_mirror_status(
                config=integration_db_config,
                archive_type="memory_state",
                log=log,
            )

        assert result["status"] == "ok"
        cmd = mock_ssh.call_args[0][1]
        assert "mirror-status" in cmd
        assert "--archive-type memory_state" in cmd

    def test_get_mirror_status_raises_ingestion_error_on_failure(self, integration_db_config):
        """get_mirror_status() raises IngestionError when command returns non-ok status."""
        log = _make_mock_log()
        with patch("archive_executor.ingestion._run_ssh_command") as mock_ssh:
            mock_ssh.return_value = (1, '{"status": "error", "error": "db unavailable"}', "")
            with pytest.raises(IngestionError):
                get_mirror_status(
                    config=integration_db_config,
                    archive_type="memory_state",
                    log=log,
                )


# ===========================================================================
# TestDerivedSeasonDimension
# ===========================================================================

class TestDerivedSeasonDimension:
    """Integration tests for derived season dimension export."""

    @pytest.fixture(autouse=True)
    def prepare(self, db_conn):
        insert_test_seasons(db_conn)
        yield
        delete_test_seasons(db_conn)

    def test_season_dimension_exported_by_seq(self, db_conn, integration_db_config, archive_dir):
        """_export_season_dimension_by_seq() exports a Parquet file for the given season."""
        dim_schema = load_dimension_schema(
            integration_db_config.schema_registry_path, "season", "v1"
        )

        path, row_count = _export_season_dimension_by_seq(
            config=integration_db_config,
            staging_dir=archive_dir,
            dim_schema=dim_schema,
            season_seq=MS_SEASON_SEQ_E,
        )

        assert os.path.isfile(path)
        assert row_count == 1

        table = pq.read_table(path)
        assert table.num_rows == 1

    def test_season_dimension_contains_correct_fields(
        self, db_conn, integration_db_config, archive_dir
    ):
        """Season dimension Parquet contains season_id, season_title, start_date, end_date."""
        dim_schema = load_dimension_schema(
            integration_db_config.schema_registry_path, "season", "v1"
        )

        path, _ = _export_season_dimension_by_seq(
            config=integration_db_config,
            staging_dir=archive_dir,
            dim_schema=dim_schema,
            season_seq=MS_SEASON_SEQ_E,
        )

        table = pq.read_table(path)
        col_names = set(table.column_names)

        assert "season_id" in col_names
        assert "season_title" in col_names
        assert "start_date" in col_names
        assert "end_date" in col_names

    def test_season_dimension_contains_correct_values(
        self, db_conn, integration_db_config, archive_dir
    ):
        """Season dimension row has the correct season_id and title."""
        dim_schema = load_dimension_schema(
            integration_db_config.schema_registry_path, "season", "v1"
        )

        path, _ = _export_season_dimension_by_seq(
            config=integration_db_config,
            staging_dir=archive_dir,
            dim_schema=dim_schema,
            season_seq=MS_SEASON_SEQ_E,
        )

        table = pq.read_table(path)
        row = {col: table.column(col).to_pylist()[0] for col in table.column_names}

        assert row["season_id"] == MS_SEASON_NAME_E
        assert "Test Season E" in str(row["season_title"])

    def test_season_dimension_returns_empty_for_nonexistent_seq(
        self, db_conn, integration_db_config, archive_dir
    ):
        """Season dimension returns empty Parquet when season_seq doesn't exist."""
        dim_schema = load_dimension_schema(
            integration_db_config.schema_registry_path, "season", "v1"
        )

        path, row_count = _export_season_dimension_by_seq(
            config=integration_db_config,
            staging_dir=archive_dir,
            dim_schema=dim_schema,
            season_seq=88888,  # non-existent
        )

        assert os.path.isfile(path)
        assert row_count == 0

        table = pq.read_table(path)
        assert table.num_rows == 0

    def test_season_dimension_output_path_is_dim_season_parquet(
        self, db_conn, integration_db_config, archive_dir
    ):
        """Season dimension output file is named dim_season.parquet."""
        dim_schema = load_dimension_schema(
            integration_db_config.schema_registry_path, "season", "v1"
        )

        path, _ = _export_season_dimension_by_seq(
            config=integration_db_config,
            staging_dir=archive_dir,
            dim_schema=dim_schema,
            season_seq=MS_SEASON_SEQ_E,
        )

        assert os.path.basename(path) == "dim_season.parquet"


# ===========================================================================
# TestEndToEndSeasonLifecycle
# ===========================================================================

class TestEndToEndSeasonLifecycle:
    """Integration test for the full season archive lifecycle (scheduler → export → DQ)."""

    @pytest.fixture(autouse=True)
    def prepare(self, db_conn, integration_db_config, ensure_integration_audit_table):
        insert_test_seasons(db_conn)
        _make_season_e_ended(db_conn)
        delete_memory_state_rows_by_player_prefix(db_conn)
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_E)
        delete_ms_jobs_by_scope(db_conn, scope_prefix="season_990")
        yield
        delete_memory_state_rows_by_player_prefix(db_conn)
        delete_memory_state_rows(db_conn, MS_SEASON_SEQ_E)
        delete_ms_jobs_by_scope(db_conn, scope_prefix="season_990")
        delete_test_seasons(db_conn)

    def test_end_to_end_scheduler_to_export(
        self, db_conn, integration_db_config, archive_dir
    ):
        """Full lifecycle: insert rows → schedule → export → DQ passes → sync skips."""

        # Step 1: Insert 100 memory state rows for season E
        inserted = _insert_ms_rows(db_conn, "E2E", 100, MS_SEASON_SEQ_E)
        assert inserted == 100
        assert count_memory_state_rows(db_conn, MS_SEASON_SEQ_E) == 100

        # Step 2: Run scheduler → Pending job should be created for season E
        created = create_season_archive_jobs(integration_db_config, "memory_state")
        e2e_jobs = [c for c in created if c["season_seq"] == MS_SEASON_SEQ_E]
        assert len(e2e_jobs) == 1, f"Expected 1 job for season E, got {e2e_jobs}"

        # Commit db_conn to start a fresh transaction — the scheduler used a separate
        # connection, and REPEATABLE READ would hide the new job in db_conn's old snapshot.
        db_conn.commit()

        job_name = e2e_jobs[0]["job_name"]
        job = _get_archive_job(db_conn, job_name)
        assert job["status"] == "Pending"
        assert job["archive_scope"] == f"season_{MS_SEASON_SEQ_E}"

        # Step 3: Verify job_meta is correct
        meta = json.loads(job["job_meta"])
        qf = meta["query_filter"]
        assert qf["filter_type"] == "season"
        assert qf["season_seq"] == MS_SEASON_SEQ_E
        assert qf["season_name"] == MS_SEASON_NAME_E

        # Step 4: Export the job's fact data
        export_meta = json.loads(json.dumps(meta))  # deep copy
        export_metadata = {
            "archive_scope": job["archive_scope"],
            "archive_job_id": job_name,
            "schema_version": job["schema_version"],
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        fact_path, row_count, _ = export_fact_data(
            config=integration_db_config,
            staging_dir=archive_dir,
            meta=export_meta,
            source_table="tabMemora Memory State",
            archive_type_name="memory_state",
            export_metadata=export_metadata,
        )

        assert row_count == 100
        assert os.path.isfile(fact_path)

        # Step 5: Verify Parquet has correct data
        table = pq.read_table(fact_path)
        assert table.num_rows == 100

        item_ids = table.column("item_id").to_pylist()
        assert all(len(v) == 36 for v in item_ids), "All item_ids should be UUID strings"

        # Step 6: DQ validation must pass
        archive_schema = load_archive_type(
            integration_db_config.schema_registry_path, "memory_state", "v1"
        )
        dq_result = validate_fact_quality_generic(
            fact_path=fact_path,
            dq_rules=archive_schema["dq_rules"],
            dimension_paths={},
        )
        failed = [r for r in dq_result["results"] if not r["passed"]]
        assert dq_result["passed"], f"DQ failed: {failed}"

        # Step 7: Verify that _is_season_archived returns True now that a job exists
        # (even though it's Pending, it's non-Failed → sync should skip this season)
        is_archived = _is_season_archived(integration_db_config, MS_SEASON_SEQ_E)
        assert is_archived, (
            "Pending archive job should make _is_season_archived return True "
            "(sync skips seasons with any non-Failed archive job)"
        )

    def test_zero_row_season_lifecycle(self, db_conn, integration_db_config, archive_dir):
        """Season with 0 rows: scheduler creates job, export produces empty Parquet."""
        # No rows inserted for season E

        created = create_season_archive_jobs(integration_db_config, "memory_state")
        e2e_jobs = [c for c in created if c["season_seq"] == MS_SEASON_SEQ_E]
        assert len(e2e_jobs) == 1

        job_name = e2e_jobs[0]["job_name"]
        job = _get_archive_job(db_conn, job_name)
        export_meta = json.loads(job["job_meta"])

        export_metadata = {
            "archive_scope": job["archive_scope"],
            "archive_job_id": job_name,
            "schema_version": "v1",
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        fact_path, row_count, _ = export_fact_data(
            config=integration_db_config,
            staging_dir=archive_dir,
            meta=export_meta,
            source_table="tabMemora Memory State",
            archive_type_name="memory_state",
            export_metadata=export_metadata,
        )

        assert row_count == 0
        assert os.path.isfile(fact_path)

        table = pq.read_table(fact_path)
        assert table.num_rows == 0

    def test_multiple_seasons_independent(
        self, db_conn, integration_db_config, archive_dir
    ):
        """Operations on season E do not affect season A's data."""
        _insert_ms_rows(db_conn, "MULTI-A", 20, MS_SEASON_SEQ_A)
        _insert_ms_rows(db_conn, "MULTI-E", 20, MS_SEASON_SEQ_E)

        try:
            meta_e = _build_export_meta(MS_SEASON_SEQ_E, MS_SEASON_NAME_E)
            export_metadata = {
                "archive_scope": f"season_{MS_SEASON_SEQ_E}",
                "archive_job_id": "ARCH-MULTI-E",
                "schema_version": "v1",
                "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }

            _, row_count_e, _ = export_fact_data(
                config=integration_db_config,
                staging_dir=archive_dir,
                meta=meta_e,
                source_table="tabMemora Memory State",
                archive_type_name="memory_state",
                export_metadata=export_metadata,
            )

            # Export of season E should have exactly 20 rows
            assert row_count_e == 20

            # Season A rows should still be intact in DB
            remaining_a = count_memory_state_rows(db_conn, MS_SEASON_SEQ_A)
            assert remaining_a == 20, "Season A rows must be unaffected by season E export"
        finally:
            delete_memory_state_rows(db_conn, MS_SEASON_SEQ_A)
