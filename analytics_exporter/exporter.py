"""Parquet export utilities for analytics datasets."""

import decimal
import os
from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from .config import Config
from .db import streaming_cursor


def _sql_type_to_arrow(sql_type: str) -> pa.DataType:
	"""Map a SQL type string to a PyArrow type.

	Handles: INT/TINYINT/BIGINT → int64; FLOAT/DOUBLE/DECIMAL → float64;
	DATETIME/TIMESTAMP → timestamp[us]; DATE → date32; all others → string.
	"""
	upper = sql_type.upper()
	if "INT" in upper:
		return pa.int64()
	if "FLOAT" in upper or "DOUBLE" in upper or "DECIMAL" in upper:
		return pa.float64()
	if "DATETIME" in upper or "TIMESTAMP" in upper:
		return pa.timestamp("us")
	if "DATE" in upper:
		return pa.date32()
	# VARCHAR, TEXT, ENUM, CHAR, etc. → string
	return pa.string()


def _coerce_value(val, target_type: pa.DataType | None = None):
	"""Coerce Python values to types PyArrow handles cleanly."""
	if val is None:
		return None
	if isinstance(val, decimal.Decimal):
		return float(val)
	if isinstance(val, date) and not isinstance(val, datetime):
		if target_type is not None and pa.types.is_timestamp(target_type):
			return datetime(val.year, val.month, val.day)
	# SSDictCursor sometimes returns numeric columns as strings
	if isinstance(val, str) and target_type is not None:
		if pa.types.is_integer(target_type):
			return int(val)
		if pa.types.is_floating(target_type):
			return float(val)
		if pa.types.is_timestamp(target_type):
			return datetime.fromisoformat(val)
	return val


def _build_arrow_schema(columns: list[dict]) -> pa.Schema:
	"""Build a PyArrow schema from a list of {name, type} column dicts."""
	return pa.schema([
		pa.field(col["name"], _sql_type_to_arrow(col["type"]))
		for col in columns
	])


def _rows_to_batch(rows: list[dict], columns: list[str], schema: pa.Schema) -> pa.RecordBatch:
	"""Convert a list of row dicts to a PyArrow RecordBatch."""
	col_data = {
		col: [_coerce_value(row.get(col), schema.field(col).type) for row in rows]
		for col in columns
	}
	return pa.RecordBatch.from_pydict(col_data, schema=schema)


def write_parquet(table: pa.Table, path: str) -> None:
	"""Write a PyArrow Table to a Parquet file."""
	pq.write_table(table, path)


def export_snapshot(
	config: Config,
	sql: str,
	params: tuple,
	columns: list[str],
	schema_def: list[dict],
	output_path: str,
) -> tuple[str, int]:
	"""Export a full snapshot query result to a Parquet file.

	Args:
		config: Exporter config (DB connection, chunk size).
		sql: SQL SELECT query to execute.
		params: Query parameters tuple.
		columns: Ordered list of column names to read from each row.
		schema_def: List of {name, type} column dicts for PyArrow schema construction.
		output_path: Absolute path to write the output Parquet file.

	Returns:
		Tuple of (output_path, row_count).
	"""
	arrow_schema = _build_arrow_schema(schema_def)
	row_count = 0
	writer = None

	try:
		with streaming_cursor(config) as cursor:
			cursor.execute(sql, params)

			while True:
				rows = cursor.fetchmany(config.analytics_chunk_size)
				if not rows:
					break

				batch = _rows_to_batch(rows, columns, arrow_schema)

				if writer is None:
					writer = pq.ParquetWriter(output_path, arrow_schema)

				writer.write_batch(batch)
				row_count += len(rows)

	finally:
		if writer is not None:
			writer.close()

	# Zero-row export: write an empty Parquet file with correct schema
	if writer is None:
		writer = pq.ParquetWriter(output_path, arrow_schema)
		writer.close()

	return output_path, row_count


def export_incremental(
	config: Config,
	existing_path: str,
	delta_sql: str,
	params: tuple,
	columns: list[str],
	schema_def: list[dict],
	pk_columns: list[str],
) -> tuple[str, int]:
	"""Export incremental delta rows and merge with existing Parquet snapshot.

	Implements the read-merge-write upsert strategy from R-001:
	1. Load existing Parquet as T_existing.
	2. Run delta query → T_delta.
	3. Concat T_existing + T_delta; deduplicate by pk_columns keeping LAST (delta wins).
	4. Write merged table back to existing_path.

	Args:
		config: Exporter config.
		existing_path: Path to the existing full-snapshot Parquet file.
		delta_sql: SQL query selecting only rows changed since last export.
		params: Query parameters (e.g., the watermark value).
		columns: Ordered list of column names.
		schema_def: List of {name, type} column dicts.
		pk_columns: Primary key columns for deduplication.

	Returns:
		Tuple of (existing_path, total_row_count_after_merge).
	"""
	arrow_schema = _build_arrow_schema(schema_def)

	# Load existing snapshot
	t_existing = pq.read_table(existing_path)

	# Collect delta rows via streaming cursor
	delta_rows: list[pa.RecordBatch] = []
	delta_count = 0

	with streaming_cursor(config) as cursor:
		cursor.execute(delta_sql, params)
		while True:
			rows = cursor.fetchmany(config.analytics_chunk_size)
			if not rows:
				break
			batch = _rows_to_batch(rows, columns, arrow_schema)
			delta_rows.append(batch)
			delta_count += len(rows)

	if delta_count == 0:
		# No delta rows — existing snapshot is already current
		return existing_path, t_existing.num_rows

	t_delta = pa.Table.from_batches(delta_rows, schema=arrow_schema)

	# Concat and deduplicate: delta rows win (they appear last after concat)
	combined = pa.concat_tables([t_existing, t_delta])

	# Build tuple keys for deduplication
	pk_arrays = [combined.column(col).to_pylist() for col in pk_columns]
	pk_tuples = list(zip(*pk_arrays))

	# Keep the LAST occurrence of each PK (delta overwrites existing)
	seen: dict = {}
	for i, key in enumerate(pk_tuples):
		seen[key] = i

	keep_indices = sorted(seen.values())
	merged = combined.take(keep_indices)

	pq.write_table(merged, existing_path)
	return existing_path, merged.num_rows
