"""Integration tests for dim_season export.

Scenarios:
  DS-FULL:   Full export -> 6-column Parquet with correct columns.
  DS-ISPUB:  is_published column is present (new vs old schema).
  DS-NONULL: No null season_id or season_seq.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_dim_season.py -v
"""

import dataclasses
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports
from analytics_exporter.tests.conftest import (
	academic_context_rows,
	cleanup_academic_context_rows,
	AC_PREFIX,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DS_PREFIX = "DIMSN"

EXPECTED_COLUMNS = {
	"season_id", "season_title", "season_seq",
	"start_date", "end_date", "is_published",
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
		analytics_datasets=["dim_season"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_dim_season")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


# ---------------------------------------------------------------------------
# DS-FULL: Full export with 6 columns
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_season_full_export(analytics_db_config, db_conn, tmp_path):
	"""dim_season export produces 6-column Parquet with correct columns."""
	cleanup_academic_context_rows(db_conn, DS_PREFIX)
	try:
		ac = academic_context_rows(db_conn, DS_PREFIX, season_seq=9980)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "dim_season" in results
		result = results["dim_season"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "dim_season.parquet")
		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS

		# Our test season is in the output
		season_ids = table.column("season_id").to_pylist()
		assert ac["season_id"] in season_ids

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "dim_season.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		cleanup_academic_context_rows(db_conn, DS_PREFIX)


# ---------------------------------------------------------------------------
# DS-ISPUB: is_published column present
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_season_has_is_published(analytics_db_config, db_conn, tmp_path):
	"""is_published column is present in dim_season output (new vs old schema)."""
	cleanup_academic_context_rows(db_conn, DS_PREFIX)
	try:
		ac = academic_context_rows(db_conn, DS_PREFIX, season_seq=9981)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_season"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_season.parquet"))
		assert "is_published" in table.schema.names

		# Our test season has is_published=1
		season_ids = table.column("season_id").to_pylist()
		idx = season_ids.index(ac["season_id"])
		assert table.column("is_published")[idx].as_py() == 1
	finally:
		cleanup_academic_context_rows(db_conn, DS_PREFIX)


# ---------------------------------------------------------------------------
# DS-NONULL: No null season_id or season_seq
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_season_no_null_keys(analytics_db_config, db_conn, tmp_path):
	"""No null season_id or season_seq values in output."""
	cleanup_academic_context_rows(db_conn, DS_PREFIX)
	try:
		academic_context_rows(db_conn, DS_PREFIX, season_seq=9982)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_season"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_season.parquet"))
		season_ids = table.column("season_id").to_pylist()
		season_seqs = table.column("season_seq").to_pylist()

		assert all(sid is not None for sid in season_ids), "Found null season_id"
		assert all(seq is not None for seq in season_seqs), "Found null season_seq"
	finally:
		cleanup_academic_context_rows(db_conn, DS_PREFIX)
