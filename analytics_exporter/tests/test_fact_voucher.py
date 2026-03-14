"""Integration tests for fact_voucher export.

Scenarios:
  FV-FULL:  Full export -> 12-column Parquet with correct column names.
  FV-FLOAT: face_value exported as float64 (not Decimal).
  FV-NONULL: No null serial_no or batch_id in output.
  FV-LJOIN: LEFT JOIN on allocation: allocated card has allocation_date;
            unallocated card has allocation_date null.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_fact_voucher.py -v
"""

import dataclasses
import logging
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FV_PREFIX = "TEST-FV"

BATCH_TABLE = "tabMemora Voucher Batch"
CARD_TABLE = "tabMemora Voucher Card"
ALLOCATION_TABLE = "tabMemora Voucher Allocation"

EXPECTED_COLUMNS = {
	"serial_no", "batch_id", "batch_name", "batch_purpose",
	"face_value", "card_status", "library", "sale_model",
	"redeemed_by", "redeemed_at", "allocation_date", "allocated_to",
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
		analytics_datasets=["fact_voucher"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_fact_voucher")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _insert_test_data(conn) -> None:
	"""Insert a batch, an allocation, and two cards (one with allocation, one without)."""
	with conn.cursor() as cur:
		# Batch
		cur.execute(
			"INSERT IGNORE INTO `tabMemora Voucher Batch` "
			"(`name`, `batch_name`, `batch_purpose`, `face_value`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)",
			(f"{FV_PREFIX}-BATCH-001", "Test Batch", "Sale", "50.000000000"),
		)

		# Allocation
		cur.execute(
			"INSERT IGNORE INTO `tabMemora Voucher Allocation` "
			"(`name`, `batch`, `customer`, `allocation_date`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)",
			(f"{FV_PREFIX}-ALLOC-001", f"{FV_PREFIX}-BATCH-001",
			 f"{FV_PREFIX}-CUST-001", "2099-06-01"),
		)

		# Card WITH allocation
		cur.execute(
			"INSERT IGNORE INTO `tabMemora Voucher Card` "
			"(`name`, `serial_no`, `batch`, `allocation`, `status`, "
			" `library`, `sale_model`, `redeemed_by`, `redeemed_at`, `batch_purpose`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
			"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)",
			(f"{FV_PREFIX}-CARD-001", f"{FV_PREFIX}-SN-001",
			 f"{FV_PREFIX}-BATCH-001", f"{FV_PREFIX}-ALLOC-001",
			 "Allocated", "TestLib", "Direct", None, None, "Sale"),
		)

		# Card WITHOUT allocation
		cur.execute(
			"INSERT IGNORE INTO `tabMemora Voucher Card` "
			"(`name`, `serial_no`, `batch`, `allocation`, `status`, "
			" `library`, `sale_model`, `redeemed_by`, `redeemed_at`, `batch_purpose`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
			"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)",
			(f"{FV_PREFIX}-CARD-002", f"{FV_PREFIX}-SN-002",
			 f"{FV_PREFIX}-BATCH-001", None,
			 "Available", "TestLib", "Direct", None, None, "Sale"),
		)
	conn.commit()


def _cleanup_test_data(conn) -> None:
	"""Delete test rows in FK-safe order: cards -> allocation -> batch."""
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Voucher Card` WHERE `name` LIKE %s",
			(f"{FV_PREFIX}-%",),
		)
		cur.execute(
			"DELETE FROM `tabMemora Voucher Allocation` WHERE `name` LIKE %s",
			(f"{FV_PREFIX}-%",),
		)
		cur.execute(
			"DELETE FROM `tabMemora Voucher Batch` WHERE `name` LIKE %s",
			(f"{FV_PREFIX}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# FV-FULL: Full export with 12 columns
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_voucher_full_export(analytics_db_config, db_conn, tmp_path):
	"""fact_voucher export produces 12-column Parquet with expected column names."""
	_cleanup_test_data(db_conn)
	try:
		_insert_test_data(db_conn)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "fact_voucher" in results
		result = results["fact_voucher"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "fact_voucher.parquet")
		assert os.path.exists(out_path)

		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS
		assert table.num_rows >= 2

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "fact_voucher.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		_cleanup_test_data(db_conn)


# ---------------------------------------------------------------------------
# FV-FLOAT: face_value exported as float64
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_voucher_face_value_float(analytics_db_config, db_conn, tmp_path):
	"""face_value is exported as float64, not Decimal."""
	_cleanup_test_data(db_conn)
	try:
		_insert_test_data(db_conn)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_voucher"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_voucher.parquet"))

		# PyArrow column type must be float64
		face_value_field = table.schema.field("face_value")
		assert face_value_field.type == pa.float64(), (
			f"Expected float64 for face_value, got {face_value_field.type}"
		)

		# Python values must be float, not Decimal
		values = table.column("face_value").to_pylist()
		for val in values:
			if val is not None:
				assert isinstance(val, float), (
					f"face_value should be float, got {type(val).__name__}"
				)
	finally:
		_cleanup_test_data(db_conn)


# ---------------------------------------------------------------------------
# FV-NONULL: No null serial_no or batch_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_voucher_no_null_keys(analytics_db_config, db_conn, tmp_path):
	"""No null serial_no or batch_id values in output."""
	_cleanup_test_data(db_conn)
	try:
		_insert_test_data(db_conn)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_voucher"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_voucher.parquet"))
		serial_nos = table.column("serial_no").to_pylist()
		batch_ids = table.column("batch_id").to_pylist()

		assert all(sn is not None for sn in serial_nos), "Found null serial_no"
		assert all(bid is not None for bid in batch_ids), "Found null batch_id"
	finally:
		_cleanup_test_data(db_conn)


# ---------------------------------------------------------------------------
# FV-LJOIN: LEFT JOIN on allocation
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_voucher_left_join_allocation(analytics_db_config, db_conn, tmp_path):
	"""LEFT JOIN: allocated card has allocation_date; unallocated card has null."""
	_cleanup_test_data(db_conn)
	try:
		_insert_test_data(db_conn)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_voucher"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_voucher.parquet"))

		serial_nos = table.column("serial_no").to_pylist()
		alloc_dates = table.column("allocation_date").to_pylist()
		allocated_tos = table.column("allocated_to").to_pylist()

		# Card WITH allocation (TEST-FV-SN-001) -> allocation_date populated
		sn1 = f"{FV_PREFIX}-SN-001"
		assert sn1 in serial_nos, f"{sn1} not found in output"
		idx1 = serial_nos.index(sn1)
		assert alloc_dates[idx1] is not None, (
			f"Card {sn1} has allocation but allocation_date is null"
		)
		assert allocated_tos[idx1] is not None, (
			f"Card {sn1} has allocation but allocated_to is null"
		)

		# Card WITHOUT allocation (TEST-FV-SN-002) -> allocation_date null
		sn2 = f"{FV_PREFIX}-SN-002"
		assert sn2 in serial_nos, f"{sn2} not found in output"
		idx2 = serial_nos.index(sn2)
		assert alloc_dates[idx2] is None, (
			f"Card {sn2} has no allocation but allocation_date is not null"
		)
		assert allocated_tos[idx2] is None, (
			f"Card {sn2} has no allocation but allocated_to is not null"
		)
	finally:
		_cleanup_test_data(db_conn)
