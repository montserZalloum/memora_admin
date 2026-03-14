"""Integration tests for fact_interaction date-range export.

Scenarios:
  FI-FULL:   Date-range export -> 10-column Parquet with correct column names.
  FI-FILT:   Rows outside the date range are excluded from the output.
  FI-NONULL: No null event_id or player_id values in output.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_fact_interaction.py -v
"""

import dataclasses
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FI_PREFIX = "TEST-FI"
INTERACTION_TABLE = "tabMemora Interaction Log"

EXPECTED_COLUMNS = {
	"event_id", "player_id", "lesson_id", "stage_id", "item_id",
	"event_type", "time_spent_sec", "errors_count", "event_ts",
	"client_metadata",
}

# Date range that brackets all in-range test data
DATE_FROM = "2099-07-01"
DATE_TO = "2099-07-15"

# Timestamps outside the date range (used by FI-FILT)
TS_BEFORE_RANGE = "2099-06-15 12:00:00.000000"
TS_AFTER_RANGE = "2099-08-01 12:00:00.000000"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base: Config, output_dir: str) -> Config:
	schema_path = os.path.join(os.path.dirname(__file__), "..", "schemas")
	return dataclasses.replace(
		base,
		analytics_output_path=output_dir,
		analytics_schema_path=str(os.path.abspath(schema_path)),
		analytics_datasets=["fact_interaction"],
		analytics_interaction_from=DATE_FROM,
		analytics_interaction_to=DATE_TO,
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_fact_interaction")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _insert_test_interactions(conn, count: int = 5, prefix: str = FI_PREFIX) -> list[str]:
	"""Insert test interaction log rows with timestamps in the DATE_FROM..DATE_TO range.

	Returns list of generated event names (PKs).
	"""
	names = []
	for n in range(1, count + 1):
		name = f"{prefix}-{n:06d}"
		names.append(name)
		# Spread timestamps across 2099-07-01 .. 2099-07-14
		day = (n % 14) + 1
		ts = f"2099-07-{day:02d} {(n % 24):02d}:{(n * 3 % 60):02d}:00.000000"
		with conn.cursor() as cur:
			cur.execute(
				"INSERT IGNORE INTO `tabMemora Interaction Log` "
				"(`name`, `player`, `lesson`, `stage_id`, `item_id`, `event_type`, "
				" `time_spent`, `errors_count`, `timestamp`, `client_metadata`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
				"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)",
				(
					name,
					f"{prefix}-PLYR-{(n % 3) + 1:03d}",
					f"{prefix}-LESSON-{(n % 2) + 1:03d}",
					f"{prefix}-STAGE-{(n % 4) + 1:03d}",
					f"{prefix}-ITEM-{n:06d}",
					"review" if n % 2 == 0 else "learn",
					n * 10,         # time_spent
					n % 3,          # errors_count
					ts,
					f'{{"source": "test", "seq": {n}}}',
				),
			)
	conn.commit()
	return names


def _insert_out_of_range_interactions(conn, prefix: str = FI_PREFIX) -> list[str]:
	"""Insert interaction rows OUTSIDE the date range (before and after).

	Returns list of generated event names.
	"""
	rows = [
		(f"{prefix}-OOR-BEFORE-001", TS_BEFORE_RANGE),
		(f"{prefix}-OOR-BEFORE-002", TS_BEFORE_RANGE),
		(f"{prefix}-OOR-AFTER-001", TS_AFTER_RANGE),
	]
	names = []
	for name, ts in rows:
		names.append(name)
		with conn.cursor() as cur:
			cur.execute(
				"INSERT IGNORE INTO `tabMemora Interaction Log` "
				"(`name`, `player`, `lesson`, `stage_id`, `item_id`, `event_type`, "
				" `time_spent`, `errors_count`, `timestamp`, `client_metadata`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
				"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)",
				(
					name,
					f"{prefix}-PLYR-001",
					f"{prefix}-LESSON-001",
					f"{prefix}-STAGE-001",
					f"{prefix}-ITEM-OOR-001",
					"review",
					5,
					0,
					ts,
					'{"source": "test", "oor": true}',
				),
			)
	conn.commit()
	return names


def _cleanup_test_interactions(conn, prefix: str = FI_PREFIX) -> None:
	"""Delete all test interaction log rows matching the prefix."""
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Interaction Log` WHERE `name` LIKE %s",
			(f"{prefix}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# FI-FULL: Date-range export with 10 columns
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_interaction_date_range_export(analytics_db_config, db_conn, tmp_path):
	"""fact_interaction date-range export produces 10-column Parquet with expected columns."""
	_cleanup_test_interactions(db_conn)
	try:
		in_range_names = _insert_test_interactions(db_conn, count=5)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "fact_interaction" in results
		result = results["fact_interaction"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "fact_interaction.parquet")
		assert os.path.exists(out_path)

		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS
		assert table.num_rows >= len(in_range_names)

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "fact_interaction.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		_cleanup_test_interactions(db_conn)


# ---------------------------------------------------------------------------
# FI-FILT: Only rows within date range are exported
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_interaction_date_range_filter(analytics_db_config, db_conn, tmp_path):
	"""Rows outside the configured date range are excluded from the export."""
	_cleanup_test_interactions(db_conn)
	try:
		in_range_names = _insert_test_interactions(db_conn, count=5)
		oor_names = _insert_out_of_range_interactions(db_conn)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert results["fact_interaction"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_interaction.parquet"))
		exported_ids = set(table.column("event_id").to_pylist())

		# In-range rows must be present
		for name in in_range_names:
			assert name in exported_ids, f"In-range row {name!r} missing from export"

		# Out-of-range rows must NOT be present
		for name in oor_names:
			assert name not in exported_ids, (
				f"Out-of-range row {name!r} should not appear in export"
			)
	finally:
		_cleanup_test_interactions(db_conn)


# ---------------------------------------------------------------------------
# FI-NONULL: No null event_id or player_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_interaction_no_null_keys(analytics_db_config, db_conn, tmp_path):
	"""No null event_id or player_id values in the exported Parquet."""
	_cleanup_test_interactions(db_conn)
	try:
		_insert_test_interactions(db_conn, count=3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_interaction"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_interaction.parquet"))

		event_ids = table.column("event_id").to_pylist()
		assert all(eid is not None for eid in event_ids), "Found null event_id"

		player_ids = table.column("player_id").to_pylist()
		assert all(pid is not None for pid in player_ids), "Found null player_id"
	finally:
		_cleanup_test_interactions(db_conn)
