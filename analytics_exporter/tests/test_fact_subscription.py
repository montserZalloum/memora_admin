"""Integration tests for fact_subscription export.

Scenarios:
  FS-FULL:   Full export -> 8-column Parquet with correct column names.
  FS-LJNULL: LEFT JOIN produces null payment fields when no matching transaction.
  FS-NONULL: No null player_id or access_key values in output.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_fact_subscription.py -v
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

FSUB_PREFIX = "TEST-FSUB"
SUBSCRIPTION_TABLE = "tabMemora Player Subscription"
TRANSACTION_TABLE = "tabMemora Subscription Transaction"

EXPECTED_COLUMNS = {
	"player_id", "access_key", "is_active", "expires_at",
	"subscribed_at", "payment_method", "amount_paid", "txn_status",
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
		analytics_datasets=["fact_subscription"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_fact_subscription")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _insert_test_subscriptions(conn, rows: list[tuple]) -> None:
	"""Insert test subscription rows.

	Each tuple: (name, player, access_key, is_active, expires_at)
	"""
	sql = (
		"INSERT IGNORE INTO `tabMemora Player Subscription` "
		"(`name`, `player`, `access_key`, `is_active`, `expires_at`, "
		" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
		"VALUES (%s, %s, %s, %s, %s, "
		"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)"
	)
	with conn.cursor() as cur:
		cur.executemany(sql, rows)
	conn.commit()


def _insert_test_transactions(conn, rows: list[tuple]) -> None:
	"""Insert test transaction rows.

	Each tuple: (name, player, payment_method, status, amount_paid)
	"""
	sql = (
		"INSERT IGNORE INTO `tabMemora Subscription Transaction` "
		"(`name`, `player`, `payment_method`, `status`, `amount_paid`, "
		" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
		"VALUES (%s, %s, %s, %s, %s, "
		"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)"
	)
	with conn.cursor() as cur:
		cur.executemany(sql, rows)
	conn.commit()


def _cleanup(conn) -> None:
	"""Delete all test data from both tables."""
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Player Subscription` WHERE `name` LIKE %s",
			(f"{FSUB_PREFIX}-%",),
		)
		cur.execute(
			"DELETE FROM `tabMemora Subscription Transaction` WHERE `name` LIKE %s",
			(f"{FSUB_PREFIX}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# FS-FULL: Full export with 8 columns
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_subscription_full_export(analytics_db_config, db_conn, tmp_path):
	"""fact_subscription export produces 8-column Parquet with expected column names."""
	_cleanup(db_conn)
	try:
		_insert_test_subscriptions(db_conn, [
			(f"{FSUB_PREFIX}-SUB-001", f"{FSUB_PREFIX}-PLYR-001", "SUB-SUBJ-TEST-001", 1, "2099-12-31"),
			(f"{FSUB_PREFIX}-SUB-002", f"{FSUB_PREFIX}-PLYR-002", "SUB-SUBJ-TEST-002", 1, "2099-12-31"),
			(f"{FSUB_PREFIX}-SUB-003", f"{FSUB_PREFIX}-PLYR-003", "SUB-SUBJ-TEST-003", 0, "2099-06-30"),
		])
		_insert_test_transactions(db_conn, [
			(f"{FSUB_PREFIX}-TXN-001", f"{FSUB_PREFIX}-PLYR-001", "Voucher", "Approved", 99.99),
			(f"{FSUB_PREFIX}-TXN-002", f"{FSUB_PREFIX}-PLYR-002", "Card", "Approved", 149.99),
			(f"{FSUB_PREFIX}-TXN-003", f"{FSUB_PREFIX}-PLYR-003", "Voucher", "Pending", 0.00),
		])

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "fact_subscription" in results
		result = results["fact_subscription"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "fact_subscription.parquet")
		assert os.path.exists(out_path)

		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS
		assert table.num_rows >= 3

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "fact_subscription.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		_cleanup(db_conn)


# ---------------------------------------------------------------------------
# FS-LJNULL: LEFT JOIN produces null payment fields
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_subscription_left_join_null(analytics_db_config, db_conn, tmp_path):
	"""LEFT JOIN produces null payment_method, amount_paid, txn_status when
	no matching transaction exists for a subscription."""
	_cleanup(db_conn)
	try:
		# Player 001 has a transaction; Player 002 does NOT
		_insert_test_subscriptions(db_conn, [
			(f"{FSUB_PREFIX}-SUB-001", f"{FSUB_PREFIX}-PLYR-001", "SUB-SUBJ-TEST-001", 1, "2099-12-31"),
			(f"{FSUB_PREFIX}-SUB-002", f"{FSUB_PREFIX}-PLYR-002", "SUB-SUBJ-TEST-002", 1, "2099-12-31"),
		])
		_insert_test_transactions(db_conn, [
			(f"{FSUB_PREFIX}-TXN-001", f"{FSUB_PREFIX}-PLYR-001", "Voucher", "Approved", 99.99),
		])

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert results["fact_subscription"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_subscription.parquet"))
		player_ids = table.column("player_id").to_pylist()

		# Find the row for Player 002 (no transaction)
		assert f"{FSUB_PREFIX}-PLYR-002" in player_ids, (
			f"Player 002 not found in output; got: {player_ids}"
		)
		idx = player_ids.index(f"{FSUB_PREFIX}-PLYR-002")

		assert table.column("payment_method")[idx].as_py() is None, (
			"Expected null payment_method for player without transaction"
		)
		assert table.column("amount_paid")[idx].as_py() is None, (
			"Expected null amount_paid for player without transaction"
		)
		assert table.column("txn_status")[idx].as_py() is None, (
			"Expected null txn_status for player without transaction"
		)

		# Verify Player 001 *does* have transaction data
		idx1 = player_ids.index(f"{FSUB_PREFIX}-PLYR-001")
		assert table.column("payment_method")[idx1].as_py() == "Voucher"
		assert table.column("txn_status")[idx1].as_py() == "Approved"
	finally:
		_cleanup(db_conn)


# ---------------------------------------------------------------------------
# FS-NONULL: No null player_id or access_key
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fact_subscription_no_null_keys(analytics_db_config, db_conn, tmp_path):
	"""No null player_id or access_key values in output."""
	_cleanup(db_conn)
	try:
		_insert_test_subscriptions(db_conn, [
			(f"{FSUB_PREFIX}-SUB-001", f"{FSUB_PREFIX}-PLYR-001", "SUB-SUBJ-TEST-001", 1, "2099-12-31"),
			(f"{FSUB_PREFIX}-SUB-002", f"{FSUB_PREFIX}-PLYR-002", "SUB-SUBJ-TEST-002", 0, "2099-06-30"),
		])

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["fact_subscription"].success

		table = pq.read_table(os.path.join(str(tmp_path), "fact_subscription.parquet"))
		player_ids = table.column("player_id").to_pylist()
		access_keys = table.column("access_key").to_pylist()

		assert all(pid is not None for pid in player_ids), "Found null player_id"
		assert all(ak is not None for ak in access_keys), "Found null access_key"
	finally:
		_cleanup(db_conn)
