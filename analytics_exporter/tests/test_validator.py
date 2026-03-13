"""Unit tests for analytics_exporter.validator.

TDD: these tests were written before validator.py was implemented.
Run without DB — pure unit tests using tempfiles.
"""

import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from analytics_exporter.validator import validate_export

pytestmark = pytest.mark.unit


def _write_parquet(path: str, schema: pa.Schema, data: dict) -> None:
	"""Write a PyArrow table to a Parquet file."""
	table = pa.table(data, schema=schema)
	pq.write_table(table, path)


class TestUniqueKeyRule:
	def test_catches_duplicate_composite_key(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([
				pa.field("player_id", pa.string()),
				pa.field("item_id", pa.string()),
			])
			_write_parquet(path, schema, {"player_id": ["P1", "P1"], "item_id": ["I1", "I1"]})
			violations = validate_export(path, [
				{"id": "DQ-01", "type": "unique_key", "columns": ["player_id", "item_id"]}
			])
			assert len(violations) > 0
			assert "DQ-01" in violations[0]

	def test_passes_on_unique_composite_key(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([
				pa.field("player_id", pa.string()),
				pa.field("item_id", pa.string()),
			])
			_write_parquet(path, schema, {"player_id": ["P1", "P2"], "item_id": ["I1", "I1"]})
			violations = validate_export(path, [
				{"id": "DQ-01", "type": "unique_key", "columns": ["player_id", "item_id"]}
			])
			assert violations == []

	def test_catches_duplicate_single_column_key(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("id", pa.string())])
			_write_parquet(path, schema, {"id": ["A", "A", "B"]})
			violations = validate_export(path, [
				{"id": "DQ-02", "type": "unique_key", "columns": ["id"]}
			])
			assert len(violations) > 0

	def test_passes_on_unique_single_column_key(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("id", pa.string())])
			_write_parquet(path, schema, {"id": ["A", "B", "C"]})
			violations = validate_export(path, [
				{"id": "DQ-02", "type": "unique_key", "columns": ["id"]}
			])
			assert violations == []


class TestNotNullRule:
	def test_catches_null_in_column(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("player_id", pa.string())])
			_write_parquet(path, schema, {"player_id": ["P1", None]})
			violations = validate_export(path, [
				{"id": "DQ-03", "type": "not_null", "column": "player_id"}
			])
			assert len(violations) > 0
			assert "DQ-03" in violations[0]

	def test_passes_on_non_null_data(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("player_id", pa.string())])
			_write_parquet(path, schema, {"player_id": ["P1", "P2"]})
			violations = validate_export(path, [
				{"id": "DQ-03", "type": "not_null", "column": "player_id"}
			])
			assert violations == []

	def test_catches_all_nulls(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("val", pa.string())])
			_write_parquet(path, schema, {"val": [None, None]})
			violations = validate_export(path, [
				{"id": "DQ-04", "type": "not_null", "column": "val"}
			])
			assert len(violations) > 0


class TestMinValueRule:
	def test_catches_negative_value(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("attempt_count", pa.int64())])
			_write_parquet(path, schema, {"attempt_count": [5, -1, 3]})
			violations = validate_export(path, [
				{"id": "DQ-05", "type": "min_value", "column": "attempt_count", "min": 0}
			])
			assert len(violations) > 0
			assert "DQ-05" in violations[0]

	def test_passes_on_all_values_at_or_above_min(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("attempt_count", pa.int64())])
			_write_parquet(path, schema, {"attempt_count": [0, 1, 5]})
			violations = validate_export(path, [
				{"id": "DQ-05", "type": "min_value", "column": "attempt_count", "min": 0}
			])
			assert violations == []

	def test_passes_on_zero_equal_to_min(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("cnt", pa.int64())])
			_write_parquet(path, schema, {"cnt": [0, 0, 0]})
			violations = validate_export(path, [
				{"id": "DQ-06", "type": "min_value", "column": "cnt", "min": 0}
			])
			assert violations == []


class TestMinRowsRule:
	def test_catches_empty_table(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("id", pa.string())])
			_write_parquet(path, schema, {"id": []})
			violations = validate_export(path, [
				{"id": "DQ-07", "type": "min_rows", "min": 1}
			])
			assert len(violations) > 0
			assert "DQ-07" in violations[0]

	def test_passes_with_sufficient_rows(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("id", pa.string())])
			_write_parquet(path, schema, {"id": ["a", "b"]})
			violations = validate_export(path, [
				{"id": "DQ-07", "type": "min_rows", "min": 1}
			])
			assert violations == []

	def test_catches_below_min_rows(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("id", pa.string())])
			_write_parquet(path, schema, {"id": ["a"]})
			violations = validate_export(path, [
				{"id": "DQ-08", "type": "min_rows", "min": 5}
			])
			assert len(violations) > 0


class TestAllRulesPassOnCleanData:
	def test_all_rules_pass_on_clean_data(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([
				pa.field("player_id", pa.string()),
				pa.field("item_id", pa.string()),
				pa.field("attempt_count", pa.int64()),
			])
			_write_parquet(path, schema, {
				"player_id": ["P1", "P2", "P3"],
				"item_id": ["I1", "I2", "I3"],
				"attempt_count": [1, 2, 3],
			})
			rules = [
				{"id": "DQ-01", "type": "unique_key", "columns": ["player_id", "item_id"]},
				{"id": "DQ-02", "type": "not_null", "column": "player_id"},
				{"id": "DQ-03", "type": "not_null", "column": "item_id"},
				{"id": "DQ-04", "type": "min_value", "column": "attempt_count", "min": 0},
				{"id": "DQ-05", "type": "min_rows", "min": 1},
			]
			violations = validate_export(path, rules)
			assert violations == []

	def test_empty_rules_list_returns_no_violations(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([pa.field("id", pa.string())])
			_write_parquet(path, schema, {"id": ["a"]})
			violations = validate_export(path, [])
			assert violations == []

	def test_multiple_violations_all_reported(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "test.parquet")
			schema = pa.schema([
				pa.field("id", pa.string()),
				pa.field("val", pa.int64()),
			])
			_write_parquet(path, schema, {"id": [None, None], "val": [-1, -2]})
			rules = [
				{"id": "DQ-01", "type": "not_null", "column": "id"},
				{"id": "DQ-02", "type": "min_value", "column": "val", "min": 0},
			]
			violations = validate_export(path, rules)
			assert len(violations) == 2
