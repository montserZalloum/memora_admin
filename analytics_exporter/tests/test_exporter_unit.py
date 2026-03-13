"""Unit tests for analytics_exporter.exporter utilities.

TDD: these tests were written before exporter.py was implemented.
Run without DB — pure unit tests.
"""

import datetime
import decimal

import pyarrow as pa
import pytest

from analytics_exporter.exporter import (
	_coerce_value,
	_rows_to_batch,
	_sql_type_to_arrow,
)

pytestmark = pytest.mark.unit


class TestSqlTypeToArrow:
	def test_int(self):
		assert _sql_type_to_arrow("INT") == pa.int64()

	def test_int_unsigned(self):
		assert _sql_type_to_arrow("INT UNSIGNED") == pa.int64()

	def test_tinyint(self):
		assert _sql_type_to_arrow("TINYINT") == pa.int64()

	def test_tinyint_1(self):
		assert _sql_type_to_arrow("TINYINT(1)") == pa.int64()

	def test_bigint(self):
		assert _sql_type_to_arrow("BIGINT") == pa.int64()

	def test_float(self):
		assert _sql_type_to_arrow("FLOAT") == pa.float64()

	def test_double(self):
		assert _sql_type_to_arrow("DOUBLE") == pa.float64()

	def test_decimal(self):
		assert _sql_type_to_arrow("DECIMAL(21,9)") == pa.float64()

	def test_datetime(self):
		assert _sql_type_to_arrow("DATETIME") == pa.timestamp("us")

	def test_timestamp(self):
		assert _sql_type_to_arrow("TIMESTAMP") == pa.timestamp("us")

	def test_date(self):
		assert _sql_type_to_arrow("DATE") == pa.date32()

	def test_varchar(self):
		assert _sql_type_to_arrow("VARCHAR(255)") == pa.string()

	def test_enum(self):
		assert _sql_type_to_arrow("ENUM('Correct','Incorrect')") == pa.string()

	def test_text(self):
		assert _sql_type_to_arrow("TEXT") == pa.string()

	def test_case_insensitive(self):
		assert _sql_type_to_arrow("int") == pa.int64()
		assert _sql_type_to_arrow("datetime") == pa.timestamp("us")
		assert _sql_type_to_arrow("date") == pa.date32()


class TestCoerceValue:
	def test_none_returns_none(self):
		assert _coerce_value(None) is None

	def test_none_with_target_type_returns_none(self):
		assert _coerce_value(None, pa.int64()) is None

	def test_decimal_to_float(self):
		result = _coerce_value(decimal.Decimal("3.14"))
		assert isinstance(result, float)
		assert abs(result - 3.14) < 1e-6

	def test_decimal_to_float_with_target_type(self):
		result = _coerce_value(decimal.Decimal("99.999"), pa.float64())
		assert isinstance(result, float)

	def test_date_passthrough_without_target(self):
		d = datetime.date(2026, 1, 15)
		result = _coerce_value(d)
		assert result == d

	def test_date_coerced_to_datetime_when_timestamp_target(self):
		d = datetime.date(2026, 3, 13)
		result = _coerce_value(d, pa.timestamp("us"))
		assert isinstance(result, datetime.datetime)
		assert result.year == 2026
		assert result.month == 3
		assert result.day == 13

	def test_datetime_passthrough(self):
		dt = datetime.datetime(2026, 3, 13, 12, 0, 0)
		result = _coerce_value(dt)
		assert result == dt

	def test_string_passthrough(self):
		assert _coerce_value("hello") == "hello"

	def test_int_passthrough(self):
		assert _coerce_value(42) == 42

	def test_string_coerced_to_int_with_target_type(self):
		result = _coerce_value("42", pa.int64())
		assert result == 42
		assert isinstance(result, int)

	def test_string_coerced_to_float_with_target_type(self):
		result = _coerce_value("3.14", pa.float64())
		assert abs(result - 3.14) < 1e-6

	def test_string_coerced_to_datetime_with_timestamp_target(self):
		result = _coerce_value("2026-03-13T12:00:00", pa.timestamp("us"))
		assert isinstance(result, datetime.datetime)
		assert result.year == 2026


class TestRowsToBatch:
	def _make_schema(self) -> pa.Schema:
		return pa.schema([
			pa.field("player_id", pa.string()),
			pa.field("attempt_count", pa.int64()),
			pa.field("score", pa.float64()),
		])

	def test_basic_conversion(self):
		schema = self._make_schema()
		rows = [
			{"player_id": "P001", "attempt_count": 5, "score": 0.8},
			{"player_id": "P002", "attempt_count": 3, "score": 0.6},
		]
		batch = _rows_to_batch(rows, ["player_id", "attempt_count", "score"], schema)
		assert batch.num_rows == 2
		assert batch.schema == schema

	def test_empty_rows_returns_empty_batch(self):
		schema = self._make_schema()
		batch = _rows_to_batch([], ["player_id", "attempt_count", "score"], schema)
		assert batch.num_rows == 0

	def test_mixed_types_with_decimal(self):
		schema = pa.schema([
			pa.field("id", pa.string()),
			pa.field("value", pa.float64()),
		])
		rows = [{"id": "x", "value": decimal.Decimal("1.5")}]
		batch = _rows_to_batch(rows, ["id", "value"], schema)
		assert batch.num_rows == 1
		assert batch.column("value")[0].as_py() == pytest.approx(1.5)

	def test_none_values(self):
		schema = pa.schema([
			pa.field("id", pa.string()),
			pa.field("val", pa.int64()),
		])
		rows = [{"id": None, "val": None}]
		batch = _rows_to_batch(rows, ["id", "val"], schema)
		assert batch.num_rows == 1
		assert batch.column("id")[0].as_py() is None
		assert batch.column("val")[0].as_py() is None

	def test_column_order_matches_schema(self):
		schema = pa.schema([
			pa.field("a", pa.string()),
			pa.field("b", pa.int64()),
			pa.field("c", pa.float64()),
		])
		rows = [{"a": "hello", "b": 1, "c": 2.5}]
		batch = _rows_to_batch(rows, ["a", "b", "c"], schema)
		assert batch.column("a")[0].as_py() == "hello"
		assert batch.column("b")[0].as_py() == 1
		assert batch.column("c")[0].as_py() == pytest.approx(2.5)
