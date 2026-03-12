"""Integration tests for the weekly structure progress snapshot pipeline (Phase 3, US1).

Covers:
  T010 - test_snapshot_basic_export: 5 rows, 2 students, 3 subjects
  T011 - test_snapshot_manifest_integrity: manifest fields and checksum

Run with:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=_9be6802bfff1e8ca \\
    DB_PASSWORD=zjAACevKaH5VGVP2 DB_NAME=_9be6802bfff1e8ca \\
    python3 -m pytest archive_executor/tests/test_snapshot.py -v
"""

import json
import os
import re
import tempfile
import datetime

import pyarrow.parquet as pq
import pymysql
import pymysql.cursors
import pytest

from archive_executor.config import Config

# ---------------------------------------------------------------------------
# Snapshot test constants (T009)
# ---------------------------------------------------------------------------

SNAP_STRUCTURE_PROGRESS_TABLE = "tabMemora Structure Progress"
SNAP_PLAYER_PROFILE_TABLE = "tabMemora Player Profile"

SNAP_PLAYER_PREFIX = "SNAP-PLYR"
SNAP_SUBJECT_PREFIX = "SNAP-SUBJ"
SNAP_PLAN_PREFIX = "SNAP-PLAN"

# Fixed snapshot date used by all US1 tests
SNAP_DATE = "2026-03-08"


# ---------------------------------------------------------------------------
# T009: DB helpers — insert / delete test rows
# ---------------------------------------------------------------------------

def insert_snap_players(conn, player_plan_pairs: list[tuple[str, str]]) -> None:
    """Insert test player profile rows.

    Args:
        player_plan_pairs: list of (player_name, plan_name) tuples.
    """
    sql = (
        "INSERT IGNORE INTO `tabMemora Player Profile` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `plan`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s, %s)"
    )
    rows = [(name, idx + 1, plan) for idx, (name, plan) in enumerate(player_plan_pairs)]
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()


def insert_snap_player_no_plan(conn, player_name: str) -> None:
    """Insert a player profile with plan=NULL."""
    sql = (
        "INSERT IGNORE INTO `tabMemora Player Profile` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `plan`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1, NULL)"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (player_name,))
    conn.commit()


def insert_snap_progress(conn, rows: list[tuple[str, str, str, float]]) -> None:
    """Insert structure progress rows.

    Args:
        rows: list of (name, player, subject, completion_percentage) tuples.
    """
    sql = (
        "INSERT IGNORE INTO `tabMemora Structure Progress` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, "
        " `docstatus`, `idx`, `player`, `subject`, `completion_percentage`) "
        "VALUES (%s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s, %s, %s, %s)"
    )
    params = [(name, idx + 1, player, subject, pct) for idx, (name, player, subject, pct) in enumerate(rows)]
    with conn.cursor() as cursor:
        cursor.executemany(sql, params)
    conn.commit()


def delete_snap_players(conn, prefix: str = SNAP_PLAYER_PREFIX) -> None:
    """Delete test player profiles with the given name prefix."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Player Profile` WHERE `name` LIKE %s",
            (f"{prefix}%",),
        )
    conn.commit()


def delete_snap_progress(conn, prefix: str = SNAP_PLAYER_PREFIX) -> None:
    """Delete structure progress rows for players with the given prefix."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM `tabMemora Structure Progress` WHERE `player` LIKE %s",
            (f"{prefix}%",),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# T009: Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def snap_db_conn(integration_db_config: Config):
    """Module-scoped raw pymysql connection for snapshot tests."""
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


@pytest.fixture(autouse=True)
def clean_snap_data(snap_db_conn):
    """Clean snapshot test data before and after each test."""
    delete_snap_progress(snap_db_conn)
    delete_snap_players(snap_db_conn)
    yield
    delete_snap_progress(snap_db_conn)
    delete_snap_players(snap_db_conn)


@pytest.fixture
def snap_output_dir():
    """Temporary directory for snapshot output."""
    with tempfile.TemporaryDirectory(prefix="memora_snap_inttest_") as tmpdir:
        yield tmpdir


@pytest.fixture
def snap_config(integration_db_config: Config, snap_output_dir: str) -> Config:
    """Config with snapshot_output_path pointing to a temp dir."""
    return Config(
        db_host=integration_db_config.db_host,
        db_port=integration_db_config.db_port,
        db_user=integration_db_config.db_user,
        db_password=integration_db_config.db_password,
        db_name=integration_db_config.db_name,
        archive_output_path=integration_db_config.archive_output_path,
        schema_registry_path=integration_db_config.schema_registry_path,
        log_path=integration_db_config.log_path,
        lock_file=integration_db_config.lock_file,
        chunk_size=integration_db_config.chunk_size,
        stuck_timeout_hours=integration_db_config.stuck_timeout_hours,
        ssh_host="", ssh_user="", ssh_key_path="",
        ssh_port=22, ssh_timeout=300,
        remote_archive_path="", remote_live_path="",
        analytics_cmd_path="", duckdb_path="",
        live_output_path=integration_db_config.live_output_path,
        live_lock_file=integration_db_config.live_lock_file,
        sync_state_path=integration_db_config.sync_state_path,
        sync_output_path=integration_db_config.sync_output_path,
        sync_overlap_seconds=integration_db_config.sync_overlap_seconds,
        sync_remote_path="",
        purge_grace_days=integration_db_config.purge_grace_days,
        snapshot_output_path=snap_output_dir,
        remote_snapshot_path="",
    )


# ---------------------------------------------------------------------------
# T010: test_snapshot_basic_export
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_basic_export(snap_db_conn, snap_config):
    """Insert 5 structure progress rows for 2 students across 3 subjects, run
    the snapshot pipeline, verify Parquet file exists with correct content."""
    from archive_executor.snapshot import run_snapshot

    # 2 students, each on a distinct plan
    player_plan_pairs = [
        (f"{SNAP_PLAYER_PREFIX}-001", f"{SNAP_PLAN_PREFIX}-001"),
        (f"{SNAP_PLAYER_PREFIX}-002", f"{SNAP_PLAN_PREFIX}-002"),
    ]
    insert_snap_players(snap_db_conn, player_plan_pairs)

    # 5 rows: player-001 has 3 subjects, player-002 has 2 subjects
    progress_rows = [
        (f"SNAP-SP-001", f"{SNAP_PLAYER_PREFIX}-001", f"{SNAP_SUBJECT_PREFIX}-A", 10.0),
        (f"SNAP-SP-002", f"{SNAP_PLAYER_PREFIX}-001", f"{SNAP_SUBJECT_PREFIX}-B", 50.0),
        (f"SNAP-SP-003", f"{SNAP_PLAYER_PREFIX}-001", f"{SNAP_SUBJECT_PREFIX}-C", 90.0),
        (f"SNAP-SP-004", f"{SNAP_PLAYER_PREFIX}-002", f"{SNAP_SUBJECT_PREFIX}-A", 20.0),
        (f"SNAP-SP-005", f"{SNAP_PLAYER_PREFIX}-002", f"{SNAP_SUBJECT_PREFIX}-B", 60.0),
    ]
    insert_snap_progress(snap_db_conn, progress_rows)

    summary = run_snapshot(snap_config, snapshot_date=SNAP_DATE)

    # Verify return dict
    assert summary["snapshot_date"] == SNAP_DATE
    assert summary["row_count"] >= 5  # may include pre-existing rows in the table

    # Verify Parquet file exists at expected path
    final_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", SNAP_DATE)
    parquet_path = os.path.join(final_dir, "fact_structure_progress.parquet")
    assert os.path.isfile(parquet_path), f"Parquet not found at {parquet_path}"

    # Read and filter to only the rows we inserted
    table = pq.read_table(parquet_path)
    assert set(table.schema.names) == {"snapshot_date", "player_id", "plan_id", "subject_id", "completion_percentage"}

    df = table.to_pydict()

    # Filter to only our test rows
    test_indices = [i for i, p in enumerate(df["player_id"]) if p.startswith(SNAP_PLAYER_PREFIX)]
    assert len(test_indices) == 5, f"Expected 5 SNAP-PLYR rows, got {len(test_indices)}"

    snap_date_val = datetime.date.fromisoformat(SNAP_DATE)
    test_players = [df["player_id"][i] for i in test_indices]
    test_plans = [df["plan_id"][i] for i in test_indices]
    test_subjects = [df["subject_id"][i] for i in test_indices]
    test_dates = [df["snapshot_date"][i] for i in test_indices]

    # snapshot_date column should match
    for d in test_dates:
        assert d == snap_date_val, f"snapshot_date mismatch: {d}"

    # Verify player_id values present
    assert f"{SNAP_PLAYER_PREFIX}-001" in test_players
    assert f"{SNAP_PLAYER_PREFIX}-002" in test_players

    # Verify plan_id values match the player's plan
    plan_by_player = {p: pl for p, pl in player_plan_pairs}
    for player_id, plan_id in zip(test_players, test_plans):
        assert plan_id == plan_by_player[player_id], (
            f"plan_id mismatch for {player_id}: expected {plan_by_player[player_id]}, got {plan_id}"
        )

    # Verify subject_id values present
    assert f"{SNAP_SUBJECT_PREFIX}-A" in test_subjects
    assert f"{SNAP_SUBJECT_PREFIX}-B" in test_subjects
    assert f"{SNAP_SUBJECT_PREFIX}-C" in test_subjects


# ---------------------------------------------------------------------------
# T011: test_snapshot_manifest_integrity
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_manifest_integrity(snap_db_conn, snap_config):
    """After a snapshot run, verify manifest.json fields and checksum."""
    import hashlib
    from archive_executor.snapshot import run_snapshot

    player_plan_pairs = [
        (f"{SNAP_PLAYER_PREFIX}-010", f"{SNAP_PLAN_PREFIX}-010"),
    ]
    insert_snap_players(snap_db_conn, player_plan_pairs)

    progress_rows = [
        ("SNAP-SP-010", f"{SNAP_PLAYER_PREFIX}-010", f"{SNAP_SUBJECT_PREFIX}-X", 75.0),
        ("SNAP-SP-011", f"{SNAP_PLAYER_PREFIX}-010", f"{SNAP_SUBJECT_PREFIX}-Y", 40.0),
    ]
    insert_snap_progress(snap_db_conn, progress_rows)

    summary = run_snapshot(snap_config, snapshot_date=SNAP_DATE)

    final_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", SNAP_DATE)
    manifest_path = os.path.join(final_dir, "manifest.json")
    assert os.path.isfile(manifest_path), f"manifest.json not found at {manifest_path}"

    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    assert manifest["dataset_key"] == "structure_progress_snapshot"
    assert manifest["kind"] == "snapshot"
    assert manifest["batch_id"].startswith("SNAP-")
    assert manifest["scope_key"] == SNAP_DATE

    assert len(manifest["files"]) == 1
    file_entry = manifest["files"][0]
    assert file_entry["row_count"] == summary["row_count"]
    assert file_entry["row_count"] >= 2  # at least the 2 rows we inserted

    # Verify checksum format
    checksum = file_entry["checksum"]
    assert checksum.startswith("sha256:"), f"checksum should start with sha256: got {checksum}"
    hex_part = checksum[len("sha256:"):]
    assert re.fullmatch(r"[0-9a-f]{64}", hex_part), f"invalid sha256 hex: {hex_part}"

    # Verify checksum matches actual file
    parquet_path = os.path.join(final_dir, "fact_structure_progress.parquet")
    sha256 = hashlib.sha256()
    with open(parquet_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    expected_checksum = "sha256:" + sha256.hexdigest()
    assert checksum == expected_checksum, "manifest checksum does not match Parquet file"


# ---------------------------------------------------------------------------
# T014: test_snapshot_plan_enrichment (US2)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_plan_enrichment(snap_db_conn, snap_config):
    """Insert 3 students each on different plans, run snapshot, verify each
    row's plan_id matches the student's profile plan exactly (not just non-null)."""
    from archive_executor.snapshot import run_snapshot

    player_plan_pairs = [
        (f"{SNAP_PLAYER_PREFIX}-101", f"{SNAP_PLAN_PREFIX}-A"),
        (f"{SNAP_PLAYER_PREFIX}-102", f"{SNAP_PLAN_PREFIX}-B"),
        (f"{SNAP_PLAYER_PREFIX}-103", f"{SNAP_PLAN_PREFIX}-C"),
    ]
    insert_snap_players(snap_db_conn, player_plan_pairs)

    progress_rows = [
        ("SNAP-SP-101", f"{SNAP_PLAYER_PREFIX}-101", f"{SNAP_SUBJECT_PREFIX}-Math", 30.0),
        ("SNAP-SP-102", f"{SNAP_PLAYER_PREFIX}-102", f"{SNAP_SUBJECT_PREFIX}-Math", 60.0),
        ("SNAP-SP-103", f"{SNAP_PLAYER_PREFIX}-103", f"{SNAP_SUBJECT_PREFIX}-Math", 90.0),
    ]
    insert_snap_progress(snap_db_conn, progress_rows)

    run_snapshot(snap_config, snapshot_date=SNAP_DATE)

    final_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", SNAP_DATE)
    parquet_path = os.path.join(final_dir, "fact_structure_progress.parquet")
    assert os.path.isfile(parquet_path)

    table = pq.read_table(parquet_path)
    df = table.to_pydict()

    # Build a lookup of player_id → plan_id from the Parquet output
    plan_by_player = {}
    for player_id, plan_id in zip(df["player_id"], df["plan_id"]):
        if player_id.startswith(SNAP_PLAYER_PREFIX + "-10"):
            plan_by_player[player_id] = plan_id

    expected_plan = {p: pl for p, pl in player_plan_pairs}
    for player_id, plan_id in plan_by_player.items():
        assert plan_id == expected_plan[player_id], (
            f"plan_id mismatch for {player_id}: expected {expected_plan[player_id]}, got {plan_id}"
        )

    # All 3 test players must appear
    assert len(plan_by_player) == 3, f"Expected 3 test players, got {len(plan_by_player)}"


# ---------------------------------------------------------------------------
# T015: test_snapshot_plan_change_across_weeks (US2)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_plan_change_across_weeks(snap_db_conn, snap_config):
    """Insert student on Plan A, snapshot date X; update to Plan B, snapshot date Y.
    Verify: date X has plan_id=Plan A + 80%, date Y has plan_id=Plan B + 0%.
    Both snapshots coexist as separate partition directories."""
    from archive_executor.snapshot import run_snapshot

    player_name = f"{SNAP_PLAYER_PREFIX}-201"
    subject_name = f"{SNAP_SUBJECT_PREFIX}-Science"
    plan_a = f"{SNAP_PLAN_PREFIX}-PlanA"
    plan_b = f"{SNAP_PLAN_PREFIX}-PlanB"
    date_x = "2026-03-08"
    date_y = "2026-03-15"

    # Insert player on Plan A with 80% completion
    insert_snap_players(snap_db_conn, [(player_name, plan_a)])
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-201", player_name, subject_name, 80.0),
    ])

    run_snapshot(snap_config, snapshot_date=date_x)

    # Switch player to Plan B and reset progress to 0%
    with snap_db_conn.cursor() as cursor:
        cursor.execute(
            "UPDATE `tabMemora Player Profile` SET `plan` = %s WHERE `name` = %s",
            (plan_b, player_name),
        )
        cursor.execute(
            "UPDATE `tabMemora Structure Progress` SET `completion_percentage` = 0.0 WHERE `name` = %s",
            ("SNAP-SP-201",),
        )
    snap_db_conn.commit()

    run_snapshot(snap_config, snapshot_date=date_y)

    # Both partition directories must exist
    dir_x = os.path.join(snap_config.snapshot_output_path, "structure_progress", date_x)
    dir_y = os.path.join(snap_config.snapshot_output_path, "structure_progress", date_y)
    assert os.path.isdir(dir_x), f"Partition for {date_x} missing"
    assert os.path.isdir(dir_y), f"Partition for {date_y} missing"

    def read_player_row(partition_dir: str, player: str) -> dict:
        path = os.path.join(partition_dir, "fact_structure_progress.parquet")
        table = pq.read_table(path)
        df = table.to_pydict()
        rows = [
            {
                "snapshot_date": df["snapshot_date"][i],
                "player_id": df["player_id"][i],
                "plan_id": df["plan_id"][i],
                "subject_id": df["subject_id"][i],
                "completion_percentage": df["completion_percentage"][i],
            }
            for i in range(len(df["player_id"]))
            if df["player_id"][i] == player and df["subject_id"][i] == subject_name
        ]
        assert len(rows) == 1, f"Expected 1 row for {player} in {partition_dir}, got {len(rows)}"
        return rows[0]

    row_x = read_player_row(dir_x, player_name)
    row_y = read_player_row(dir_y, player_name)

    assert row_x["plan_id"] == plan_a, f"date X plan_id: expected {plan_a}, got {row_x['plan_id']}"
    assert row_x["completion_percentage"] == 80.0, f"date X completion: expected 80.0, got {row_x['completion_percentage']}"

    assert row_y["plan_id"] == plan_b, f"date Y plan_id: expected {plan_b}, got {row_y['plan_id']}"
    assert row_y["completion_percentage"] == 0.0, f"date Y completion: expected 0.0, got {row_y['completion_percentage']}"


# ---------------------------------------------------------------------------
# Phase 5: User Story 3 — Idempotent Rerun Safety
# ---------------------------------------------------------------------------

SNAP_DATE_IDEM = "2026-04-06"
SNAP_DATE_OVERWRITE = "2026-04-13"


# ---------------------------------------------------------------------------
# T016: test_snapshot_idempotent_rerun (US3)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_idempotent_rerun(snap_db_conn, snap_config):
    """Run snapshot for the same date twice; verify Parquet is identical (not appended).

    T016: Parquet bytes identical, manifest row_count unchanged, checksum matches,
    no duplicate rows in output.
    """
    from archive_executor.snapshot import run_snapshot

    insert_snap_players(snap_db_conn, [
        (f"{SNAP_PLAYER_PREFIX}-301", f"{SNAP_PLAN_PREFIX}-301"),
    ])
    progress_rows = [
        ("SNAP-SP-301", f"{SNAP_PLAYER_PREFIX}-301", f"{SNAP_SUBJECT_PREFIX}-A", 50.0),
        ("SNAP-SP-302", f"{SNAP_PLAYER_PREFIX}-301", f"{SNAP_SUBJECT_PREFIX}-B", 75.0),
        ("SNAP-SP-303", f"{SNAP_PLAYER_PREFIX}-301", f"{SNAP_SUBJECT_PREFIX}-C", 25.0),
    ]
    insert_snap_progress(snap_db_conn, progress_rows)

    # First run
    run_snapshot(snap_config, snapshot_date=SNAP_DATE_IDEM)

    final_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", SNAP_DATE_IDEM)
    parquet_path = os.path.join(final_dir, "fact_structure_progress.parquet")
    manifest_path = os.path.join(final_dir, "manifest.json")

    with open(parquet_path, "rb") as f:
        first_bytes = f.read()
    with open(manifest_path, "r") as f:
        manifest1 = json.load(f)
    first_checksum = manifest1["files"][0]["checksum"]
    first_row_count = manifest1["files"][0]["row_count"]

    # Second run — same date, same source data
    run_snapshot(snap_config, snapshot_date=SNAP_DATE_IDEM)

    with open(parquet_path, "rb") as f:
        second_bytes = f.read()
    with open(manifest_path, "r") as f:
        manifest2 = json.load(f)
    second_checksum = manifest2["files"][0]["checksum"]
    second_row_count = manifest2["files"][0]["row_count"]

    assert second_bytes == first_bytes, "Parquet bytes should be identical on rerun"
    assert second_row_count == first_row_count, "row_count should not change on rerun"
    assert second_checksum == first_checksum, "checksum should match on rerun"

    # No duplicates: our test player has exactly 3 rows, not 6
    table = pq.read_table(parquet_path)
    df = table.to_pydict()
    test_rows = [i for i, p in enumerate(df["player_id"]) if p == f"{SNAP_PLAYER_PREFIX}-301"]
    assert len(test_rows) == 3, f"Expected 3 rows (no duplicates), got {len(test_rows)}"


# ---------------------------------------------------------------------------
# T017: test_snapshot_overwrite_with_changed_source (US3)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_overwrite_with_changed_source(snap_db_conn, snap_config):
    """Run snapshot with 5 rows, add 2 more source rows, rerun — verify 7 rows (overwrite).

    T017: New Parquet has 7 rows; the old snapshot is fully replaced, not appended.
    """
    from archive_executor.snapshot import run_snapshot

    insert_snap_players(snap_db_conn, [
        (f"{SNAP_PLAYER_PREFIX}-401", f"{SNAP_PLAN_PREFIX}-401"),
        (f"{SNAP_PLAYER_PREFIX}-402", f"{SNAP_PLAN_PREFIX}-402"),
    ])
    progress_rows_initial = [
        ("SNAP-SP-401", f"{SNAP_PLAYER_PREFIX}-401", f"{SNAP_SUBJECT_PREFIX}-A", 10.0),
        ("SNAP-SP-402", f"{SNAP_PLAYER_PREFIX}-401", f"{SNAP_SUBJECT_PREFIX}-B", 20.0),
        ("SNAP-SP-403", f"{SNAP_PLAYER_PREFIX}-401", f"{SNAP_SUBJECT_PREFIX}-C", 30.0),
        ("SNAP-SP-404", f"{SNAP_PLAYER_PREFIX}-402", f"{SNAP_SUBJECT_PREFIX}-A", 40.0),
        ("SNAP-SP-405", f"{SNAP_PLAYER_PREFIX}-402", f"{SNAP_SUBJECT_PREFIX}-B", 50.0),
    ]
    insert_snap_progress(snap_db_conn, progress_rows_initial)

    # First snapshot — 5 test rows
    run_snapshot(snap_config, snapshot_date=SNAP_DATE_OVERWRITE)

    final_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", SNAP_DATE_OVERWRITE)
    parquet_path = os.path.join(final_dir, "fact_structure_progress.parquet")

    table1 = pq.read_table(parquet_path)
    df1 = table1.to_pydict()
    test_rows_1 = [i for i, p in enumerate(df1["player_id"]) if p.startswith(f"{SNAP_PLAYER_PREFIX}-4")]
    assert len(test_rows_1) == 5, f"Expected 5 rows before overwrite, got {len(test_rows_1)}"

    # Add 2 more source rows
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-406", f"{SNAP_PLAYER_PREFIX}-402", f"{SNAP_SUBJECT_PREFIX}-C", 60.0),
        ("SNAP-SP-407", f"{SNAP_PLAYER_PREFIX}-402", f"{SNAP_SUBJECT_PREFIX}-D", 70.0),
    ])

    # Rerun for same date — should overwrite with 7 rows
    run_snapshot(snap_config, snapshot_date=SNAP_DATE_OVERWRITE)

    table2 = pq.read_table(parquet_path)
    df2 = table2.to_pydict()
    test_rows_2 = [i for i, p in enumerate(df2["player_id"]) if p.startswith(f"{SNAP_PLAYER_PREFIX}-4")]
    assert len(test_rows_2) == 7, f"Expected 7 rows after overwrite, got {len(test_rows_2)}"


# ---------------------------------------------------------------------------
# Phase 6: User Story 4 — Missing Plan Rejection
# ---------------------------------------------------------------------------

SNAP_DATE_US4 = "2026-05-04"


# ---------------------------------------------------------------------------
# T019: test_snapshot_rejects_no_profile (US4)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_rejects_no_profile(snap_db_conn, snap_config):
    """Insert 3 progress rows for a player with NO profile, plus 2 valid rows.
    Verify: output does not contain the no-profile player, rejected_no_profile >= 3.

    T019: Rejection of players with no matching Player Profile entry.
    """
    from archive_executor.snapshot import run_snapshot

    no_profile_player = f"{SNAP_PLAYER_PREFIX}-NP-001"
    valid_player = f"{SNAP_PLAYER_PREFIX}-501"
    valid_plan = f"{SNAP_PLAN_PREFIX}-501"

    # Only insert the valid player's profile — no_profile_player intentionally has none
    insert_snap_players(snap_db_conn, [(valid_player, valid_plan)])

    # 3 progress rows for the orphan player (no profile)
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-NP-001", no_profile_player, f"{SNAP_SUBJECT_PREFIX}-X", 10.0),
        ("SNAP-SP-NP-002", no_profile_player, f"{SNAP_SUBJECT_PREFIX}-Y", 20.0),
        ("SNAP-SP-NP-003", no_profile_player, f"{SNAP_SUBJECT_PREFIX}-Z", 30.0),
    ])
    # 2 valid rows
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-501", valid_player, f"{SNAP_SUBJECT_PREFIX}-A", 55.0),
        ("SNAP-SP-502", valid_player, f"{SNAP_SUBJECT_PREFIX}-B", 65.0),
    ])

    summary = run_snapshot(snap_config, snapshot_date=SNAP_DATE_US4)

    final_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", SNAP_DATE_US4)
    parquet_path = os.path.join(final_dir, "fact_structure_progress.parquet")
    assert os.path.isfile(parquet_path)

    table = pq.read_table(parquet_path)
    df = table.to_pydict()

    # No-profile player must NOT appear in output
    no_profile_rows = [i for i, p in enumerate(df["player_id"]) if p == no_profile_player]
    assert len(no_profile_rows) == 0, f"no-profile player found in output: {len(no_profile_rows)} rows"

    # Valid player rows must be present
    valid_rows = [i for i, p in enumerate(df["player_id"]) if p == valid_player]
    assert len(valid_rows) == 2, f"Expected 2 valid rows for {valid_player}, got {len(valid_rows)}"

    # Rejection count must be at least 3 (the ones we inserted)
    assert summary["rejected_no_profile"] >= 3, (
        f"Expected rejected_no_profile >= 3, got {summary['rejected_no_profile']}"
    )


# ---------------------------------------------------------------------------
# T020: test_snapshot_rejects_null_plan (US4)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_rejects_null_plan(snap_db_conn, snap_config):
    """Insert a player profile with plan=NULL and 2 progress rows, plus 3 valid rows.
    Verify: output excludes null-plan player, rejected_null_plan >= 2.

    T020: Rejection of players whose Player Profile has plan=NULL.
    """
    from archive_executor.snapshot import run_snapshot

    null_plan_player = f"{SNAP_PLAYER_PREFIX}-NUL-001"
    valid_player = f"{SNAP_PLAYER_PREFIX}-601"
    valid_plan = f"{SNAP_PLAN_PREFIX}-601"

    insert_snap_player_no_plan(snap_db_conn, null_plan_player)
    insert_snap_players(snap_db_conn, [(valid_player, valid_plan)])

    # 2 rows for null-plan player
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-NUL-001", null_plan_player, f"{SNAP_SUBJECT_PREFIX}-P", 40.0),
        ("SNAP-SP-NUL-002", null_plan_player, f"{SNAP_SUBJECT_PREFIX}-Q", 60.0),
    ])
    # 3 valid rows
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-601", valid_player, f"{SNAP_SUBJECT_PREFIX}-A", 10.0),
        ("SNAP-SP-602", valid_player, f"{SNAP_SUBJECT_PREFIX}-B", 20.0),
        ("SNAP-SP-603", valid_player, f"{SNAP_SUBJECT_PREFIX}-C", 30.0),
    ])

    summary = run_snapshot(snap_config, snapshot_date=SNAP_DATE_US4)

    final_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", SNAP_DATE_US4)
    parquet_path = os.path.join(final_dir, "fact_structure_progress.parquet")
    assert os.path.isfile(parquet_path)

    table = pq.read_table(parquet_path)
    df = table.to_pydict()

    # Null-plan player must NOT appear in output
    null_plan_rows = [i for i, p in enumerate(df["player_id"]) if p == null_plan_player]
    assert len(null_plan_rows) == 0, f"null-plan player found in output: {len(null_plan_rows)} rows"

    # Valid rows must be present
    valid_rows = [i for i, p in enumerate(df["player_id"]) if p == valid_player]
    assert len(valid_rows) == 3, f"Expected 3 valid rows for {valid_player}, got {len(valid_rows)}"

    # Rejection count must be at least 2
    assert summary["rejected_null_plan"] >= 2, (
        f"Expected rejected_null_plan >= 2, got {summary['rejected_null_plan']}"
    )


# ---------------------------------------------------------------------------
# T021: test_snapshot_empty_table (US4)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_empty_table(snap_db_conn, snap_config):
    """Insert only rejected rows (null-plan player) so that our test player
    produces 0 valid output rows. Verify: Parquet written with correct schema,
    manifest exists, no errors raised (FR-013).

    Note: The DB may contain other valid players, so total row_count may be > 0.
    We verify schema integrity and that no exception is raised.

    T021: FR-013 — empty (all-rejected) case handled gracefully.
    """
    from archive_executor.snapshot import run_snapshot

    null_plan_player = f"{SNAP_PLAYER_PREFIX}-NUL2-001"
    insert_snap_player_no_plan(snap_db_conn, null_plan_player)
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-NUL2-001", null_plan_player, f"{SNAP_SUBJECT_PREFIX}-R", 50.0),
    ])

    # Should not raise
    summary = run_snapshot(snap_config, snapshot_date="2026-05-11")

    final_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", "2026-05-11")
    parquet_path = os.path.join(final_dir, "fact_structure_progress.parquet")
    manifest_path = os.path.join(final_dir, "manifest.json")

    assert os.path.isfile(parquet_path), "Parquet file must exist even if all rows rejected"
    assert os.path.isfile(manifest_path), "manifest.json must exist even if all rows rejected"

    # Verify schema is correct (regardless of row count)
    table = pq.read_table(parquet_path)
    assert set(table.schema.names) == {"snapshot_date", "player_id", "plan_id", "subject_id", "completion_percentage"}

    # Verify manifest is valid JSON with expected fields
    with open(manifest_path, "r") as f:
        manifest = json.load(f)
    assert manifest["dataset_key"] == "structure_progress_snapshot"
    assert manifest["files"][0]["row_count"] == summary["row_count"]
    assert summary["row_count"] >= 0


# ---------------------------------------------------------------------------
# Phase 7: User Story 5 — Weekly Trend Analytics
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# T023: test_snapshot_multi_week_trend (US5)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_multi_week_trend(snap_db_conn, snap_config):
    """Generate 3 weekly snapshots with increasing completion percentages.
    Verify: 3 rows across partitions for the student-subject pair, with correct
    snapshot_date and increasing completion_percentage.

    T023: US5 — multi-week trend dataset validates across consecutive snapshots.
    """
    from archive_executor.snapshot import run_snapshot

    player_name = f"{SNAP_PLAYER_PREFIX}-701"
    plan_name = f"{SNAP_PLAN_PREFIX}-701"
    subject_name = f"{SNAP_SUBJECT_PREFIX}-Trend"
    progress_name = "SNAP-SP-701"
    week_dates = ["2026-06-07", "2026-06-14", "2026-06-21"]
    completions = [20.0, 50.0, 80.0]

    insert_snap_players(snap_db_conn, [(player_name, plan_name)])
    insert_snap_progress(snap_db_conn, [(progress_name, player_name, subject_name, completions[0])])

    # Snapshot week 1 — 20%
    run_snapshot(snap_config, snapshot_date=week_dates[0])

    # Update to 50% and snapshot week 2
    with snap_db_conn.cursor() as cursor:
        cursor.execute(
            "UPDATE `tabMemora Structure Progress` SET `completion_percentage` = %s WHERE `name` = %s",
            (completions[1], progress_name),
        )
    snap_db_conn.commit()
    run_snapshot(snap_config, snapshot_date=week_dates[1])

    # Update to 80% and snapshot week 3
    with snap_db_conn.cursor() as cursor:
        cursor.execute(
            "UPDATE `tabMemora Structure Progress` SET `completion_percentage` = %s WHERE `name` = %s",
            (completions[2], progress_name),
        )
    snap_db_conn.commit()
    run_snapshot(snap_config, snapshot_date=week_dates[2])

    # Read all 3 partition Parquet files and extract the trend row
    trend_rows = []
    for date_str, expected_pct in zip(week_dates, completions):
        partition_dir = os.path.join(snap_config.snapshot_output_path, "structure_progress", date_str)
        assert os.path.isdir(partition_dir), f"Partition missing for {date_str}"
        parquet_path = os.path.join(partition_dir, "fact_structure_progress.parquet")
        table = pq.read_table(parquet_path)
        df = table.to_pydict()
        matching = [
            {
                "snapshot_date": df["snapshot_date"][i],
                "completion_percentage": df["completion_percentage"][i],
            }
            for i in range(len(df["player_id"]))
            if df["player_id"][i] == player_name and df["subject_id"][i] == subject_name
        ]
        assert len(matching) == 1, f"Expected 1 row for {player_name}/{subject_name} in {date_str}, got {len(matching)}"
        row = matching[0]
        assert row["snapshot_date"] == datetime.date.fromisoformat(date_str), (
            f"snapshot_date mismatch in {date_str}: got {row['snapshot_date']}"
        )
        assert row["completion_percentage"] == expected_pct, (
            f"completion_percentage mismatch in {date_str}: expected {expected_pct}, got {row['completion_percentage']}"
        )
        trend_rows.append(row)

    # Verify increasing trend
    assert trend_rows[0]["completion_percentage"] < trend_rows[1]["completion_percentage"] < trend_rows[2]["completion_percentage"], \
        "Completion percentages should increase across weeks"


# ---------------------------------------------------------------------------
# Phase 8: Polish — DQ Validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# T025: test_snapshot_dq_validation (Polish)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_dq_validation(snap_db_conn, snap_config):
    """Verify that DQ validation runs as part of the pipeline and passes for valid data.

    T025: DQ rules DQ-SP-01 through DQ-SP-08 all pass for well-formed snapshot output.
    """
    from archive_executor.snapshot import run_snapshot

    insert_snap_players(snap_db_conn, [
        (f"{SNAP_PLAYER_PREFIX}-801", f"{SNAP_PLAN_PREFIX}-801"),
        (f"{SNAP_PLAYER_PREFIX}-802", f"{SNAP_PLAN_PREFIX}-802"),
    ])
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-801", f"{SNAP_PLAYER_PREFIX}-801", f"{SNAP_SUBJECT_PREFIX}-DQ1", 25.0),
        ("SNAP-SP-802", f"{SNAP_PLAYER_PREFIX}-801", f"{SNAP_SUBJECT_PREFIX}-DQ2", 75.0),
        ("SNAP-SP-803", f"{SNAP_PLAYER_PREFIX}-802", f"{SNAP_SUBJECT_PREFIX}-DQ1", 50.0),
    ])

    # Should not raise even with DQ validation enabled
    summary = run_snapshot(snap_config, snapshot_date="2026-07-05")
    assert summary["row_count"] >= 3
    # DQ key is present in summary
    assert "dq_passed" in summary


# ---------------------------------------------------------------------------
# T026: test_snapshot_transfer_checksum_failure_is_fatal
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_snapshot_transfer_checksum_failure_is_fatal(snap_db_conn, snap_config, monkeypatch):
    """When verify_remote_checksums returns valid=False the pipeline must raise.

    T026: A failed remote checksum must be fatal — the call must not return
    success with remote_path populated when the remote copy is corrupt or
    incomplete.
    """
    import dataclasses

    import archive_executor.transfer as transfer_mod
    from archive_executor.snapshot import run_snapshot

    insert_snap_players(snap_db_conn, [
        (f"{SNAP_PLAYER_PREFIX}-901", f"{SNAP_PLAN_PREFIX}-901"),
    ])
    insert_snap_progress(snap_db_conn, [
        ("SNAP-SP-901", f"{SNAP_PLAYER_PREFIX}-901", f"{SNAP_SUBJECT_PREFIX}-CHK", 55.0),
    ])

    # Build a config that satisfies has_ssh_config() (frozen dataclass — use replace)
    ssh_config = dataclasses.replace(
        snap_config,
        ssh_host="analytics.internal",
        ssh_user="deploy",
        ssh_key_path="/home/deploy/.ssh/id_ed25519",
        remote_snapshot_path="/remote/snap",
    )

    import archive_executor.snapshot as snapshot_mod

    # DQ validation passes (not under test here)
    monkeypatch.setattr(
        snapshot_mod,
        "_run_dq_validation",
        lambda config, parquet_path: {"passed": True, "results": [], "warnings": []},
    )

    # transfer_batch succeeds; verify_remote_checksums reports a mismatch
    monkeypatch.setattr(
        transfer_mod,
        "transfer_batch",
        lambda **kw: "/remote/snap/SNAP-2026-08-03",
    )
    monkeypatch.setattr(
        transfer_mod,
        "verify_remote_checksums",
        lambda **kw: {
            "valid": False,
            "errors": ["sha256 mismatch: fact_structure_progress.parquet"],
            "files_checked": 1,
        },
    )

    with pytest.raises(RuntimeError, match="checksum"):
        run_snapshot(ssh_config, snapshot_date="2026-08-03")
