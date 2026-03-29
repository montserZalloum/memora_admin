"""Integration tests for dim_review_item export.

Scenarios:
  RI-FULL:    Full export -> 6-column Parquet with correct columns.
  RI-NONULL:  No null item_id values in output.
  RI-NEW:     New columns (question_text, correct_choice) present.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_dim_review_item.py -v
"""

import dataclasses
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports
from analytics_exporter.tests.conftest import RI_ITEM_PREFIX


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DRI_PREFIX = "DIMRI"

EXPECTED_COLUMNS = {
	"item_id", "subject_id", "topic_id", "lesson_id",
	"question_text", "correct_choice",
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
		analytics_datasets=["dim_review_item"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_dim_review_item")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _insert_test_review_items(conn, count: int = 3) -> list[str]:
	"""Insert review items with all new fields populated. Returns item_ids."""
	ids = []
	for n in range(1, count + 1):
		item_id = f"{RI_ITEM_PREFIX}-{DRI_PREFIX}-{n:06d}"
		ids.append(item_id)
		with conn.cursor() as cur:
			cur.execute(
				"INSERT IGNORE INTO `tabMemora Review Item` "
				"(`name`, `item_id`, `subject`, `topic`, `lesson`, "
				" `question_text`, `correct_choice`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
				"VALUES (%s, %s, %s, %s, %s, %s, %s, "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s)",
				(item_id, item_id,
				 f"SUBJ-{DRI_PREFIX}", f"TOPIC-{DRI_PREFIX}", f"LESSON-{DRI_PREFIX}",
				 f"What is {n}+{n}?", n % 4 + 1,
				 n),
			)
	conn.commit()
	return ids


def _cleanup_test_review_items(conn) -> None:
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Review Item` WHERE `name` LIKE %s",
			(f"{RI_ITEM_PREFIX}-{DRI_PREFIX}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# RI-FULL: Full export with 6 columns
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_review_item_full_export(analytics_db_config, db_conn, tmp_path):
	"""dim_review_item export produces 6-column Parquet with correct column names."""
	_cleanup_test_review_items(db_conn)
	try:
		inserted_ids = _insert_test_review_items(db_conn, 3)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "dim_review_item" in results
		result = results["dim_review_item"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "dim_review_item.parquet")
		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS

		# Our test items are present
		all_ids = table.column("item_id").to_pylist()
		for iid in inserted_ids:
			assert iid in all_ids, f"Test item {iid} not found in output"

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "dim_review_item.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		_cleanup_test_review_items(db_conn)


# ---------------------------------------------------------------------------
# RI-NONULL: No null item_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_review_item_no_null_item_id(analytics_db_config, db_conn, tmp_path):
	"""No null item_id values in output."""
	_cleanup_test_review_items(db_conn)
	try:
		_insert_test_review_items(db_conn, 2)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_review_item"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_review_item.parquet"))
		item_ids = table.column("item_id").to_pylist()
		assert all(iid is not None for iid in item_ids), "Found null item_id"
	finally:
		_cleanup_test_review_items(db_conn)


# ---------------------------------------------------------------------------
# RI-NEW: New columns present and populated
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_review_item_new_columns_populated(analytics_db_config, db_conn, tmp_path):
	"""New columns (question_text, correct_choice) are present and populated."""
	_cleanup_test_review_items(db_conn)
	try:
		inserted_ids = _insert_test_review_items(db_conn, 1)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_review_item"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_review_item.parquet"))
		all_ids = table.column("item_id").to_pylist()
		idx = all_ids.index(inserted_ids[0])

		assert table.column("question_text")[idx].as_py() == "What is 1+1?"
		assert table.column("correct_choice")[idx].as_py() == 2  # (1 % 4) + 1 = 2
	finally:
		_cleanup_test_review_items(db_conn)
