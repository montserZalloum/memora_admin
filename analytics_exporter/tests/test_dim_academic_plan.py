"""Integration tests for dim_academic_plan export.

Scenarios:
  AP-FULL:    Full export -> 11-column Parquet with correct columns.
  AP-DEORM:   Denormalized grade_title and major_title are present.
  AP-SUBJ:    subject_list from GROUP_CONCAT subquery is populated.
  AP-NONULL:  No null plan_id values in output.

Run:
    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
        python3 -m pytest analytics_exporter/tests/test_dim_academic_plan.py -v
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

AP_PREFIX = "DIMAP"

EXPECTED_COLUMNS = {
	"plan_id", "plan_name", "grade_id", "grade_title",
	"major_id", "major_title", "season_id", "is_published",
	"total_subjects", "total_lessons", "subject_list",
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
		analytics_datasets=["dim_academic_plan"],
	)


def _make_logger() -> logging.Logger:
	log = logging.getLogger("test_dim_academic_plan")
	if not log.handlers:
		h = logging.StreamHandler()
		h.setLevel(logging.DEBUG)
		log.addHandler(h)
	log.setLevel(logging.DEBUG)
	return log


def _insert_plan_subjects(conn, plan_id: str, subjects: list[str]) -> None:
	"""Insert Plan Subject child rows."""
	for idx, subj in enumerate(subjects, 1):
		ps_name = f"{plan_id}-PS-{idx:03d}"
		with conn.cursor() as cur:
			cur.execute(
				"INSERT IGNORE INTO `tabMemora Plan Subject` "
				"(`name`, `subject`, `parent`, `parentfield`, `parenttype`, `idx`, "
				" `creation`, `modified`, `modified_by`, `owner`, `docstatus`) "
				"VALUES (%s, %s, %s, 'subjects', 'Memora Academic Plan', %s, "
				"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0)",
				(ps_name, subj, plan_id, idx),
			)
	conn.commit()


def _cleanup_plan_subjects(conn, plan_id: str) -> None:
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM `tabMemora Plan Subject` WHERE `parent` = %s",
			(plan_id,),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# AP-FULL: Full export with 11 columns
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_academic_plan_full_export(analytics_db_config, db_conn, tmp_path):
	"""dim_academic_plan export produces 11-column Parquet with correct columns."""
	cleanup_academic_context_rows(db_conn, AP_PREFIX)
	try:
		ac = academic_context_rows(db_conn, AP_PREFIX, season_seq=9970)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())

		assert "dim_academic_plan" in results
		result = results["dim_academic_plan"]
		assert result.success, f"Export failed: {result.error}; violations: {result.violations}"

		out_path = os.path.join(str(tmp_path), "dim_academic_plan.parquet")
		table = pq.read_table(out_path)
		assert set(table.schema.names) == EXPECTED_COLUMNS

		# Our test plan is present
		plan_ids = table.column("plan_id").to_pylist()
		assert ac["plan_id"] in plan_ids

		# Manifest written
		manifest_path = os.path.join(str(tmp_path), "dim_academic_plan.manifest.json")
		assert os.path.exists(manifest_path)
	finally:
		cleanup_academic_context_rows(db_conn, AP_PREFIX)


# ---------------------------------------------------------------------------
# AP-DEORM: Denormalized grade_title and major_title
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_academic_plan_denormalized_titles(analytics_db_config, db_conn, tmp_path):
	"""Denormalized grade_title and major_title are present and correct."""
	cleanup_academic_context_rows(db_conn, AP_PREFIX)
	try:
		ac = academic_context_rows(db_conn, AP_PREFIX, season_seq=9971)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_academic_plan"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_academic_plan.parquet"))
		plan_ids = table.column("plan_id").to_pylist()
		idx = plan_ids.index(ac["plan_id"])

		assert table.column("grade_title")[idx].as_py() == f"Test Grade {AP_PREFIX}"
		assert table.column("major_title")[idx].as_py() == f"Test Major {AP_PREFIX}"
	finally:
		cleanup_academic_context_rows(db_conn, AP_PREFIX)


# ---------------------------------------------------------------------------
# AP-SUBJ: subject_list from GROUP_CONCAT
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_academic_plan_subject_list(analytics_db_config, db_conn, tmp_path):
	"""subject_list column populated from GROUP_CONCAT of Plan Subject children."""
	cleanup_academic_context_rows(db_conn, AP_PREFIX)
	try:
		ac = academic_context_rows(db_conn, AP_PREFIX, season_seq=9972)
		plan_id = ac["plan_id"]

		_insert_plan_subjects(db_conn, plan_id, [
			f"SUBJ-{AP_PREFIX}-MATH",
			f"SUBJ-{AP_PREFIX}-SCI",
		])

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_academic_plan"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_academic_plan.parquet"))
		plan_ids = table.column("plan_id").to_pylist()
		idx = plan_ids.index(plan_id)

		subject_list = table.column("subject_list")[idx].as_py()
		assert subject_list is not None, "subject_list is null"
		assert f"SUBJ-{AP_PREFIX}-MATH" in subject_list
		assert f"SUBJ-{AP_PREFIX}-SCI" in subject_list
	finally:
		_cleanup_plan_subjects(db_conn, ac["plan_id"])
		cleanup_academic_context_rows(db_conn, AP_PREFIX)


# ---------------------------------------------------------------------------
# AP-NONULL: No null plan_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dim_academic_plan_no_null_plan_id(analytics_db_config, db_conn, tmp_path):
	"""No null plan_id values in output."""
	cleanup_academic_context_rows(db_conn, AP_PREFIX)
	try:
		academic_context_rows(db_conn, AP_PREFIX, season_seq=9973)

		cfg = _make_config(analytics_db_config, str(tmp_path))
		results = orchestrate_exports(cfg, _make_logger())
		assert results["dim_academic_plan"].success

		table = pq.read_table(os.path.join(str(tmp_path), "dim_academic_plan.parquet"))
		plan_ids = table.column("plan_id").to_pylist()
		assert all(pid is not None for pid in plan_ids), "Found null plan_id"
	finally:
		cleanup_academic_context_rows(db_conn, AP_PREFIX)
