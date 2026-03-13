"""Integration tests for Content Hierarchy export — US3.

Scenarios:
  HI-FIVE: Five Parquet files produced with correct fields; id/name columns present.
  HI-TREE: Full hierarchy traversal resolves (no orphaned FK refs).
  HI-UNPUB: Unpublished (is_published=0) entities are included in output.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python3 -m pytest analytics_exporter/tests/test_hierarchy.py -v
"""

import dataclasses
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports
from analytics_exporter.tests.conftest import (
	HI_PREFIX,
	LESSON_TABLE,
	cleanup_hierarchy_rows,
	hierarchy_rows,
)


HIERARCHY_DATASETS = ["subjects", "tracks", "units", "topics", "lessons"]


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


def _make_logger(name: str = "test_hierarchy") -> logging.Logger:
	log = logging.getLogger(name)
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


# ---------------------------------------------------------------------------
# HI-FIVE: Five files produced with correct fields
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_five_hierarchy_files_produced_with_correct_fields(analytics_db_config, db_conn, tmp_path):
	"""Export produces 5 Parquet files, each with correct column set."""
	prefix = "HIFIV"
	cleanup_hierarchy_rows(db_conn, prefix)
	try:
		hi = hierarchy_rows(db_conn, prefix)

		cfg = _make_config(analytics_db_config, str(tmp_path), HIERARCHY_DATASETS)
		results = orchestrate_exports(cfg, _make_logger())

		for dataset in HIERARCHY_DATASETS:
			assert dataset in results, f"{dataset} not dispatched by orchestrate_exports"
			result = results[dataset]
			assert result.success, (
				f"{dataset} export failed: {result.error}; violations: {result.violations}"
			)
			assert result.violations == []

		# subjects: id, name
		subjects_table = pq.read_table(os.path.join(str(tmp_path), "subjects.parquet"))
		assert set(subjects_table.schema.names) == {"id", "name"}

		# tracks: id, name, subject_id
		tracks_table = pq.read_table(os.path.join(str(tmp_path), "tracks.parquet"))
		assert set(tracks_table.schema.names) == {"id", "name", "subject_id"}

		# units: id, name, track_id, subject_id
		units_table = pq.read_table(os.path.join(str(tmp_path), "units.parquet"))
		assert set(units_table.schema.names) == {"id", "name", "track_id", "subject_id"}

		# topics: id, name, unit_id, track_id, subject_id
		topics_table = pq.read_table(os.path.join(str(tmp_path), "topics.parquet"))
		assert set(topics_table.schema.names) == {"id", "name", "unit_id", "track_id", "subject_id"}

		# lessons: id, name, topic_id, unit_id, track_id, subject_id
		lessons_table = pq.read_table(os.path.join(str(tmp_path), "lessons.parquet"))
		assert set(lessons_table.schema.names) == {
			"id", "name", "topic_id", "unit_id", "track_id", "subject_id"
		}

		# Our test rows must appear in each table
		assert hi["subject_id"] in subjects_table.column("id").to_pylist()
		assert hi["track_id"]   in tracks_table.column("id").to_pylist()
		assert hi["unit_id"]    in units_table.column("id").to_pylist()
		assert hi["topic_id"]   in topics_table.column("id").to_pylist()
		assert hi["lesson_id"]  in lessons_table.column("id").to_pylist()

	finally:
		cleanup_hierarchy_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# HI-TREE: Full hierarchy traversal resolves (no orphaned FK refs)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_hierarchy_tree_resolves_completely(analytics_db_config, db_conn, tmp_path):
	"""Every FK reference across hierarchy files resolves to a parent row."""
	prefix = "HITRE"
	cleanup_hierarchy_rows(db_conn, prefix)
	try:
		hierarchy_rows(db_conn, prefix)

		cfg = _make_config(analytics_db_config, str(tmp_path), HIERARCHY_DATASETS)
		results = orchestrate_exports(cfg, _make_logger("hi_tree"))

		for dataset in HIERARCHY_DATASETS:
			assert results[dataset].success, (
				f"{dataset} export failed: {results[dataset].error}"
			)

		subjects_table = pq.read_table(os.path.join(str(tmp_path), "subjects.parquet"))
		tracks_table   = pq.read_table(os.path.join(str(tmp_path), "tracks.parquet"))
		units_table    = pq.read_table(os.path.join(str(tmp_path), "units.parquet"))
		topics_table   = pq.read_table(os.path.join(str(tmp_path), "topics.parquet"))
		lessons_table  = pq.read_table(os.path.join(str(tmp_path), "lessons.parquet"))

		subject_ids = set(subjects_table.column("id").to_pylist())
		track_ids   = set(tracks_table.column("id").to_pylist())
		unit_ids    = set(units_table.column("id").to_pylist())
		topic_ids   = set(topics_table.column("id").to_pylist())

		# tracks.subject_id → subjects.id
		for sid in tracks_table.column("subject_id").to_pylist():
			if sid is not None:
				assert sid in subject_ids, f"tracks.subject_id={sid!r} not in subjects"

		# units.track_id → tracks.id
		for tid in units_table.column("track_id").to_pylist():
			if tid is not None:
				assert tid in track_ids, f"units.track_id={tid!r} not in tracks"

		# topics.unit_id → units.id
		for uid in topics_table.column("unit_id").to_pylist():
			if uid is not None:
				assert uid in unit_ids, f"topics.unit_id={uid!r} not in units"

		# lessons.topic_id → topics.id
		for topid in lessons_table.column("topic_id").to_pylist():
			if topid is not None:
				assert topid in topic_ids, f"lessons.topic_id={topid!r} not in topics"

	finally:
		cleanup_hierarchy_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# HI-UNPUB: Unpublished entities are included in output
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_unpublished_lesson_included_in_export(analytics_db_config, db_conn, tmp_path):
	"""Unpublished (is_published=0) lessons must appear in lessons.parquet output."""
	prefix = "HIUPB"
	unpub_lesson_id = f"{HI_PREFIX}-LESSON-{prefix}-UNPUB"
	cleanup_hierarchy_rows(db_conn, prefix)
	try:
		hi = hierarchy_rows(db_conn, prefix)

		# Insert an explicitly unpublished lesson
		with db_conn.cursor() as cursor:
			cursor.execute(
				f"INSERT IGNORE INTO `{LESSON_TABLE}` "
				"(`name`, `lesson_title`, `topic`, `unit`, `track`, `subject`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
				" `is_published`, `is_reviewable`) "
				"VALUES (%s, %s, %s, %s, %s, %s, "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 99, 0, 0)",
				(
					unpub_lesson_id,
					f"Unpublished Lesson {prefix}",
					hi["topic_id"],
					hi["unit_id"],
					hi["track_id"],
					hi["subject_id"],
				),
			)
		db_conn.commit()

		cfg = _make_config(analytics_db_config, str(tmp_path), ["lessons"])
		results = orchestrate_exports(cfg, _make_logger("hi_unpub"))

		assert results["lessons"].success, (
			f"lessons export failed: {results['lessons'].error}"
		)

		lessons_table = pq.read_table(os.path.join(str(tmp_path), "lessons.parquet"))
		lesson_ids = lessons_table.column("id").to_pylist()

		assert unpub_lesson_id in lesson_ids, (
			f"Unpublished lesson {unpub_lesson_id!r} should appear in lessons.parquet"
		)

	finally:
		with db_conn.cursor() as cursor:
			cursor.execute(
				f"DELETE FROM `{LESSON_TABLE}` WHERE `name` = %s",
				(unpub_lesson_id,),
			)
		db_conn.commit()
		cleanup_hierarchy_rows(db_conn, prefix)
