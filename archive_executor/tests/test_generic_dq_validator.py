"""Unit tests for the generic DQ validation engine (validate_fact_quality_generic).

All tests use mock Parquet data written to temp files — no DB or network required.
"""

import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from archive_executor.validator import validate_fact_quality_generic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_parquet(table: pa.Table, tmp_dir: str, name: str = "fact.parquet") -> str:
	path = os.path.join(tmp_dir, name)
	pq.write_table(table, path)
	return path


def _fact(columns: dict) -> pa.Table:
	"""Build a small PyArrow Table from column_name → list_of_values."""
	arrays = {k: pa.array(v) for k, v in columns.items()}
	return pa.table(arrays)


def _dim(id_col: str, ids: list, extra: dict | None = None) -> pa.Table:
	"""Build a minimal dimension table."""
	cols = {id_col: pa.array(ids)}
	if extra:
		cols.update({k: pa.array(v) for k, v in extra.items()})
	return pa.table(cols)


# ---------------------------------------------------------------------------
# not_null
# ---------------------------------------------------------------------------


class TestNotNull:
	def test_pass_no_nulls(self, tmp_path):
		fact = _fact({"name": ["A", "B", "C"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-01", "type": "not_null", "column": "name"}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True
		assert result["results"][0]["passed"] is True

	def test_fail_with_nulls(self, tmp_path):
		fact = _fact({"name": ["A", None, "C"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-01", "type": "not_null", "column": "name"}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is False
		assert result["results"][0]["passed"] is False
		assert "null_count=1" in result["results"][0]["detail"]

	def test_fail_missing_column(self, tmp_path):
		fact = _fact({"other": ["A"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-01", "type": "not_null", "column": "name"}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is False
		assert "missing" in result["results"][0]["detail"]


# ---------------------------------------------------------------------------
# enum_values
# ---------------------------------------------------------------------------


class TestEnumValues:
	def test_pass_all_valid(self, tmp_path):
		fact = _fact({"event_type": ["Started", "Completed", "Failed"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-07", "type": "enum_values", "column": "event_type",
				  "values": ["Started", "Completed", "Failed", "Skipped"]}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True

	def test_fail_invalid_value(self, tmp_path):
		fact = _fact({"event_type": ["Started", "Unknown"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-07", "type": "enum_values", "column": "event_type",
				  "values": ["Started", "Completed"]}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is False
		assert "Unknown" in result["results"][0]["detail"]

	def test_pass_nulls_ignored(self, tmp_path):
		fact = _fact({"event_type": ["Started", None]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-07", "type": "enum_values", "column": "event_type",
				  "values": ["Started", "Completed"]}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True


# ---------------------------------------------------------------------------
# min_value
# ---------------------------------------------------------------------------


class TestMinValue:
	def test_pass(self, tmp_path):
		fact = _fact({"time_spent": [0, 5, 100]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-08", "type": "min_value", "column": "time_spent", "min": 0}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True

	def test_fail_below_min(self, tmp_path):
		fact = _fact({"time_spent": [-1, 5]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-08", "type": "min_value", "column": "time_spent", "min": 0}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is False
		assert "min=-1" in result["results"][0]["detail"]

	def test_pass_all_nulls_skipped(self, tmp_path):
		fact = _fact({"time_spent": pa.array([None, None], type=pa.int64())})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-08", "type": "min_value", "column": "time_spent", "min": 0}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True
		assert "skipped" in result["results"][0]["detail"]


# ---------------------------------------------------------------------------
# max_value
# ---------------------------------------------------------------------------


class TestMaxValue:
	def test_pass(self, tmp_path):
		fact = _fact({"score": [0, 50, 100]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-XX", "type": "max_value", "column": "score", "max": 100}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True

	def test_fail_above_max(self, tmp_path):
		fact = _fact({"score": [50, 150]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-XX", "type": "max_value", "column": "score", "max": 100}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is False
		assert "max=150" in result["results"][0]["detail"]


# ---------------------------------------------------------------------------
# scope_range
# ---------------------------------------------------------------------------


class TestScopeRange:
	def test_pass_within_range(self, tmp_path):
		fact = _fact({"timestamp": ["2099-01-15 12:00:00", "2099-01-20 00:00:00"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-10", "type": "scope_range", "column": "timestamp"}]
		result = validate_fact_quality_generic(
			path, rules,
			scope_date_from="2099-01-01",
			scope_date_to="2099-02-01",
		)
		assert result["passed"] is True

	def test_fail_out_of_range(self, tmp_path):
		fact = _fact({"timestamp": ["2099-01-15 12:00:00", "2099-03-01 00:00:00"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-10", "type": "scope_range", "column": "timestamp"}]
		result = validate_fact_quality_generic(
			path, rules,
			scope_date_from="2099-01-01",
			scope_date_to="2099-02-01",
		)
		assert result["passed"] is False
		assert "1 rows outside scope" in result["results"][0]["detail"]

	def test_skipped_when_no_scope_dates(self, tmp_path):
		fact = _fact({"timestamp": ["2099-01-15"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-10", "type": "scope_range", "column": "timestamp"}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True
		assert "Skipped" in result["results"][0]["detail"]


# ---------------------------------------------------------------------------
# referential
# ---------------------------------------------------------------------------


class TestReferential:
	def test_pass_all_ids_in_dim(self, tmp_path):
		fact = _fact({"player": ["P1", "P2"]})
		dim = _dim("name", ["P1", "P2", "P3"])
		fact_path = _write_parquet(fact, str(tmp_path), "fact.parquet")
		dim_path = _write_parquet(dim, str(tmp_path), "player.parquet")
		rules = [{"id": "DQ-11", "type": "referential", "column": "player", "dimension": "player"}]
		result = validate_fact_quality_generic(fact_path, rules, dimension_paths={"player": dim_path})
		assert result["passed"] is True

	def test_fail_orphan_ids(self, tmp_path):
		fact = _fact({"player": ["P1", "P99"]})
		dim = _dim("name", ["P1", "P2"])
		fact_path = _write_parquet(fact, str(tmp_path), "fact.parquet")
		dim_path = _write_parquet(dim, str(tmp_path), "player.parquet")
		rules = [{"id": "DQ-11", "type": "referential", "column": "player", "dimension": "player"}]
		result = validate_fact_quality_generic(fact_path, rules, dimension_paths={"player": dim_path})
		assert result["passed"] is False
		assert "1 player" in result["results"][0]["detail"]

	def test_skipped_when_no_dim_path(self, tmp_path):
		fact = _fact({"player": ["P1"]})
		fact_path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-11", "type": "referential", "column": "player", "dimension": "player"}]
		result = validate_fact_quality_generic(fact_path, rules)
		assert result["passed"] is True
		assert "Skipped" in result["results"][0]["detail"]

	def test_dim_id_column_detection_entity_id(self, tmp_path):
		"""Dimension with {entity}_id column is detected correctly."""
		fact = _fact({"lesson": ["L1", "L2"]})
		dim = _dim("lesson_id", ["L1", "L2", "L3"])
		fact_path = _write_parquet(fact, str(tmp_path), "fact.parquet")
		dim_path = _write_parquet(dim, str(tmp_path), "lesson.parquet")
		rules = [{"id": "DQ-12", "type": "referential", "column": "lesson", "dimension": "lesson"}]
		result = validate_fact_quality_generic(fact_path, rules, dimension_paths={"lesson": dim_path})
		assert result["passed"] is True


# ---------------------------------------------------------------------------
# unique_key
# ---------------------------------------------------------------------------


class TestUniqueKey:
	def test_pass_no_duplicates(self, tmp_path):
		fact = _fact({"name": ["A", "B", "C"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-13", "type": "unique_key", "columns": ["name"]}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True

	def test_fail_with_duplicates(self, tmp_path):
		fact = _fact({"name": ["A", "A", "C"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-13", "type": "unique_key", "columns": ["name"]}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is False
		assert "duplicate" in result["results"][0]["detail"]

	def test_fail_missing_columns(self, tmp_path):
		fact = _fact({"other": ["A"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-13", "type": "unique_key", "columns": ["name"]}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is False
		assert "missing" in result["results"][0]["detail"]


# ---------------------------------------------------------------------------
# Empty fact table
# ---------------------------------------------------------------------------


class TestEmptyFact:
	def test_empty_returns_passed_with_warning(self, tmp_path):
		fact = pa.table({"name": pa.array([], type=pa.string())})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-01", "type": "not_null", "column": "name"}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True
		assert result["results"] == []
		assert any("0 rows" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Unknown rule type
# ---------------------------------------------------------------------------


class TestUnknownRuleType:
	def test_unknown_rule_skipped_with_warning(self, tmp_path):
		fact = _fact({"name": ["A"]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [{"id": "DQ-99", "type": "future_rule", "column": "name"}]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True
		assert any("future_rule" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Multiple rules — partial failure
# ---------------------------------------------------------------------------


class TestMultipleRules:
	def test_all_pass(self, tmp_path):
		fact = _fact({"name": ["A", "B"], "val": [1, 2]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [
			{"id": "DQ-01", "type": "not_null", "column": "name"},
			{"id": "DQ-02", "type": "min_value", "column": "val", "min": 0},
		]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is True
		assert len(result["results"]) == 2

	def test_one_fail_overall_fail(self, tmp_path):
		fact = _fact({"name": ["A", None], "val": [1, 2]})
		path = _write_parquet(fact, str(tmp_path))
		rules = [
			{"id": "DQ-01", "type": "not_null", "column": "name"},
			{"id": "DQ-02", "type": "min_value", "column": "val", "min": 0},
		]
		result = validate_fact_quality_generic(path, rules)
		assert result["passed"] is False
		passed_ids = [r["rule"] for r in result["results"] if r["passed"]]
		failed_ids = [r["rule"] for r in result["results"] if not r["passed"]]
		assert "DQ-02" in passed_ids
		assert "DQ-01" in failed_ids
