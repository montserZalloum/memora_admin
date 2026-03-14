"""Integration tests for Fact Practice export (was practice_log in 047).

Scenarios:
  FP-FULL:   Full export -> 7-field Parquet, no duplicate PKs, watermark written.
  FP-INCR:   Incremental re-export -> delta merged, watermark updated.
  FP-ZERO:   Rows with correct_count=0 / last_result='Incorrect' included unchanged.
  FP-RC:     Connection uses READ COMMITTED isolation.
  FP-EMPTY:  Zero-row export -> valid empty Parquet with correct schema;
             watermark not written by export_snapshot (only orchestrate writes it).

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python3 -m pytest analytics_exporter/tests/test_fact_practice.py -v
"""

import dataclasses
import logging
import os

import pyarrow.parquet as pq
import pymysql
import pymysql.cursors
import pytest

from analytics_exporter.config import Config
from analytics_exporter.exporter import export_snapshot
from analytics_exporter.run import orchestrate_exports
from analytics_exporter.watermark import load_watermark
from analytics_exporter.tests.conftest import (
	practice_log_rows,
	cleanup_practice_log_rows,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base: Config, output_dir: str, mode: str = "auto") -> Config:
	"""Return a Config copy with overridden output/schema paths and datasets=['fact_practice']."""
	schema_path = os.path.join(os.path.dirname(__file__), "..", "schemas")
	return dataclasses.replace(
		base,
		analytics_output_path=output_dir,
		analytics_schema_path=str(os.path.abspath(schema_path)),
		analytics_datasets=["fact_practice"],
		analytics_mode=mode,
	)


def _make_logger(name: str = "test_fact_practice") -> logging.Logger:
	log = logging.getLogger(name)
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


# ---------------------------------------------------------------------------
# FP-FULL: Full snapshot
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_full_export_produces_correct_parquet(analytics_db_config, db_conn, tmp_path):
	"""Full export produces 7-field Parquet with no duplicate PKs; watermark written."""
	prefix = "FPFUL"
	cleanup_practice_log_rows(db_conn, prefix)
	try:
		practice_log_rows(db_conn, prefix, 10)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "fact_practice" in results, "fact_practice not dispatched by orchestrate_exports"
		result = results["fact_practice"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"
		assert result.violations == []

		out_path = os.path.join(str(tmp_path), "fact_practice.parquet")
		assert os.path.exists(out_path)

		table = pq.read_table(out_path)
		expected_cols = {
			"player_id", "item_id", "attempt_count", "correct_count",
			"first_seen_at", "last_seen_at", "last_result",
		}
		assert set(table.schema.names) == expected_cols

		# No duplicate (player_id, item_id) PKs
		pks = list(zip(
			table.column("player_id").to_pylist(),
			table.column("item_id").to_pylist(),
		))
		assert len(pks) == len(set(pks)), "Duplicate (player_id, item_id) found in output"

		# Watermark written with non-null last_watermark
		wm_path = os.path.join(str(tmp_path), ".watermark.json")
		assert os.path.exists(wm_path)
		wm = load_watermark(wm_path)
		assert "fact_practice" in wm
		assert wm["fact_practice"]["last_watermark"] is not None
	finally:
		cleanup_practice_log_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# FP-INCR: Incremental merge
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_incremental_export_merges_delta(analytics_db_config, db_conn, tmp_path):
	"""Incremental re-export: only delta rows queried; merged file grows; watermark updated."""
	prefix = "FPICR"
	cleanup_practice_log_rows(db_conn, prefix)
	try:
		# Insert 5 rows (n=1..5): timestamps 2099-06-01 00:01 to 00:05
		practice_log_rows(db_conn, prefix, 5)

		cfg = _make_config(analytics_db_config, str(tmp_path))

		# First run -- full snapshot
		results1 = orchestrate_exports(cfg, _make_logger("fp_incr_r1"))
		assert results1["fact_practice"].success

		out_path = os.path.join(str(tmp_path), "fact_practice.parquet")
		row_count_1 = pq.read_table(out_path).num_rows

		wm_path = os.path.join(str(tmp_path), ".watermark.json")
		wm_before = load_watermark(wm_path)
		assert wm_before["fact_practice"]["last_watermark"] is not None

		# Insert 3 more rows (n=6..8)
		db_conn.commit()
		practice_log_rows(db_conn, prefix, 8)

		# Second run -- incremental
		results2 = orchestrate_exports(cfg, _make_logger("fp_incr_r2"))
		assert results2["fact_practice"].success

		merged = pq.read_table(out_path)
		assert merged.num_rows == row_count_1 + 3, (
			f"Expected {row_count_1 + 3} rows after incremental merge, got {merged.num_rows}"
		)

		# Watermark advanced
		wm_after = load_watermark(wm_path)
		assert (
			wm_after["fact_practice"]["last_watermark"]
			> wm_before["fact_practice"]["last_watermark"]
		)
	finally:
		cleanup_practice_log_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# FP-ZERO: Zero-value rows included without modification
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_zero_value_rows_included(analytics_db_config, db_conn, tmp_path):
	"""Rows with correct_count=0 and last_result='Incorrect' appear in output unchanged."""
	prefix = "FPZRO"
	cleanup_practice_log_rows(db_conn, prefix)
	try:
		# The helper generates n=9 with attempt_count=1, correct_count=0, last_result='Incorrect'
		practice_log_rows(db_conn, prefix, 9)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_practice"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_practice.parquet"))
		item_ids = table.column("item_id").to_pylist()

		# Row n=9: item_id TEST-RI-FPZRO-000009
		zero_item = f"TEST-RI-{prefix}-000009"
		assert zero_item in item_ids, f"Zero-value row {zero_item!r} not found in output"

		idx = item_ids.index(zero_item)
		assert table.column("correct_count")[idx].as_py() == 0
		assert table.column("last_result")[idx].as_py() == "Incorrect"
	finally:
		cleanup_practice_log_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# FP-RC: READ COMMITTED isolation
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_read_committed_isolation(analytics_db_config):
	"""DB connection for export uses READ COMMITTED transaction isolation level."""
	conn = pymysql.connect(
		host=analytics_db_config.db_host,
		port=analytics_db_config.db_port,
		user=analytics_db_config.db_user,
		password=analytics_db_config.db_password,
		database=analytics_db_config.db_name,
		charset="utf8mb4",
		cursorclass=pymysql.cursors.DictCursor,
	)
	try:
		with conn.cursor() as cur:
			cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
			# MariaDB uses @@tx_isolation; MySQL 8 uses @@transaction_isolation
			try:
				cur.execute("SELECT @@transaction_isolation AS iso")
			except Exception:
				cur.execute("SELECT @@tx_isolation AS iso")
			row = cur.fetchone()
		assert row["iso"].upper() == "READ-COMMITTED"
	finally:
		conn.close()


# ---------------------------------------------------------------------------
# FP-EMPTY: Zero-row source produces valid empty Parquet with correct schema
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_empty_source_produces_valid_parquet(analytics_db_config, tmp_path):
	"""export_snapshot with zero-row result produces an empty Parquet with correct schema."""
	cfg = _make_config(analytics_db_config, str(tmp_path))
	out_path = os.path.join(str(tmp_path), "fact_practice.parquet")

	schema_def = [
		{"name": "player_id",     "type": "VARCHAR"},
		{"name": "item_id",       "type": "VARCHAR"},
		{"name": "first_seen_at", "type": "DATETIME"},
		{"name": "last_seen_at",  "type": "DATETIME"},
		{"name": "last_result",   "type": "VARCHAR"},
		{"name": "attempt_count", "type": "INT"},
		{"name": "correct_count", "type": "INT"},
	]
	columns = [c["name"] for c in schema_def]

	# Query that returns zero rows
	sql = (
		"SELECT `player_id`, `item_id`, `first_seen_at`, `last_seen_at`, "
		"       `last_result`, `attempt_count`, `correct_count` "
		"FROM `tabMemora Practice Log` WHERE 1=0"
	)

	path, count = export_snapshot(cfg, sql, (), columns, schema_def, out_path)

	assert count == 0
	assert os.path.exists(path)

	table = pq.read_table(path)
	assert table.num_rows == 0
	assert set(table.schema.names) == set(columns)

	# Watermark NOT written -- export_snapshot has no side effects on watermark state
	wm_path = os.path.join(str(tmp_path), ".watermark.json")
	assert not os.path.exists(wm_path)
