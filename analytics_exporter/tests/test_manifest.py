"""Unit and integration tests for analytics manifest generation.

Unit scenarios:
  MF-SHA:  compute_sha256 on a temp file returns correct hex digest.
  MF-JSON: write_manifest produces valid JSON with all required fields.
  MF-MULTI: Multi-file manifest has correct files array length.
  MF-ZERO: Zero-byte file produces a valid checksum.

Integration scenarios (T032/T033 — Phase 5: Manifest Verification):
  MF-E2E-SINGLE:  Export real dataset, independently verify SHA-256 + row_count + size_bytes.
  MF-E2E-ZERO:    Zero-row dataset produces manifest with row_count=0 and valid SHA-256.
  MF-E2E-MULTI:   Multi-file dataset manifest has multiple entries in files array.
  MF-E2E-NOORPH:  No orphan manifest written when Parquet export fails.

Run:
    python3 -m pytest analytics_exporter/tests/test_manifest.py -v
"""

import dataclasses
import hashlib
import json
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.manifest import compute_sha256, write_manifest
from analytics_exporter.run import orchestrate_exports


# ---------------------------------------------------------------------------
# MF-SHA: compute_sha256 returns correct hex digest
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_compute_sha256_correct_digest(tmp_path):
    """compute_sha256 on a known file returns the correct SHA-256 hex digest."""
    content = b"hello world analytics exporter"
    f = tmp_path / "sample.bin"
    f.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    result = compute_sha256(str(f))
    assert result == expected


# ---------------------------------------------------------------------------
# MF-JSON: write_manifest produces valid JSON with required fields
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_write_manifest_valid_json(tmp_path):
    """write_manifest produces valid JSON with all required top-level fields."""
    files_info = [
        {
            "filename": "dim_player.parquet",
            "row_count": 100,
            "checksum": "abc123def456",
            "size_bytes": 5678,
        }
    ]
    path = write_manifest(str(tmp_path), "dim_player", files_info)

    assert os.path.exists(path)
    assert path.endswith("dim_player.manifest.json")

    with open(path) as f:
        manifest = json.load(f)

    assert manifest["manifest_version"] == "1.0"
    assert manifest["dataset_key"] == "dim_player"
    assert manifest["kind"] == "analytics"
    assert manifest["schema_version"] == "1.0"
    assert "created_at" in manifest
    assert manifest["source"] == "memora_admin"
    assert len(manifest["files"]) == 1

    entry = manifest["files"][0]
    assert entry["filename"] == "dim_player.parquet"
    assert entry["row_count"] == 100
    assert entry["checksum"] == "sha256:abc123def456"
    assert entry["size_bytes"] == 5678


# ---------------------------------------------------------------------------
# MF-MULTI: Multi-file manifest has correct files array length
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_multi_file_manifest_correct_length(tmp_path):
    """Multi-file manifest has correct number of entries in files array."""
    files_info = [
        {
            "filename": "fact_challenge_attempt.parquet",
            "row_count": 30,
            "checksum": "aaa111",
            "size_bytes": 1000,
        },
        {
            "filename": "fact_challenge_detail.parquet",
            "row_count": 95,
            "checksum": "bbb222",
            "size_bytes": 2000,
        },
    ]
    path = write_manifest(str(tmp_path), "fact_challenge", files_info)

    with open(path) as f:
        manifest = json.load(f)

    assert manifest["dataset_key"] == "fact_challenge"
    assert len(manifest["files"]) == 2
    assert manifest["files"][0]["filename"] == "fact_challenge_attempt.parquet"
    assert manifest["files"][1]["filename"] == "fact_challenge_detail.parquet"


# ---------------------------------------------------------------------------
# MF-ZERO: Zero-byte file produces valid checksum
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_compute_sha256_zero_byte_file(tmp_path):
    """Zero-byte file produces a valid SHA-256 checksum (the hash of empty input)."""
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")

    result = compute_sha256(str(f))
    expected = hashlib.sha256(b"").hexdigest()
    assert result == expected
    assert len(result) == 64  # SHA-256 hex digest is 64 chars


# ===========================================================================
# Integration tests (T032/T033 — Phase 5: Manifest Verification)
# ===========================================================================

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FC_PREFIX = "TEST-MF"
_ATTEMPT_TABLE = "tabMemora Challenge Attempt"
_DETAIL_TABLE = "tabMemora Challenge Attempt Detail"


def _make_config(base: Config, output_dir: str, datasets: list[str]) -> Config:
    schema_path = os.path.join(os.path.dirname(__file__), "..", "schemas")
    return dataclasses.replace(
        base,
        analytics_output_path=output_dir,
        analytics_schema_path=str(os.path.abspath(schema_path)),
        analytics_datasets=datasets,
    )


def _make_logger() -> logging.Logger:
    log = logging.getLogger("test_manifest_integration")
    if not log.handlers:
        h = logging.StreamHandler()
        h.setLevel(logging.DEBUG)
        log.addHandler(h)
    log.setLevel(logging.DEBUG)
    return log


def _insert_challenge_attempts(conn, count: int = 1) -> list[str]:
    """Insert test challenge attempts with TEST-MF-ATT-* names."""
    ids = []
    for n in range(1, count + 1):
        att_name = f"{_FC_PREFIX}-ATT-{n:03d}"
        ids.append(att_name)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO `tabMemora Challenge Attempt` "
                "(`name`, `player`, `topic`, `subject`, `season`, "
                " `attempt_number`, `total_questions`, `correct_count`, `score_pct`, "
                " `passed`, `time_spent`, `xp_earned`, `submitted_at`, "
                " `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
                "VALUES (%s, %s, %s, %s, %s, "
                "        %s, %s, %s, %s, "
                "        %s, %s, %s, %s, "
                "        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)",
                (
                    att_name,
                    f"{_FC_PREFIX}-PLYR-{n:03d}",
                    f"{_FC_PREFIX}-TOPIC-{n:03d}",
                    f"{_FC_PREFIX}-SUBJ-{n:03d}",
                    None,
                    n, 5, 3, 60.0, 1, 120, 50,
                    "2099-07-01 10:00:00",
                ),
            )
    conn.commit()
    return ids


def _insert_challenge_details(conn, attempt_name: str, count: int = 3) -> None:
    """Insert test challenge attempt details linked to a parent attempt."""
    for n in range(1, count + 1):
        dtl_name = f"{_FC_PREFIX}-DTL-{n:03d}"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO `tabMemora Challenge Attempt Detail` "
                "(`name`, `parent`, `parentfield`, `parenttype`, `idx`, "
                " `item_id`, `correct`, `time_spent`, `chosen_answer`, "
                " `creation`, `modified`, `modified_by`, `owner`, `docstatus`) "
                "VALUES (%s, %s, 'details', 'Memora Challenge Attempt', %s, "
                "        %s, %s, %s, %s, "
                "        NOW(), NOW(), 'test@test.com', 'test@test.com', 0)",
                (
                    dtl_name, attempt_name, n,
                    f"{_FC_PREFIX}-ITEM-{n:03d}",
                    1 if n % 2 else 0,
                    20 + n * 5,
                    (n % 4) + 1,
                ),
            )
    conn.commit()


def _cleanup_challenge_data(conn) -> None:
    """Delete all TEST-MF-* challenge rows."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM `tabMemora Challenge Attempt Detail` WHERE `name` LIKE %s",
            (f"{_FC_PREFIX}-%",),
        )
        cur.execute(
            "DELETE FROM `tabMemora Challenge Attempt` WHERE `name` LIKE %s",
            (f"{_FC_PREFIX}-%",),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# T032 — MF-E2E-SINGLE: End-to-end manifest verification for single-file dataset
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_manifest_e2e_single_file_checksum(analytics_db_config, db_conn, tmp_path):
    """Export dim_season, independently compute SHA-256, confirm manifest checksum matches exactly."""
    cfg = _make_config(analytics_db_config, str(tmp_path), ["dim_season"])
    results = orchestrate_exports(cfg, _make_logger())

    assert "dim_season" in results
    result = results["dim_season"]
    assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

    parquet_path = os.path.join(str(tmp_path), "dim_season.parquet")
    manifest_path = os.path.join(str(tmp_path), "dim_season.manifest.json")
    assert os.path.exists(parquet_path)
    assert os.path.exists(manifest_path)

    with open(manifest_path) as f:
        manifest = json.load(f)

    entry = manifest["files"][0]

    # Independently compute SHA-256 with hashlib
    h = hashlib.sha256()
    with open(parquet_path, "rb") as pf:
        while True:
            chunk = pf.read(65536)
            if not chunk:
                break
            h.update(chunk)
    independent_checksum = h.hexdigest()

    assert entry["checksum"] == f"sha256:{independent_checksum}", (
        f"Manifest checksum {entry['checksum']} != independently computed sha256:{independent_checksum}"
    )


@pytest.mark.integration
def test_manifest_e2e_single_file_row_count(analytics_db_config, db_conn, tmp_path):
    """Export dim_season, confirm manifest row_count matches pq.read_table().num_rows."""
    cfg = _make_config(analytics_db_config, str(tmp_path), ["dim_season"])
    results = orchestrate_exports(cfg, _make_logger())

    assert results["dim_season"].success

    parquet_path = os.path.join(str(tmp_path), "dim_season.parquet")
    manifest_path = os.path.join(str(tmp_path), "dim_season.manifest.json")

    with open(manifest_path) as f:
        manifest = json.load(f)

    table = pq.read_table(parquet_path)
    assert manifest["files"][0]["row_count"] == table.num_rows, (
        f"Manifest row_count {manifest['files'][0]['row_count']} != "
        f"Parquet num_rows {table.num_rows}"
    )


@pytest.mark.integration
def test_manifest_e2e_single_file_size_bytes(analytics_db_config, db_conn, tmp_path):
    """Export dim_season, confirm manifest size_bytes matches os.path.getsize()."""
    cfg = _make_config(analytics_db_config, str(tmp_path), ["dim_season"])
    results = orchestrate_exports(cfg, _make_logger())

    assert results["dim_season"].success

    parquet_path = os.path.join(str(tmp_path), "dim_season.parquet")
    manifest_path = os.path.join(str(tmp_path), "dim_season.manifest.json")

    with open(manifest_path) as f:
        manifest = json.load(f)

    actual_size = os.path.getsize(parquet_path)
    assert manifest["files"][0]["size_bytes"] == actual_size, (
        f"Manifest size_bytes {manifest['files'][0]['size_bytes']} != "
        f"actual file size {actual_size}"
    )


# ---------------------------------------------------------------------------
# T033 — MF-E2E-ZERO: Zero-row dataset produces manifest with row_count=0
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_manifest_zero_row_dataset(analytics_db_config, db_conn, tmp_path):
    """Zero-row dataset produces manifest with row_count=0 and valid SHA-256."""
    # Use fact_interaction with far-future date range to get zero rows
    cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_interaction"])
    cfg = dataclasses.replace(
        cfg,
        analytics_interaction_from="2199-01-01",
        analytics_interaction_to="2199-01-02",
    )

    results = orchestrate_exports(cfg, _make_logger())

    assert "fact_interaction" in results
    result = results["fact_interaction"]
    assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

    parquet_path = os.path.join(str(tmp_path), "fact_interaction.parquet")
    manifest_path = os.path.join(str(tmp_path), "fact_interaction.manifest.json")
    assert os.path.exists(parquet_path)
    assert os.path.exists(manifest_path)

    with open(manifest_path) as f:
        manifest = json.load(f)

    entry = manifest["files"][0]
    assert entry["row_count"] == 0, f"Expected row_count=0, got {entry['row_count']}"

    # SHA-256 should still be valid (hash of empty Parquet file, not empty bytes)
    h = hashlib.sha256()
    with open(parquet_path, "rb") as pf:
        while True:
            chunk = pf.read(65536)
            if not chunk:
                break
            h.update(chunk)
    independent_checksum = h.hexdigest()
    assert entry["checksum"] == f"sha256:{independent_checksum}"

    # Parquet file should have 0 rows but valid schema
    table = pq.read_table(parquet_path)
    assert table.num_rows == 0


# ---------------------------------------------------------------------------
# T033 — MF-E2E-MULTI: Multi-file dataset manifest has multiple entries
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_manifest_multi_file_entries(analytics_db_config, db_conn, tmp_path):
    """Multi-file dataset (fact_challenge) produces manifest with multiple entries in files array."""
    _cleanup_challenge_data(db_conn)
    try:
        att_ids = _insert_challenge_attempts(db_conn, 1)
        _insert_challenge_details(db_conn, att_ids[0], 3)

        cfg = _make_config(analytics_db_config, str(tmp_path), ["fact_challenge"])
        results = orchestrate_exports(cfg, _make_logger())

        assert results["fact_challenge_attempt"].success
        assert results["fact_challenge_detail"].success

        manifest_path = os.path.join(str(tmp_path), "fact_challenge.manifest.json")
        assert os.path.exists(manifest_path)

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert len(manifest["files"]) == 2, (
            f"Expected 2 files in manifest, got {len(manifest['files'])}"
        )

        filenames = {e["filename"] for e in manifest["files"]}
        assert "fact_challenge_attempt.parquet" in filenames
        assert "fact_challenge_detail.parquet" in filenames

        # Independently verify each entry's checksum and size
        for entry in manifest["files"]:
            fpath = os.path.join(str(tmp_path), entry["filename"])
            assert os.path.exists(fpath)

            h = hashlib.sha256()
            with open(fpath, "rb") as pf:
                while True:
                    chunk = pf.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
            assert entry["checksum"] == f"sha256:{h.hexdigest()}"
            assert entry["size_bytes"] == os.path.getsize(fpath)

            table = pq.read_table(fpath)
            assert entry["row_count"] == table.num_rows
    finally:
        _cleanup_challenge_data(db_conn)


# ---------------------------------------------------------------------------
# T033 — MF-E2E-NOORPH: No orphan manifest when export fails
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_manifest_no_orphan_on_export_failure(analytics_db_config, db_conn, tmp_path, monkeypatch):
    """Manifest is NOT written when Parquet export fails — no orphan manifest."""
    cfg = _make_config(analytics_db_config, str(tmp_path), ["dim_season"])

    def _exploding_export(*args, **kwargs):
        raise RuntimeError("simulated export failure")

    monkeypatch.setattr(
        "analytics_exporter.run.export_snapshot", _exploding_export,
    )

    results = orchestrate_exports(cfg, _make_logger())

    assert "dim_season" in results
    result = results["dim_season"]
    assert not result.success, "Export should have failed"

    manifest_path = os.path.join(str(tmp_path), "dim_season.manifest.json")
    assert not os.path.exists(manifest_path), (
        "Orphan manifest found — manifest should not be written when export fails"
    )
