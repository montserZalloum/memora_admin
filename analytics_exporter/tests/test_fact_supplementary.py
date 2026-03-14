"""Integration tests for supplementary dataset exports (Phase 6 / US3).

Covers T034 (single-file) and T035 (multi-file) datasets:

Single-file (T034):
  SP-*: fact_structure_progress (4 columns)
  PW-*: fact_player_wallet (7 columns)
  LS-*: dim_lesson_stage (6 columns, LEFT JOIN settings)
  CR-*: fact_content_report (8 columns)
  AJ-*: fact_archive_job (11 columns)

Multi-file (T035):
  LC-*: fact_live_challenge (event 9 cols + participation 7 cols, combined manifest)
  TR-*: fact_task_run (task_run_log 10 cols + build_queue 8 cols, combined manifest)

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python3 -m pytest analytics_exporter/tests/test_fact_supplementary.py -v
"""

import dataclasses
import json
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base: Config, output_dir: str, datasets: list[str]) -> Config:
    schema_path = os.path.join(os.path.dirname(__file__), "..", "schemas")
    return dataclasses.replace(
        base,
        analytics_output_path=output_dir,
        analytics_schema_path=str(os.path.abspath(schema_path)),
        analytics_datasets=datasets,
    )


def _make_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setLevel(logging.DEBUG)
        log.addHandler(h)
    log.setLevel(logging.DEBUG)
    return log


# ===========================================================================
# T034 — Single-file supplementary datasets
# ===========================================================================

# ---------------------------------------------------------------------------
# fact_structure_progress
# ---------------------------------------------------------------------------

SP_PREFIX = "TEST-SP-SUPP"

EXPECTED_SP_COLUMNS = {
    "player_id", "subject_id", "completion_pct", "passed_lessons_bitset",
}


def _insert_sp_rows(conn) -> None:
    sql = (
        "INSERT IGNORE INTO `tabMemora Structure Progress` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `player`, `subject`, `completion_percentage`, "
        " `passed_lessons_bitset`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s, "
        "        %s, %s, %s, %s)"
    )
    rows = [
        (f"{SP_PREFIX}-ROW-001", 1, f"{SP_PREFIX}-PLYR-001", f"{SP_PREFIX}-SUBJ-001", 0.75, "11001"),
        (f"{SP_PREFIX}-ROW-002", 2, f"{SP_PREFIX}-PLYR-001", f"{SP_PREFIX}-SUBJ-002", 0.50, "10100"),
        (f"{SP_PREFIX}-ROW-003", 3, f"{SP_PREFIX}-PLYR-002", f"{SP_PREFIX}-SUBJ-001", 1.00, "11111"),
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def _cleanup_sp_rows(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Structure Progress` WHERE `player` LIKE %s",
            (f"{SP_PREFIX}-%",),
        )
    conn.commit()


@pytest.mark.integration
def test_fact_structure_progress_columns(analytics_db_config, db_conn, tmp_path):
    """fact_structure_progress produces 4-column Parquet with correct columns."""
    _cleanup_sp_rows(db_conn)
    try:
        _insert_sp_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_structure_progress"])
        results = orchestrate_exports(cfg, _make_logger("test_sp"))

        assert "fact_structure_progress" in results
        result = results["fact_structure_progress"]
        assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

        out_path = os.path.join(str(tmp_path), "fact_structure_progress.parquet")
        table = pq.read_table(out_path)
        assert set(table.schema.names) == EXPECTED_SP_COLUMNS

        # Manifest written
        assert os.path.exists(os.path.join(str(tmp_path), "fact_structure_progress.manifest.json"))
    finally:
        _cleanup_sp_rows(db_conn)


@pytest.mark.integration
def test_fact_structure_progress_no_null_keys(analytics_db_config, db_conn, tmp_path):
    """No null player_id or subject_id in fact_structure_progress."""
    _cleanup_sp_rows(db_conn)
    try:
        _insert_sp_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_structure_progress"])
        results = orchestrate_exports(cfg, _make_logger("test_sp_nonull"))
        assert results["fact_structure_progress"].success

        table = pq.read_table(os.path.join(str(tmp_path), "fact_structure_progress.parquet"))
        player_ids = table.column("player_id").to_pylist()
        subject_ids = table.column("subject_id").to_pylist()

        # Verify our test rows are included
        sp_players = [p for p in player_ids if p and p.startswith(SP_PREFIX)]
        assert len(sp_players) >= 3

        # No nulls globally
        assert all(p is not None for p in player_ids), "Found null player_id"
        assert all(s is not None for s in subject_ids), "Found null subject_id"
    finally:
        _cleanup_sp_rows(db_conn)


# ---------------------------------------------------------------------------
# fact_player_wallet
# ---------------------------------------------------------------------------

PW_PREFIX = "TEST-PW-SUPP"

EXPECTED_PW_COLUMNS = {
    "player_id", "total_xp", "total_lessons", "total_time_min",
    "current_streak", "daily_xp_json", "last_sync_at",
}


def _insert_pw_rows(conn) -> None:
    sql = (
        "INSERT IGNORE INTO `tabMemora Player Wallet` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `player`, `total_xp`, `total_lessons`, "
        " `total_time_min`, `current_streak`, `daily_xp_json`, `last_sync_at`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s, "
        "        %s, %s, %s, %s, %s, %s, NOW())"
    )
    rows = [
        (f"{PW_PREFIX}-W-001", 1, f"{PW_PREFIX}-PLYR-001", 2500, 10, 120, 3,
         '{"2099-01-01": 100}'),
        (f"{PW_PREFIX}-W-002", 2, f"{PW_PREFIX}-PLYR-002", 750,  3,  45,  1,
         '{}'),
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def _cleanup_pw_rows(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Player Wallet` WHERE `player` LIKE %s",
            (f"{PW_PREFIX}-%",),
        )
    conn.commit()


@pytest.mark.integration
def test_fact_player_wallet_columns(analytics_db_config, db_conn, tmp_path):
    """fact_player_wallet produces 7-column Parquet with correct columns."""
    _cleanup_pw_rows(db_conn)
    try:
        _insert_pw_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_player_wallet"])
        results = orchestrate_exports(cfg, _make_logger("test_pw"))

        assert "fact_player_wallet" in results
        result = results["fact_player_wallet"]
        assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

        out_path = os.path.join(str(tmp_path), "fact_player_wallet.parquet")
        table = pq.read_table(out_path)
        assert set(table.schema.names) == EXPECTED_PW_COLUMNS

        # Verify our test players are present
        player_ids = table.column("player_id").to_pylist()
        assert f"{PW_PREFIX}-PLYR-001" in player_ids
        assert f"{PW_PREFIX}-PLYR-002" in player_ids

        # No null player_id
        assert all(p is not None for p in player_ids), "Found null player_id"

        # Manifest written
        assert os.path.exists(os.path.join(str(tmp_path), "fact_player_wallet.manifest.json"))
    finally:
        _cleanup_pw_rows(db_conn)


# ---------------------------------------------------------------------------
# dim_lesson_stage
# ---------------------------------------------------------------------------

LS_PREFIX = "TEST-LS-SUPP"

EXPECTED_LS_COLUMNS = {
    "stage_id", "lesson_id", "stage_type",
    "is_skippable", "default_stage_time", "is_time_calculated",
}

LS_LESSON_ID = f"{LS_PREFIX}-LESSON-001"


def _insert_ls_rows(conn) -> list[str]:
    """Insert lesson stage rows. Returns list of stage_ids inserted."""
    stage_ids = []
    stages = [
        (f"{LS_PREFIX}-STG-001", "FlashCard"),
        (f"{LS_PREFIX}-STG-002", "MultiChoice"),
        (f"{LS_PREFIX}-STG-003", "FillBlank"),
    ]
    for idx, (stage_id, stage_type) in enumerate(stages, 1):
        stage_name = f"{LS_LESSON_ID}-STGREC-{idx:03d}"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO `tabMemora Lesson Stage` "
                "(`name`, `stage_id`, `stage_type`, `is_skippable`, "
                " `parent`, `parentfield`, `parenttype`, "
                " `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
                "VALUES (%s, %s, %s, 0, %s, 'stages', 'Memora Lesson', "
                "        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s)",
                (stage_name, stage_id, stage_type, LS_LESSON_ID, idx),
            )
        stage_ids.append(stage_id)
    conn.commit()
    return stage_ids


def _cleanup_ls_rows(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Lesson Stage` WHERE `parent` = %s",
            (LS_LESSON_ID,),
        )
    conn.commit()


@pytest.mark.integration
def test_dim_lesson_stage_columns(analytics_db_config, db_conn, tmp_path):
    """dim_lesson_stage produces 6-column Parquet including LEFT JOIN settings columns."""
    _cleanup_ls_rows(db_conn)
    try:
        stage_ids = _insert_ls_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["dim_lesson_stage"])
        results = orchestrate_exports(cfg, _make_logger("test_ls"))

        assert "dim_lesson_stage" in results
        result = results["dim_lesson_stage"]
        assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

        out_path = os.path.join(str(tmp_path), "dim_lesson_stage.parquet")
        table = pq.read_table(out_path)
        assert set(table.schema.names) == EXPECTED_LS_COLUMNS

        # Verify test stages are in output
        all_stage_ids = table.column("stage_id").to_pylist()
        for sid in stage_ids:
            assert sid in all_stage_ids, f"{sid} not found in output"

        # No null stage_id or lesson_id
        assert all(s is not None for s in all_stage_ids), "Found null stage_id"
        lesson_ids = table.column("lesson_id").to_pylist()
        assert all(l is not None for l in lesson_ids), "Found null lesson_id"

        # Settings columns are present (nullable — null since no settings inserted)
        assert "default_stage_time" in table.schema.names
        assert "is_time_calculated" in table.schema.names

        # Manifest written
        assert os.path.exists(os.path.join(str(tmp_path), "dim_lesson_stage.manifest.json"))
    finally:
        _cleanup_ls_rows(db_conn)


# ---------------------------------------------------------------------------
# fact_content_report
# ---------------------------------------------------------------------------

CR_PREFIX = "TEST-CR-SUPP"

EXPECTED_CR_COLUMNS = {
    "player_id", "subject_id", "lesson_id", "report_type",
    "description", "status", "created_at", "resolved_at",
}


def _insert_cr_rows(conn) -> None:
    sql = (
        "INSERT IGNORE INTO `tabMemora Content Report` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `player`, `subject`, `lesson`, "
        " `report_type`, `description`, `status`) "
        "VALUES (%s, '2099-06-01 10:00:00', '2099-06-01 10:00:00', "
        "        'test@test.com', 'test@test.com', 0, %s, "
        "        %s, %s, %s, %s, %s, %s)"
    )
    rows = [
        (f"{CR_PREFIX}-RPT-001", 1,
         f"{CR_PREFIX}-PLYR-001", f"{CR_PREFIX}-SUBJ-001", f"{CR_PREFIX}-LSSN-001",
         "Incorrect Answer", "The correct answer is wrong", "Open"),
        (f"{CR_PREFIX}-RPT-002", 2,
         f"{CR_PREFIX}-PLYR-002", f"{CR_PREFIX}-SUBJ-001", None,
         "Missing Content", "Stage has no content", "Resolved"),
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def _cleanup_cr_rows(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Content Report` WHERE `name` LIKE %s",
            (f"{CR_PREFIX}-%",),
        )
    conn.commit()


@pytest.mark.integration
def test_fact_content_report_columns(analytics_db_config, db_conn, tmp_path):
    """fact_content_report produces 8-column Parquet with correct columns."""
    _cleanup_cr_rows(db_conn)
    try:
        _insert_cr_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_content_report"])
        results = orchestrate_exports(cfg, _make_logger("test_cr"))

        assert "fact_content_report" in results
        result = results["fact_content_report"]
        assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

        out_path = os.path.join(str(tmp_path), "fact_content_report.parquet")
        table = pq.read_table(out_path)
        assert set(table.schema.names) == EXPECTED_CR_COLUMNS

        # No null player_id
        player_ids = table.column("player_id").to_pylist()
        assert all(p is not None for p in player_ids), "Found null player_id"

        # Our test rows present
        cr_players = [p for p in player_ids if p and p.startswith(CR_PREFIX)]
        assert len(cr_players) >= 2

        # Manifest written
        assert os.path.exists(os.path.join(str(tmp_path), "fact_content_report.manifest.json"))
    finally:
        _cleanup_cr_rows(db_conn)


# ---------------------------------------------------------------------------
# fact_archive_job
# ---------------------------------------------------------------------------

AJ_PREFIX = "TEST-AJ-SUPP"

EXPECTED_AJ_COLUMNS = {
    "job_id", "source_doctype", "status", "archive_scope",
    "started_at", "completed_at", "duration_seconds",
    "row_count", "file_size_bytes", "retry_count", "error_log",
}


def _insert_aj_rows(conn) -> None:
    sql = (
        "INSERT IGNORE INTO `tabMemora Archive Job` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
        " `status`, `priority`, `retry_count`, `post_archive_action`, "
        " `source_deleted`, `sync_paused`, "
        " `duration_seconds`, `row_count`, `file_size_bytes`, "
        " `file_path`, `job_meta`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0, "
        "        %s, %s, 'v1', 'practice_log', "
        "        'Completed', 'Normal', 0, 'None', 0, 0, "
        "        120, 500, 10240, "
        "        '/tmp/test', '{}')"
    )
    rows = [
        (f"{AJ_PREFIX}-001", "Memora Practice Log", f"{AJ_PREFIX}-SCOPE-001"),
        (f"{AJ_PREFIX}-002", "Memora Interaction Log", f"{AJ_PREFIX}-SCOPE-002"),
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def _cleanup_aj_rows(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Archive Job` WHERE `name` LIKE %s",
            (f"{AJ_PREFIX}-%",),
        )
    conn.commit()


@pytest.mark.integration
def test_fact_archive_job_columns(analytics_db_config, db_conn, tmp_path):
    """fact_archive_job produces 11-column Parquet with correct columns."""
    _cleanup_aj_rows(db_conn)
    try:
        _insert_aj_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_archive_job"])
        results = orchestrate_exports(cfg, _make_logger("test_aj"))

        assert "fact_archive_job" in results
        result = results["fact_archive_job"]
        assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

        out_path = os.path.join(str(tmp_path), "fact_archive_job.parquet")
        table = pq.read_table(out_path)
        assert set(table.schema.names) == EXPECTED_AJ_COLUMNS

        # Test jobs present in output
        job_ids = table.column("job_id").to_pylist()
        assert f"{AJ_PREFIX}-001" in job_ids
        assert f"{AJ_PREFIX}-002" in job_ids

        # No null job_id or source_doctype
        assert all(j is not None for j in job_ids), "Found null job_id"
        source_dtypes = table.column("source_doctype").to_pylist()
        assert all(s is not None for s in source_dtypes), "Found null source_doctype"

        # Manifest written
        assert os.path.exists(os.path.join(str(tmp_path), "fact_archive_job.manifest.json"))
    finally:
        _cleanup_aj_rows(db_conn)


# ===========================================================================
# T035 — Multi-file supplementary datasets
# ===========================================================================

# ---------------------------------------------------------------------------
# fact_live_challenge (event + participation)
# ---------------------------------------------------------------------------

LC_PREFIX = "TEST-LC-SUPP"

EXPECTED_LC_EVENT_COLUMNS = {
    "event_id", "event_name", "status", "scheduled_start",
    "exam_duration", "capacity", "participant_count", "submitted_count", "is_paid",
}

EXPECTED_LC_PARTICIPATION_COLUMNS = {
    "event_id", "player_id", "joined_at", "submitted_at",
    "score", "rank", "xp_awarded",
}


def _insert_lc_rows(conn) -> str:
    """Insert test live challenge event + participation rows. Returns event_id."""
    event_id = f"{LC_PREFIX}-EVT-001"

    with conn.cursor() as cur:
        cur.execute(
            "INSERT IGNORE INTO `tabMemora Live Challenge Event` "
            "(`name`, `event_name`, `status`, `scheduled_start`, "
            " `exam_duration`, `capacity`, `participant_count`, `submitted_count`, `is_paid`, "
            " `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
            "VALUES (%s, %s, 'Completed', '2099-07-01 10:00:00', "
            "        60, 100, 5, 5, 0, "
            "        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1)",
            (event_id, f"Test Live Challenge {LC_PREFIX}"),
        )
        # Insert participation rows
        for n in range(1, 3):
            part_name = f"{LC_PREFIX}-PART-{n:03d}"
            cur.execute(
                "INSERT IGNORE INTO `tabMemora Live Challenge Participation` "
                "(`name`, `event`, `player`, `joined_at`, `submitted_at`, "
                " `score`, `rank`, `xp_awarded`, "
                " `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
                "VALUES (%s, %s, %s, '2099-07-01 10:05:00', '2099-07-01 11:05:00', "
                "        %s, %s, %s, "
                "        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s)",
                (
                    part_name,
                    event_id,
                    f"{LC_PREFIX}-PLYR-{n:03d}",
                    90 - (n * 10),   # score
                    n,               # rank
                    50 - (n * 5),    # xp_awarded
                    n,               # idx
                ),
            )
    conn.commit()
    return event_id


def _cleanup_lc_rows(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Live Challenge Participation` WHERE `event` LIKE %s",
            (f"{LC_PREFIX}-%",),
        )
        cur.execute(
            "DELETE FROM `tabMemora Live Challenge Event` WHERE `name` LIKE %s",
            (f"{LC_PREFIX}-%",),
        )
    conn.commit()


@pytest.mark.integration
def test_fact_live_challenge_multi_file_export(analytics_db_config, db_conn, tmp_path):
    """fact_live_challenge exports two Parquet files (event 9 cols, participation 7 cols)."""
    _cleanup_lc_rows(db_conn)
    try:
        event_id = _insert_lc_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_live_challenge"])
        results = orchestrate_exports(cfg, _make_logger("test_lc"))

        assert "fact_live_challenge_event" in results
        assert "fact_live_challenge_participation" in results

        evt_result = results["fact_live_challenge_event"]
        part_result = results["fact_live_challenge_participation"]

        assert evt_result.success, f"Event export failed: {evt_result.error}"
        assert part_result.success, f"Participation export failed: {part_result.error}"

        evt_path = os.path.join(str(tmp_path), "fact_live_challenge_event.parquet")
        part_path = os.path.join(str(tmp_path), "fact_live_challenge_participation.parquet")

        assert os.path.exists(evt_path), "fact_live_challenge_event.parquet not found"
        assert os.path.exists(part_path), "fact_live_challenge_participation.parquet not found"

        # Column counts and names
        evt_table = pq.read_table(evt_path)
        part_table = pq.read_table(part_path)

        assert set(evt_table.schema.names) == EXPECTED_LC_EVENT_COLUMNS, (
            f"Event columns mismatch: {set(evt_table.schema.names)}"
        )
        assert set(part_table.schema.names) == EXPECTED_LC_PARTICIPATION_COLUMNS, (
            f"Participation columns mismatch: {set(part_table.schema.names)}"
        )

        # Test data present
        event_ids_in_evt = evt_table.column("event_id").to_pylist()
        assert event_id in event_ids_in_evt

        event_ids_in_part = part_table.column("event_id").to_pylist()
        lc_part_events = [e for e in event_ids_in_part if e == event_id]
        assert len(lc_part_events) >= 2
    finally:
        _cleanup_lc_rows(db_conn)


@pytest.mark.integration
def test_fact_live_challenge_combined_manifest(analytics_db_config, db_conn, tmp_path):
    """fact_live_challenge combined manifest has both files in files array."""
    _cleanup_lc_rows(db_conn)
    try:
        _insert_lc_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_live_challenge"])
        results = orchestrate_exports(cfg, _make_logger("test_lc_manifest"))
        assert results["fact_live_challenge_event"].success
        assert results["fact_live_challenge_participation"].success

        manifest_path = os.path.join(str(tmp_path), "fact_live_challenge.manifest.json")
        assert os.path.exists(manifest_path), "fact_live_challenge.manifest.json not found"

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert "files" in manifest
        assert len(manifest["files"]) == 2, (
            f"Expected 2 files in manifest, got {len(manifest['files'])}"
        )

        filenames = {entry["filename"] for entry in manifest["files"]}
        assert "fact_live_challenge_event.parquet" in filenames
        assert "fact_live_challenge_participation.parquet" in filenames

        for entry in manifest["files"]:
            assert "row_count" in entry
            assert "checksum" in entry
            assert entry["checksum"].startswith("sha256:")
            assert "size_bytes" in entry
            assert entry["size_bytes"] > 0
    finally:
        _cleanup_lc_rows(db_conn)


# ---------------------------------------------------------------------------
# fact_task_run (task_run_log + build_queue)
# ---------------------------------------------------------------------------

TR_PREFIX = "TEST-TR-SUPP"
BQ_PREFIX = "TEST-BQ-SUPP"

EXPECTED_TRL_COLUMNS = {
    "task_name", "run_date", "started_at", "completed_at",
    "duration_sec", "status", "triggered_by",
    "processed_count", "failed_count", "error_message",
}

EXPECTED_BQ_COLUMNS = {
    "target_type", "target_name", "status",
    "started_at", "completed_at", "duration_sec",
    "files_generated", "trigger_reason",
}


def _insert_tr_rows(conn) -> None:
    """Insert test task run log rows."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Task Run Log` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `task_name`, `run_date`, `started_at`, `completed_at`, "
        " `duration_sec`, `status`, `triggered_by`, `processed_count`, `failed_count`) "
        "VALUES (%s, NOW(), NOW(), 'test', 'test', 0, %s, "
        "        %s, %s, %s, %s, 5, 'Completed', 'Scheduler', 10, 0)"
    )
    rows = [
        (f"{TR_PREFIX}-001", 1,
         f"sync_task_{TR_PREFIX}_1", "2099-07-01",
         "2099-07-01 00:00:00", "2099-07-01 00:00:05"),
        (f"{TR_PREFIX}-002", 2,
         f"sync_task_{TR_PREFIX}_2", "2099-07-02",
         "2099-07-02 00:00:00", "2099-07-02 00:00:05"),
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def _insert_bq_rows(conn) -> None:
    """Insert test build queue rows."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Build Queue` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `target_type`, `target_name`, `status`, "
        " `started_at`, `completed_at`, `duration_sec`, "
        " `files_generated`, `trigger_reason`) "
        "VALUES (%s, NOW(), NOW(), 'test', 'test', 0, %s, "
        "        %s, %s, 'Completed', "
        "        '2099-07-01 01:00:00', '2099-07-01 01:00:10', 10, "
        "        3, %s)"
    )
    rows = [
        (f"{BQ_PREFIX}-001", 1, "AcademicPlan", f"{BQ_PREFIX}-PLAN-001", "PlayerSync"),
        (f"{BQ_PREFIX}-002", 2, "Subject",      f"{BQ_PREFIX}-SUBJ-001", "ContentUpdate"),
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def _cleanup_tr_rows(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Task Run Log` WHERE `name` LIKE %s",
            (f"{TR_PREFIX}-%",),
        )
    conn.commit()


def _cleanup_bq_rows(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Build Queue` WHERE `name` LIKE %s",
            (f"{BQ_PREFIX}-%",),
        )
    conn.commit()


@pytest.mark.integration
def test_fact_task_run_multi_file_export(analytics_db_config, db_conn, tmp_path):
    """fact_task_run exports two Parquet files (task_run_log 10 cols, build_queue 8 cols)."""
    _cleanup_tr_rows(db_conn)
    _cleanup_bq_rows(db_conn)
    try:
        _insert_tr_rows(db_conn)
        _insert_bq_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_task_run"])
        results = orchestrate_exports(cfg, _make_logger("test_tr"))

        assert "fact_task_run_log" in results
        assert "fact_build_queue" in results

        trl_result = results["fact_task_run_log"]
        bq_result = results["fact_build_queue"]

        assert trl_result.success, f"Task run log export failed: {trl_result.error}"
        assert bq_result.success, f"Build queue export failed: {bq_result.error}"

        trl_path = os.path.join(str(tmp_path), "fact_task_run_log.parquet")
        bq_path = os.path.join(str(tmp_path), "fact_build_queue.parquet")

        assert os.path.exists(trl_path), "fact_task_run_log.parquet not found"
        assert os.path.exists(bq_path), "fact_build_queue.parquet not found"

        trl_table = pq.read_table(trl_path)
        bq_table = pq.read_table(bq_path)

        assert set(trl_table.schema.names) == EXPECTED_TRL_COLUMNS, (
            f"Task run log columns mismatch: {set(trl_table.schema.names)}"
        )
        assert set(bq_table.schema.names) == EXPECTED_BQ_COLUMNS, (
            f"Build queue columns mismatch: {set(bq_table.schema.names)}"
        )

        # Test data present
        task_names = trl_table.column("task_name").to_pylist()
        tr_tasks = [t for t in task_names if t and t.startswith(f"sync_task_{TR_PREFIX}")]
        assert len(tr_tasks) >= 2

        target_names = bq_table.column("target_name").to_pylist()
        bq_targets = [t for t in target_names if t and t.startswith(BQ_PREFIX)]
        assert len(bq_targets) >= 2
    finally:
        _cleanup_tr_rows(db_conn)
        _cleanup_bq_rows(db_conn)


@pytest.mark.integration
def test_fact_task_run_combined_manifest(analytics_db_config, db_conn, tmp_path):
    """fact_task_run combined manifest has both files in files array."""
    _cleanup_tr_rows(db_conn)
    _cleanup_bq_rows(db_conn)
    try:
        _insert_tr_rows(db_conn)
        _insert_bq_rows(db_conn)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_task_run"])
        results = orchestrate_exports(cfg, _make_logger("test_tr_manifest"))
        assert results["fact_task_run_log"].success
        assert results["fact_build_queue"].success

        manifest_path = os.path.join(str(tmp_path), "fact_task_run.manifest.json")
        assert os.path.exists(manifest_path), "fact_task_run.manifest.json not found"

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert "files" in manifest
        assert len(manifest["files"]) == 2, (
            f"Expected 2 files in manifest, got {len(manifest['files'])}"
        )

        filenames = {entry["filename"] for entry in manifest["files"]}
        assert "fact_task_run_log.parquet" in filenames
        assert "fact_build_queue.parquet" in filenames

        for entry in manifest["files"]:
            assert "row_count" in entry
            assert "checksum" in entry
            assert entry["checksum"].startswith("sha256:")
            assert "size_bytes" in entry
            assert entry["size_bytes"] > 0
    finally:
        _cleanup_tr_rows(db_conn)
        _cleanup_bq_rows(db_conn)
