"""Integration tests for dim_content_hierarchy export.

Scenarios:
  CH-FULL:    Full export -> 16-column Parquet with denormalized titles.
  CH-STAGE:   stage_count and stage_types from subqueries are populated.
  CH-UNPUB:   Unpublished lessons are excluded from output.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_dim_content_hierarchy.py -v
"""

import dataclasses
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports
from analytics_exporter.tests.conftest import (
	hierarchy_rows,
	cleanup_hierarchy_rows,
	HI_PREFIX,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CH_PREFIX = "DIMCH"

EXPECTED_COLUMNS = {
	"lesson_id", "lesson_title",
	"subject_id", "subject_title",
	"track_id", "track_title",
	"unit_id", "unit_title",
	"topic_id", "topic_title",
	"base_xp", "max_hearts", "is_reviewable", "bit_index",
	"stage_count", "stage_types",
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
		analytics_datasets=["dim_content_hierarchy"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_dim_content_hierarchy")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _insert_lesson_stages(conn, lesson_id: str, stages: list[tuple[str, str]]) -> None:
	"""Insert Lesson Stage child rows. stages = [(stage_id, stage_type), ...]."""
	for idx, (stage_id, stage_type) in enumerate(stages, 1):
		stage_name = f"{lesson_id}-STAGE-{idx:03d}"
		with conn.cursor() as cur:
			cur.execute(
				"INSERT IGNORE INTO `tabMemora Lesson Stage` "
				"(`name`, `stage_id`, `stage_type`, `is_skippable`, "
				" `parent`, `parentfield`, `parenttype`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
				"VALUES (%s, %s, %s, 0, %s, 'stages', 'Memora Lesson', "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s)",
				(stage_name, stage_id, stage_type, lesson_id, idx),
			)
	conn.commit()


def _cleanup_lesson_stages(conn, lesson_id: str) -> None:
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Lesson Stage` WHERE `parent` = %s",
			(lesson_id,),
		)
	conn.commit()


def _insert_unpublished_lesson(conn, prefix: str) -> str:
	"""Insert an unpublished lesson. Returns lesson_id."""
	lesson_id = f"{HI_PREFIX}-LESSON-{prefix}-UNPUB"
	subj_id = f"{HI_PREFIX}-SUBJ-{prefix}-001"
	with conn.cursor() as cur:
		cur.execute(
			"INSERT IGNORE INTO `tabMemora Lesson` "
			"(`name`, `lesson_title`, `subject`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
			" `is_published`, `is_reviewable`) "
			"VALUES (%s, %s, %s, "
			"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1, 0, 0)",
			(lesson_id, f"Unpublished Lesson {prefix}", subj_id),
		)
	conn.commit()
	return lesson_id


def _cleanup_unpublished_lesson(conn, prefix: str) -> None:
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Lesson` WHERE `name` = %s",
			(f"{HI_PREFIX}-LESSON-{prefix}-UNPUB",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# CH-FULL: Full export with 16 columns and denormalized titles
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_content_hierarchy_full_export(analytics_db_config, db_conn, tmp_path):
	"""dim_content_hierarchy has 16 columns with denormalized Subject/Track/Unit/Topic titles."""
	cleanup_hierarchy_rows(db_conn, CH_PREFIX)
	try:
		hi = hierarchy_rows(db_conn, CH_PREFIX)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "dim_content_hierarchy" in results
		result = results["dim_content_hierarchy"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "dim_content_hierarchy.parquet")
		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS, (
			f"Expected {EXPECTED_COLUMNS}, got {set(table.schema.names)}"
		)

		# Find our test lesson
		lesson_ids = table.column("lesson_id").to_pylist()
		assert hi["lesson_id"] in lesson_ids

		idx = lesson_ids.index(hi["lesson_id"])
		assert table.column("subject_title")[idx].as_py() == f"Test Subject {CH_PREFIX}"
		assert table.column("track_title")[idx].as_py() == f"Test Track {CH_PREFIX}"
		assert table.column("unit_title")[idx].as_py() == f"Test Unit {CH_PREFIX}"
		assert table.column("topic_title")[idx].as_py() == f"Test Topic {CH_PREFIX}"

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "dim_content_hierarchy.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		cleanup_hierarchy_rows(db_conn, CH_PREFIX)


# ---------------------------------------------------------------------------
# CH-STAGE: stage_count and stage_types from subqueries
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_content_hierarchy_stage_subqueries(analytics_db_config, db_conn, tmp_path):
	"""stage_count and stage_types are populated from Lesson Stage subqueries."""
	cleanup_hierarchy_rows(db_conn, CH_PREFIX)
	try:
		hi = hierarchy_rows(db_conn, CH_PREFIX)
		lesson_id = hi["lesson_id"]

		# Insert 2 stages with distinct types
		_insert_lesson_stages(db_conn, lesson_id, [
			(f"STG-{CH_PREFIX}-001", "MCQ"),
			(f"STG-{CH_PREFIX}-002", "FillBlank"),
		])

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_content_hierarchy"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_content_hierarchy.parquet"))
		lesson_ids = table.column("lesson_id").to_pylist()
		idx = lesson_ids.index(lesson_id)

		assert table.column("stage_count")[idx].as_py() == 2
		stage_types = table.column("stage_types")[idx].as_py()
		assert "MCQ" in stage_types
		assert "FillBlank" in stage_types
	finally:
		_cleanup_lesson_stages(db_conn, hi["lesson_id"])
		cleanup_hierarchy_rows(db_conn, CH_PREFIX)


# ---------------------------------------------------------------------------
# CH-UNPUB: Unpublished lessons excluded
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_content_hierarchy_excludes_unpublished(analytics_db_config, db_conn, tmp_path):
	"""Unpublished lessons (is_published=0) are excluded from the export."""
	cleanup_hierarchy_rows(db_conn, CH_PREFIX)
	_cleanup_unpublished_lesson(db_conn, CH_PREFIX)
	try:
		hi = hierarchy_rows(db_conn, CH_PREFIX)
		unpub_id = _insert_unpublished_lesson(db_conn, CH_PREFIX)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_content_hierarchy"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_content_hierarchy.parquet"))
		lesson_ids = table.column("lesson_id").to_pylist()

		assert hi["lesson_id"] in lesson_ids, "Published lesson should be in output"
		assert unpub_id not in lesson_ids, "Unpublished lesson should NOT be in output"
	finally:
		_cleanup_unpublished_lesson(db_conn, CH_PREFIX)
		cleanup_hierarchy_rows(db_conn, CH_PREFIX)
