"""Integration tests for dim_player export.

Scenarios:
  DP-FULL:   Full export -> 8-column Parquet with correct column names.
  DP-NONULL: No null player_id values in output.
  DP-EXCL:   Sensitive fields (mobile, password) NOT present in output.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_dim_player.py -v
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

DP_PREFIX = "TEST-DP"
PLAYER_TABLE = "tabMemora Player Profile"

EXPECTED_COLUMNS = {
	"player_id", "display_name", "grade_id", "major_id",
	"season_id", "gender", "language", "registered_at",
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
		analytics_datasets=["dim_player"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_dim_player")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _insert_test_players(conn, count: int = 3) -> list[str]:
	"""Insert test players with TEST-DP-* prefix. Returns list of player_ids."""
	ids = []
	for n in range(1, count + 1):
		pid = f"{DP_PREFIX}-{n:03d}"
		ids.append(pid)
		with conn.cursor() as cur:
			cur.execute(
				"INSERT IGNORE INTO `tabMemora Player Profile` "
				"(`name`, `display_name`, `grade`, `major`, `season`, "
				" `gender`, `preferred_lang`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
				"VALUES (%s, %s, %s, %s, %s, %s, %s, "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0)",
				(pid, f"Player {n}", None, None, None,
				 "Male" if n % 2 == 0 else "Female", "ar"),
			)
	conn.commit()
	return ids


def _cleanup_test_players(conn) -> None:
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Player Profile` WHERE `name` LIKE %s",
			(f"{DP_PREFIX}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# DP-FULL: Full export with 8 columns
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_player_full_export(analytics_db_config, db_conn, tmp_path):
	"""dim_player export produces 8-column Parquet with expected column names."""
	_cleanup_test_players(db_conn)
	try:
		_insert_test_players(db_conn, 3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "dim_player" in results
		result = results["dim_player"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "dim_player.parquet")
		assert os.path.exists(out_path)

		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS
		assert table.num_rows >= 3

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "dim_player.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		_cleanup_test_players(db_conn)


# ---------------------------------------------------------------------------
# DP-NONULL: No null player_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_player_no_null_player_id(analytics_db_config, db_conn, tmp_path):
	"""No null player_id values in output."""
	_cleanup_test_players(db_conn)
	try:
		_insert_test_players(db_conn, 2)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_player"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_player.parquet"))
		player_ids = table.column("player_id").to_pylist()
		assert all(pid is not None for pid in player_ids), "Found null player_id"
	finally:
		_cleanup_test_players(db_conn)


# ---------------------------------------------------------------------------
# DP-EXCL: Sensitive fields excluded
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_player_excludes_sensitive_fields(analytics_db_config, db_conn, tmp_path):
	"""Sensitive fields (mobile, password) are NOT in the output Parquet."""
	_cleanup_test_players(db_conn)
	try:
		_insert_test_players(db_conn, 1)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_player"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_player.parquet"))
		col_names = set(table.schema.names)
		assert "mobile" not in col_names, "Sensitive field 'mobile' found in output"
		assert "password" not in col_names, "Sensitive field 'password' found in output"
	finally:
		_cleanup_test_players(db_conn)
