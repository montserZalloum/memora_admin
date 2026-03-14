"""Integration tests for fact_challenge multi-file export.

Scenarios:
  FC-MULTI:   Export produces TWO Parquet files (attempt + detail).
  FC-ATTCOL:  Attempt file has 13 expected columns.
  FC-DTLCOL:  Detail file has 5 expected columns.
  FC-MANIF:   Combined manifest has both files in files array.
  FC-NONULL:  No null attempt_id/player_id in attempt; no null attempt_id/item_id in detail.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_fact_challenge.py -v
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
# Constants
# ---------------------------------------------------------------------------

FC_PREFIX = "TEST-FC"
ATTEMPT_TABLE = "tabMemora Challenge Attempt"
DETAIL_TABLE = "tabMemora Challenge Attempt Detail"

EXPECTED_ATTEMPT_COLUMNS = {
	"attempt_id", "player_id", "topic_id", "subject_id", "season_id",
	"attempt_number", "total_questions", "correct_count", "score_pct",
	"passed", "time_spent_sec", "xp_earned", "submitted_at",
}

EXPECTED_DETAIL_COLUMNS = {
	"attempt_id", "item_id", "is_correct", "time_spent_sec", "chosen_answer",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base: Config, output_dir: str) -> Config:
	schema_path = os.path.join(os.path.dirname(__file__), "..", "schemas")
	return dataclasses.replace(
		base,
		analytics_output_path=output_dir,
		analytics_schema_path=str(os.path.abspath(schema_path)),
		analytics_datasets=["fact_challenge"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_fact_challenge")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _insert_test_attempts(conn, count: int = 1) -> list[str]:
	"""Insert test challenge attempts with TEST-FC-ATT-* names. Returns list of attempt names."""
	ids = []
	for n in range(1, count + 1):
		att_name = f"{FC_PREFIX}-ATT-{n:03d}"
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
					f"{FC_PREFIX}-PLYR-{n:03d}",
					f"{FC_PREFIX}-TOPIC-{n:03d}",
					f"{FC_PREFIX}-SUBJ-{n:03d}",
					None,                          # season
					n,                              # attempt_number
					5,                              # total_questions
					3,                              # correct_count
					60.0,                           # score_pct
					1,                              # passed
					120,                            # time_spent
					50,                             # xp_earned
					"2099-07-01 10:00:00",          # submitted_at
				),
			)
	conn.commit()
	return ids


def _insert_test_details(conn, attempt_name: str, count: int = 3) -> list[str]:
	"""Insert test challenge attempt details linked to a parent attempt. Returns list of detail names."""
	correct_vals = [1, 0, 1]
	time_vals = [20, 25, 30]
	answer_vals = [1, 3, 2]
	ids = []
	for n in range(1, count + 1):
		dtl_name = f"{FC_PREFIX}-DTL-{n:03d}"
		ids.append(dtl_name)
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
					dtl_name,
					attempt_name,
					n,                                          # idx
					f"{FC_PREFIX}-ITEM-{n:03d}",                # item_id
					correct_vals[(n - 1) % len(correct_vals)],  # correct
					time_vals[(n - 1) % len(time_vals)],        # time_spent
					answer_vals[(n - 1) % len(answer_vals)],    # chosen_answer
				),
			)
	conn.commit()
	return ids


def _cleanup_test_data(conn) -> None:
	"""Delete all TEST-FC-* rows in FK-safe order: details first, then attempts."""
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Challenge Attempt Detail` WHERE `name` LIKE %s",
			(f"{FC_PREFIX}-%",),
		)
		cur.execute(
			"DELETE FROM `tabMemora Challenge Attempt` WHERE `name` LIKE %s",
			(f"{FC_PREFIX}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# FC-MULTI: Multi-file export produces two Parquet files
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_challenge_multi_file_export(analytics_db_config, db_conn, tmp_path):
	"""Export produces TWO Parquet files: fact_challenge_attempt (13 cols) and fact_challenge_detail (5 cols)."""
	_cleanup_test_data(db_conn)
	try:
		att_ids = _insert_test_attempts(db_conn, 1)
		_insert_test_details(db_conn, att_ids[0], 3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		# Both member datasets must be present in results
		assert "fact_challenge_attempt" in results, "fact_challenge_attempt not in results"
		assert "fact_challenge_detail" in results, "fact_challenge_detail not in results"

		att_result = results["fact_challenge_attempt"]
		dtl_result = results["fact_challenge_detail"]

		assert att_result.success, f"Attempt export failed: {att_result.error}; violations: {att_result.violations}"
		assert dtl_result.success, f"Detail export failed: {dtl_result.error}; violations: {dtl_result.violations}"

		# Both Parquet files exist
		att_path = os.path.join(str(tmp_path), "fact_challenge_attempt.parquet")
		dtl_path = os.path.join(str(tmp_path), "fact_challenge_detail.parquet")
		assert os.path.exists(att_path), "fact_challenge_attempt.parquet not found"
		assert os.path.exists(dtl_path), "fact_challenge_detail.parquet not found"

		# Column counts
		att_table = pq.read_table(att_path)
		dtl_table = pq.read_table(dtl_path)
		assert len(att_table.schema.names) == 13, f"Expected 13 attempt columns, got {len(att_table.schema.names)}"
		assert len(dtl_table.schema.names) == 5, f"Expected 5 detail columns, got {len(dtl_table.schema.names)}"

		# Row counts (at least our test data)
		assert att_table.num_rows >= 1
		assert dtl_table.num_rows >= 3
	finally:
		_cleanup_test_data(db_conn)


# ---------------------------------------------------------------------------
# FC-ATTCOL: Attempt file has correct column names
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_challenge_attempt_columns(analytics_db_config, db_conn, tmp_path):
	"""Attempt Parquet has the 13 expected columns."""
	_cleanup_test_data(db_conn)
	try:
		att_ids = _insert_test_attempts(db_conn, 1)
		_insert_test_details(db_conn, att_ids[0], 3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_challenge_attempt"].success

		att_path = os.path.join(str(tmp_path), "fact_challenge_attempt.parquet")
		table = pq.read_table(att_path)
		assert set(table.schema.names) == EXPECTED_ATTEMPT_COLUMNS
	finally:
		_cleanup_test_data(db_conn)


# ---------------------------------------------------------------------------
# FC-DTLCOL: Detail file has correct column names
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_challenge_detail_columns(analytics_db_config, db_conn, tmp_path):
	"""Detail Parquet has the 5 expected columns."""
	_cleanup_test_data(db_conn)
	try:
		att_ids = _insert_test_attempts(db_conn, 1)
		_insert_test_details(db_conn, att_ids[0], 3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_challenge_detail"].success

		dtl_path = os.path.join(str(tmp_path), "fact_challenge_detail.parquet")
		table = pq.read_table(dtl_path)
		assert set(table.schema.names) == EXPECTED_DETAIL_COLUMNS
	finally:
		_cleanup_test_data(db_conn)


# ---------------------------------------------------------------------------
# FC-MANIF: Combined manifest has both files
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_challenge_combined_manifest(analytics_db_config, db_conn, tmp_path):
	"""Combined manifest (fact_challenge.manifest.json) has both files in files array."""
	_cleanup_test_data(db_conn)
	try:
		att_ids = _insert_test_attempts(db_conn, 1)
		_insert_test_details(db_conn, att_ids[0], 3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_challenge_attempt"].success
		assert results["fact_challenge_detail"].success

		manifest_path = os.path.join(str(tmp_path), "fact_challenge.manifest.json")
		assert os.path.exists(manifest_path), "fact_challenge.manifest.json not found"

		with open(manifest_path) as f:
			manifest = json.load(f)

		assert "files" in manifest
		assert len(manifest["files"]) == 2, f"Expected 2 files in manifest, got {len(manifest['files'])}"

		filenames = {entry["filename"] for entry in manifest["files"]}
		assert "fact_challenge_attempt.parquet" in filenames
		assert "fact_challenge_detail.parquet" in filenames

		# Each entry should have required fields
		for entry in manifest["files"]:
			assert "row_count" in entry
			assert "checksum" in entry
			assert "size_bytes" in entry
			assert entry["row_count"] >= 0
			assert entry["size_bytes"] > 0
	finally:
		_cleanup_test_data(db_conn)


# ---------------------------------------------------------------------------
# FC-NONULL: No null keys in attempt or detail files
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_challenge_no_null_keys(analytics_db_config, db_conn, tmp_path):
	"""No null attempt_id/player_id in attempt file; no null attempt_id/item_id in detail file."""
	_cleanup_test_data(db_conn)
	try:
		att_ids = _insert_test_attempts(db_conn, 1)
		_insert_test_details(db_conn, att_ids[0], 3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_challenge_attempt"].success
		assert results["fact_challenge_detail"].success

		# Attempt file: no null attempt_id or player_id
		att_path = os.path.join(str(tmp_path), "fact_challenge_attempt.parquet")
		att_table = pq.read_table(att_path)
		attempt_ids = att_table.column("attempt_id").to_pylist()
		player_ids = att_table.column("player_id").to_pylist()
		assert all(v is not None for v in attempt_ids), "Found null attempt_id in attempt file"
		assert all(v is not None for v in player_ids), "Found null player_id in attempt file"

		# Detail file: no null attempt_id or item_id
		dtl_path = os.path.join(str(tmp_path), "fact_challenge_detail.parquet")
		dtl_table = pq.read_table(dtl_path)
		detail_attempt_ids = dtl_table.column("attempt_id").to_pylist()
		detail_item_ids = dtl_table.column("item_id").to_pylist()
		assert all(v is not None for v in detail_attempt_ids), "Found null attempt_id in detail file"
		assert all(v is not None for v in detail_item_ids), "Found null item_id in detail file"
	finally:
		_cleanup_test_data(db_conn)
