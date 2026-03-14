"""Integration tests for fact_memory_state export.

Scenarios:
  FMS-FULL:    Full export -> 12-column Parquet with correct column names.
  FMS-UUID:    BIN_TO_UUID converts binary item_id to UUID text string.
  FMS-DECIMAL: CAST converts DECIMAL stability/difficulty to float64.
  FMS-NONULL:  No null ms_id, player_id, or item_id values in output.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_fact_memory_state.py -v
"""

import dataclasses
import logging
import os
import re
import uuid
from datetime import date, datetime

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FMS_PREFIX = "TEST-FMS"
PLAYER_PREFIX = f"{FMS_PREFIX}-PLYR"
MEMORY_STATE_TABLE = "tabMemora Memory State"

# season_seq=1 maps to partition p_season_1 (<2), guaranteed to exist
TEST_SEASON_SEQ = 1

EXPECTED_COLUMNS = {
	"ms_id", "player_id", "item_id", "season_seq",
	"subject_id", "lesson_id", "stability", "difficulty",
	"next_review", "last_review", "fsrs_state", "fsrs_step",
}

UUID_PATTERN = re.compile(
	r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
	re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base: Config, output_dir: str) -> Config:
	schema_path = os.path.join(os.path.dirname(__file__), "..", "schemas")
	return dataclasses.replace(
		base,
		analytics_output_path=output_dir,
		analytics_schema_path=str(os.path.abspath(schema_path)),
		analytics_datasets=["fact_memory_state"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_fact_memory_state")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _ms_name(n: int) -> int:
	"""Generate a stable BIGINT name for test row n using uuid5."""
	raw = uuid.uuid5(uuid.NAMESPACE_DNS, f"ms-name-FMS-{n}").bytes[:7]
	return int.from_bytes(raw, "big") + 1


def _ms_item_id_bytes(n: int) -> bytes:
	"""Generate a deterministic 16-byte binary item_id for test row n."""
	return uuid.uuid5(uuid.NAMESPACE_DNS, f"ms-item-FMS-{n}").bytes


def _insert_test_memory_states(conn, count: int = 3) -> list[int]:
	"""Insert test memory state rows. Returns list of BIGINT names (ms_ids)."""
	names = []
	for n in range(1, count + 1):
		name = _ms_name(n)
		names.append(name)
		player = f"{PLAYER_PREFIX}-{n:03d}"
		item_id = _ms_item_id_bytes(n)
		with conn.cursor() as cur:
			cur.execute(
				"INSERT IGNORE INTO `tabMemora Memory State` "
				"(`name`, `player`, `item_id`, `season_seq`, `subject`, `lesson`, "
				" `stability`, `difficulty`, `next_review`, `last_review`, `state`, `step`) "
				"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
				(
					name,
					player,
					item_id,
					TEST_SEASON_SEQ,
					f"{FMS_PREFIX}-SUBJ-{n:03d}",
					f"{FMS_PREFIX}-LESSON-{n:03d}",
					3.141592653,       # stability (DECIMAL)
					0.300000000,       # difficulty (DECIMAL)
					date(2099, 7, 15),          # next_review (DATE)
					datetime(2099, 6, 1, 12, 0, 0),  # last_review (DATETIME)
					2,                          # state (fsrs_state)
					0,                          # step (fsrs_step)
				),
			)
	conn.commit()
	return names


def _cleanup_test_memory_states(conn) -> None:
	"""Delete all test memory state rows with player matching TEST-FMS-%."""
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Memory State` WHERE `player` LIKE %s",
			(f"{FMS_PREFIX}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# FMS-FULL: Full export with 12 columns
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_memory_state_full_export(analytics_db_config, db_conn, tmp_path):
	"""fact_memory_state export produces 12-column Parquet with correct column names."""
	_cleanup_test_memory_states(db_conn)
	try:
		inserted_names = _insert_test_memory_states(db_conn, 3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "fact_memory_state" in results
		result = results["fact_memory_state"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "fact_memory_state.parquet")
		assert os.path.exists(out_path)

		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS
		assert table.num_rows >= 3

		# Our test rows are present
		all_ms_ids = table.column("ms_id").to_pylist()
		for name in inserted_names:
			assert name in all_ms_ids, f"Test ms_id {name} not found in output"

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "fact_memory_state.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		_cleanup_test_memory_states(db_conn)


# ---------------------------------------------------------------------------
# FMS-UUID: BIN_TO_UUID converts binary item_id to UUID text
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_memory_state_bin_to_uuid(analytics_db_config, db_conn, tmp_path):
	"""BIN_TO_UUID converts binary item_id to a valid UUID text string."""
	_cleanup_test_memory_states(db_conn)
	try:
		inserted_names = _insert_test_memory_states(db_conn, 1)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_memory_state"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_memory_state.parquet"))

		# Find our test row
		all_ms_ids = table.column("ms_id").to_pylist()
		idx = all_ms_ids.index(inserted_names[0])

		item_id_val = table.column("item_id")[idx].as_py()
		assert item_id_val is not None, "item_id is null"
		assert isinstance(item_id_val, str), f"item_id should be str, got {type(item_id_val)}"
		assert UUID_PATTERN.match(item_id_val), (
			f"item_id {item_id_val!r} does not match UUID format"
		)

		# Verify it matches the UUID we inserted
		expected_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "ms-item-FMS-1"))
		assert item_id_val.lower() == expected_uuid.lower(), (
			f"item_id {item_id_val} does not match expected UUID {expected_uuid}"
		)
	finally:
		_cleanup_test_memory_states(db_conn)


# ---------------------------------------------------------------------------
# FMS-DECIMAL: CAST converts DECIMAL to float64
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_memory_state_decimal_to_float(analytics_db_config, db_conn, tmp_path):
	"""CAST converts DECIMAL stability/difficulty to float64 (not Decimal objects)."""
	_cleanup_test_memory_states(db_conn)
	try:
		inserted_names = _insert_test_memory_states(db_conn, 1)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_memory_state"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_memory_state.parquet"))

		# Find our test row
		all_ms_ids = table.column("ms_id").to_pylist()
		idx = all_ms_ids.index(inserted_names[0])

		stability_val = table.column("stability")[idx].as_py()
		difficulty_val = table.column("difficulty")[idx].as_py()

		# Must be float, not Decimal
		assert isinstance(stability_val, float), (
			f"stability should be float, got {type(stability_val).__name__}"
		)
		assert isinstance(difficulty_val, float), (
			f"difficulty should be float, got {type(difficulty_val).__name__}"
		)

		# Verify approximate values
		assert abs(stability_val - 3.141592653) < 1e-6, (
			f"stability value {stability_val} not close to expected 3.141592653"
		)
		assert abs(difficulty_val - 0.3) < 1e-6, (
			f"difficulty value {difficulty_val} not close to expected 0.3"
		)
	finally:
		_cleanup_test_memory_states(db_conn)


# ---------------------------------------------------------------------------
# FMS-NONULL: No null ms_id, player_id, or item_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_memory_state_no_null_keys(analytics_db_config, db_conn, tmp_path):
	"""No null ms_id, player_id, or item_id values in output."""
	_cleanup_test_memory_states(db_conn)
	try:
		_insert_test_memory_states(db_conn, 2)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_memory_state"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_memory_state.parquet"))

		ms_ids = table.column("ms_id").to_pylist()
		player_ids = table.column("player_id").to_pylist()
		item_ids = table.column("item_id").to_pylist()

		assert all(v is not None for v in ms_ids), "Found null ms_id"
		assert all(v is not None for v in player_ids), "Found null player_id"
		assert all(v is not None for v in item_ids), "Found null item_id"
	finally:
		_cleanup_test_memory_states(db_conn)
