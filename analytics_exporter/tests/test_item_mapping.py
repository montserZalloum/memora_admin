"""Integration tests for Item Mapping export — US2.

Scenarios:
  IM-FULL: Full export → 6-field Parquet, no null columns, one row per item_id.
  IM-EXCL: Items with null/empty lesson excluded from output.
  IM-JOIN: Every active item_id in practice log resolves to a row in item_mapping
           (LEFT JOIN on item_id produces zero unresolved rows). [T020]

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python3 -m pytest analytics_exporter/tests/test_item_mapping.py -v
"""

import dataclasses
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports
from analytics_exporter.tests.conftest import (
	cleanup_hierarchy_rows,
	cleanup_practice_log_rows,
	cleanup_review_item_rows,
	hierarchy_rows,
	practice_log_rows,
	review_item_rows,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base: Config, output_dir: str, datasets: list[str]) -> Config:
	schema_path = os.path.join(os.path.dirname(__file__), "..", "schemas")
	return dataclasses.replace(
		base,
		analytics_output_path=output_dir,
		analytics_schema_path=str(os.path.abspath(schema_path)),
		analytics_datasets=datasets,
		analytics_mode="full",
	)


def _make_logger(name: str = "test_item_mapping") -> logging.Logger:
	log = logging.getLogger(name)
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


# ---------------------------------------------------------------------------
# IM-FULL: Full snapshot produces 6-field Parquet, no nulls, one row per item_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_full_export_produces_correct_parquet(analytics_db_config, db_conn, tmp_path):
	"""Full export: 6-field Parquet with no null columns, one row per item_id."""
	prefix = "IMFUL"
	cleanup_review_item_rows(db_conn, prefix)
	cleanup_hierarchy_rows(db_conn, prefix)
	try:
		hi = hierarchy_rows(db_conn, prefix)
		review_item_rows(
			db_conn, prefix, 5,
			subject_id=hi["subject_id"],
			track_id=hi["track_id"],
			unit_id=hi["unit_id"],
			topic_id=hi["topic_id"],
			lesson_id=hi["lesson_id"],
		)

		cfg = _make_config(analytics_db_config, str(tmp_path), ["item_mapping"])
		results = orchestrate_exports(cfg, _make_logger())

		assert "item_mapping" in results, "item_mapping not dispatched by orchestrate_exports"
		result = results["item_mapping"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"
		assert result.violations == []

		out_path = os.path.join(str(tmp_path), "item_mapping.parquet")
		assert os.path.exists(out_path)

		table = pq.read_table(out_path)
		expected_cols = {"item_id", "lesson_id", "topic_id", "unit_id", "track_id", "subject_id"}
		assert set(table.schema.names) == expected_cols

		# No null values in any column
		for col_name in expected_cols:
			null_count = table.column(col_name).null_count
			assert null_count == 0, f"Column {col_name!r} has {null_count} null values"

		# One row per item_id (no duplicates)
		item_ids = table.column("item_id").to_pylist()
		assert len(item_ids) == len(set(item_ids)), "Duplicate item_id found in output"

		# Our test rows must appear
		for n in range(1, 6):
			item_id = f"TEST-RI-{prefix}-{n:06d}"
			assert item_id in item_ids, f"Expected item_id {item_id!r} in output"

	finally:
		cleanup_review_item_rows(db_conn, prefix)
		cleanup_hierarchy_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# IM-EXCL: Items with null/empty lesson excluded from output
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_items_missing_lesson_excluded(analytics_db_config, db_conn, tmp_path):
	"""Items with null or empty lesson field are excluded from item_mapping output."""
	prefix = "IMEXC"
	null_lesson_item = f"TEST-RI-{prefix}-NULLL"
	empty_lesson_item = f"TEST-RI-{prefix}-EMPT"
	cleanup_review_item_rows(db_conn, prefix)
	cleanup_hierarchy_rows(db_conn, prefix)
	try:
		hi = hierarchy_rows(db_conn, prefix)

		# Insert 3 items with complete hierarchy (should appear in output)
		review_item_rows(
			db_conn, prefix, 3,
			subject_id=hi["subject_id"],
			track_id=hi["track_id"],
			unit_id=hi["unit_id"],
			topic_id=hi["topic_id"],
			lesson_id=hi["lesson_id"],
		)

		# Insert 1 item with null lesson (should be excluded)
		with db_conn.cursor() as cursor:
			cursor.execute(
				"INSERT IGNORE INTO `tabMemora Review Item` "
				"(`name`, `item_id`, `lesson`, `topic`, `unit`, `track`, `subject`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
				"VALUES (%s, %s, NULL, %s, %s, %s, %s, "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 99)",
				(null_lesson_item, null_lesson_item,
				 hi["topic_id"], hi["unit_id"], hi["track_id"], hi["subject_id"]),
			)
			# Insert 1 item with empty string lesson (should also be excluded)
			cursor.execute(
				"INSERT IGNORE INTO `tabMemora Review Item` "
				"(`name`, `item_id`, `lesson`, `topic`, `unit`, `track`, `subject`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
				"VALUES (%s, %s, '', %s, %s, %s, %s, "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 100)",
				(empty_lesson_item, empty_lesson_item,
				 hi["topic_id"], hi["unit_id"], hi["track_id"], hi["subject_id"]),
			)
		db_conn.commit()

		cfg = _make_config(analytics_db_config, str(tmp_path), ["item_mapping"])
		results = orchestrate_exports(cfg, _make_logger("im_excl"))

		assert results["item_mapping"].success, (
			f"Export failed: {results['item_mapping'].error}"
		)

		table = pq.read_table(os.path.join(str(tmp_path), "item_mapping.parquet"))
		item_ids = table.column("item_id").to_pylist()

		# Null-lesson item must NOT appear
		assert null_lesson_item not in item_ids, (
			f"Item with null lesson {null_lesson_item!r} should be excluded"
		)
		# Empty-lesson item must NOT appear
		assert empty_lesson_item not in item_ids, (
			f"Item with empty lesson {empty_lesson_item!r} should be excluded"
		)
		# Complete items must appear
		for n in range(1, 4):
			item_id = f"TEST-RI-{prefix}-{n:06d}"
			assert item_id in item_ids, f"Complete item {item_id!r} should appear in output"

	finally:
		with db_conn.cursor() as cursor:
			cursor.execute(
				"DELETE FROM `tabMemora Review Item` WHERE `name` IN (%s, %s)",
				(null_lesson_item, empty_lesson_item),
			)
		db_conn.commit()
		cleanup_review_item_rows(db_conn, prefix)
		cleanup_hierarchy_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# IM-JOIN: Every active item_id in practice log resolves to item_mapping row [T020]
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_practice_log_join_resolves_completely(analytics_db_config, db_conn, tmp_path):
	"""LEFT JOIN practice_log → item_mapping on item_id produces zero unresolved rows
	for items that have complete curriculum assignments.
	"""
	prefix = "IMJOIN"
	cleanup_practice_log_rows(db_conn, prefix)
	cleanup_review_item_rows(db_conn, prefix)
	cleanup_hierarchy_rows(db_conn, prefix)
	try:
		hi = hierarchy_rows(db_conn, prefix)

		# Insert review items with complete hierarchy
		review_item_rows(
			db_conn, prefix, 5,
			subject_id=hi["subject_id"],
			track_id=hi["track_id"],
			unit_id=hi["unit_id"],
			topic_id=hi["topic_id"],
			lesson_id=hi["lesson_id"],
		)
		# Insert practice log rows referencing those items
		practice_log_rows(db_conn, prefix, 5)

		# Export practice_log
		cfg_pl = _make_config(analytics_db_config, str(tmp_path), ["practice_log"])
		results_pl = orchestrate_exports(cfg_pl, _make_logger("im_join_pl"))
		assert results_pl["practice_log"].success, (
			f"practice_log export failed: {results_pl['practice_log'].error}"
		)

		# Export item_mapping
		cfg_im = _make_config(analytics_db_config, str(tmp_path), ["item_mapping"])
		results_im = orchestrate_exports(cfg_im, _make_logger("im_join_im"))
		assert results_im["item_mapping"].success, (
			f"item_mapping export failed: {results_im['item_mapping'].error}"
		)

		pl_table = pq.read_table(os.path.join(str(tmp_path), "practice_log.parquet"))
		im_table = pq.read_table(os.path.join(str(tmp_path), "item_mapping.parquet"))

		pl_item_ids = set(pl_table.column("item_id").to_pylist())
		im_item_ids = set(im_table.column("item_id").to_pylist())
		im_lesson_map = dict(zip(
			im_table.column("item_id").to_pylist(),
			im_table.column("lesson_id").to_pylist(),
		))

		# Every test item_id that appears in practice_log must resolve in item_mapping
		test_item_ids = {f"TEST-RI-{prefix}-{n:06d}" for n in range(1, 6)}
		for item_id in test_item_ids:
			if item_id in pl_item_ids:
				assert item_id in im_item_ids, (
					f"item_id {item_id!r} found in practice_log but missing from item_mapping"
				)
				lesson_id = im_lesson_map.get(item_id)
				assert lesson_id is not None, (
					f"lesson_id is null for item_id {item_id!r} in item_mapping"
				)

	finally:
		cleanup_practice_log_rows(db_conn, prefix)
		cleanup_review_item_rows(db_conn, prefix)
		cleanup_hierarchy_rows(db_conn, prefix)
