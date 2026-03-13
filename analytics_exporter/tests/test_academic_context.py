"""Integration tests for Academic Context export — US4.

Scenarios:
  AC-FIVE: Five Parquet files produced with correct required fields.
  AC-FKEY: academic_plans FKs (season, grade, major) resolve to rows in their
           respective files.
  AC-UNPB: Unpublished (is_published=0) plans are included in output.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python3 -m pytest analytics_exporter/tests/test_academic_context.py -v
"""

import dataclasses
import logging
import os

import pyarrow.parquet as pq
import pytest

from analytics_exporter.config import Config
from analytics_exporter.run import orchestrate_exports
from analytics_exporter.tests.conftest import (
	AC_PREFIX,
	ACADEMIC_PLAN_TABLE,
	academic_context_rows,
	cleanup_academic_context_rows,
)


ACADEMIC_DATASETS = ["seasons", "grades", "majors", "academic_plans", "grade_majors"]


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


def _make_logger(name: str = "test_academic_context") -> logging.Logger:
	log = logging.getLogger(name)
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


# ---------------------------------------------------------------------------
# AC-FIVE: Five files produced with correct fields
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_five_academic_files_produced_with_correct_fields(analytics_db_config, db_conn, tmp_path):
	"""Export produces 5 Parquet files, each with correct column set."""
	prefix = "ACFIV"
	cleanup_academic_context_rows(db_conn, prefix)
	try:
		ctx = academic_context_rows(db_conn, prefix, season_seq=9970)

		cfg = _make_config(analytics_db_config, str(tmp_path), ACADEMIC_DATASETS)
		results = orchestrate_exports(cfg, _make_logger())

		for dataset in ACADEMIC_DATASETS:
			assert dataset in results, f"{dataset} not dispatched by orchestrate_exports"
			result = results[dataset]
			# Verify the export ran without a fatal error.  DQ violations against
			# pre-existing production data are informational — they don't mean our
			# export code is broken.  Each assertion below validates our test rows.
			assert result.error is None, f"{dataset} export raised an error: {result.error}"
			out = os.path.join(str(tmp_path), f"{dataset}.parquet")
			assert os.path.exists(out), f"{dataset}.parquet not written to disk"

		# seasons: id, name, season_seq, start_date, end_date
		seasons_table = pq.read_table(os.path.join(str(tmp_path), "seasons.parquet"))
		assert set(seasons_table.schema.names) == {"id", "name", "season_seq", "start_date", "end_date"}

		# grades: id, name, sort_order
		grades_table = pq.read_table(os.path.join(str(tmp_path), "grades.parquet"))
		assert set(grades_table.schema.names) == {"id", "name", "sort_order"}

		# majors: id, name
		majors_table = pq.read_table(os.path.join(str(tmp_path), "majors.parquet"))
		assert set(majors_table.schema.names) == {"id", "name"}

		# academic_plans: id, name, season, grade, major, is_published
		plans_table = pq.read_table(os.path.join(str(tmp_path), "academic_plans.parquet"))
		assert set(plans_table.schema.names) == {
			"id", "name", "season", "grade", "major", "is_published"
		}

		# grade_majors: grade, major
		gm_table = pq.read_table(os.path.join(str(tmp_path), "grade_majors.parquet"))
		assert set(gm_table.schema.names) == {"grade", "major"}

		# Our test rows must appear
		assert ctx["season_id"] in seasons_table.column("id").to_pylist()
		assert ctx["grade_id"]  in grades_table.column("id").to_pylist()
		assert ctx["major_id"]  in majors_table.column("id").to_pylist()
		assert ctx["plan_id"]   in plans_table.column("id").to_pylist()

		# grade_majors row must appear
		gm_grades = gm_table.column("grade").to_pylist()
		gm_majors = gm_table.column("major").to_pylist()
		assert ctx["grade_id"] in gm_grades, "test grade_id not found in grade_majors.grade"
		# Verify the (grade, major) pair exists
		pairs = set(zip(gm_grades, gm_majors))
		assert (ctx["grade_id"], ctx["major_id"]) in pairs, (
			f"grade_major pair ({ctx['grade_id']!r}, {ctx['major_id']!r}) not in grade_majors"
		)

	finally:
		cleanup_academic_context_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# AC-FKEY: academic_plans FKs (season, grade, major) resolve to parent rows
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_academic_plans_fks_resolve(analytics_db_config, db_conn, tmp_path):
	"""academic_plans.season/grade/major FKs resolve to rows in their respective files."""
	prefix = "ACFKY"
	cleanup_academic_context_rows(db_conn, prefix)
	try:
		academic_context_rows(db_conn, prefix, season_seq=9971)

		cfg = _make_config(analytics_db_config, str(tmp_path), ACADEMIC_DATASETS)
		results = orchestrate_exports(cfg, _make_logger("ac_fkey"))

		for dataset in ACADEMIC_DATASETS:
			result = results[dataset]
			assert result.error is None, f"{dataset} export raised an error: {result.error}"
			assert os.path.exists(os.path.join(str(tmp_path), f"{dataset}.parquet"))

		seasons_table = pq.read_table(os.path.join(str(tmp_path), "seasons.parquet"))
		grades_table  = pq.read_table(os.path.join(str(tmp_path), "grades.parquet"))
		majors_table  = pq.read_table(os.path.join(str(tmp_path), "majors.parquet"))
		plans_table   = pq.read_table(os.path.join(str(tmp_path), "academic_plans.parquet"))
		gm_table      = pq.read_table(os.path.join(str(tmp_path), "grade_majors.parquet"))

		season_ids = set(seasons_table.column("id").to_pylist())
		grade_ids  = set(grades_table.column("id").to_pylist())
		major_ids  = set(majors_table.column("id").to_pylist())

		# academic_plans.season → seasons.id
		for sid in plans_table.column("season").to_pylist():
			if sid is not None:
				assert sid in season_ids, f"academic_plans.season={sid!r} not in seasons"

		# academic_plans.grade → grades.id
		for gid in plans_table.column("grade").to_pylist():
			if gid is not None:
				assert gid in grade_ids, f"academic_plans.grade={gid!r} not in grades"

		# academic_plans.major → majors.id
		for mid in plans_table.column("major").to_pylist():
			if mid is not None:
				assert mid in major_ids, f"academic_plans.major={mid!r} not in majors"

		# grade_majors.grade → grades.id
		for gid in gm_table.column("grade").to_pylist():
			if gid is not None:
				assert gid in grade_ids, f"grade_majors.grade={gid!r} not in grades"

		# grade_majors.major → majors.id
		for mid in gm_table.column("major").to_pylist():
			if mid is not None:
				assert mid in major_ids, f"grade_majors.major={mid!r} not in majors"

	finally:
		cleanup_academic_context_rows(db_conn, prefix)


# ---------------------------------------------------------------------------
# AC-UNPB: Unpublished plans are included in output
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_unpublished_academic_plan_included(analytics_db_config, db_conn, tmp_path):
	"""Unpublished (is_published=0) academic plans must appear in academic_plans.parquet."""
	prefix = "ACUPB"
	unpub_plan_id = f"{AC_PREFIX}-PLAN-{prefix}-UNPUB"
	cleanup_academic_context_rows(db_conn, prefix)
	try:
		ctx = academic_context_rows(db_conn, prefix, season_seq=9972)

		# Insert an explicitly unpublished plan
		with db_conn.cursor() as cursor:
			cursor.execute(
				f"INSERT IGNORE INTO `{ACADEMIC_PLAN_TABLE}` "
				"(`name`, `plan_name`, `season`, `grade`, `major`, `is_published`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
				"VALUES (%s, %s, %s, %s, %s, 0, NOW(), NOW(), "
				"        'test@test.com', 'test@test.com', 0, 99)",
				(
					unpub_plan_id,
					f"Unpublished Plan {prefix}",
					ctx["season_id"],
					ctx["grade_id"],
					ctx["major_id"],
				),
			)
		db_conn.commit()

		cfg = _make_config(analytics_db_config, str(tmp_path), ["academic_plans"])
		results = orchestrate_exports(cfg, _make_logger("ac_unpb"))

		result = results["academic_plans"]
		assert result.error is None, (
			f"academic_plans export raised an error: {result.error}"
		)

		plans_table = pq.read_table(os.path.join(str(tmp_path), "academic_plans.parquet"))
		plan_ids = plans_table.column("id").to_pylist()

		assert unpub_plan_id in plan_ids, (
			f"Unpublished plan {unpub_plan_id!r} should appear in academic_plans.parquet"
		)

		# Verify is_published=0 is preserved
		plan_row_idx = plan_ids.index(unpub_plan_id)
		is_pub = plans_table.column("is_published").to_pylist()[plan_row_idx]
		assert is_pub == 0, f"Expected is_published=0 for unpublished plan, got {is_pub}"

	finally:
		with db_conn.cursor() as cursor:
			cursor.execute(
				f"DELETE FROM `{ACADEMIC_PLAN_TABLE}` WHERE `name` = %s",
				(unpub_plan_id,),
			)
		db_conn.commit()
		cleanup_academic_context_rows(db_conn, prefix)
