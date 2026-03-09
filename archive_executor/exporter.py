"""Parquet export for fact data and dimension snapshots."""

import os
from collections import defaultdict
from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from .config import Config
from .db import streaming_cursor


def _sql_type_to_arrow(sql_type: str) -> pa.DataType:
	"""Map a SQL type string from schema_snapshot to a pyarrow type."""
	upper = sql_type.upper()
	if "INT" in upper:
		return pa.int64()
	if "FLOAT" in upper or "DOUBLE" in upper or "DECIMAL" in upper:
		return pa.float64()
	if "DATETIME" in upper or "TIMESTAMP" in upper:
		return pa.timestamp("us")
	if "DATE" in upper:
		return pa.date32()
	# VARCHAR, TEXT, ENUM, etc. → string
	return pa.string()


def _build_arrow_schema(schema_snapshot: dict) -> pa.Schema:
	"""Build a pyarrow Schema from the schema_snapshot embedded in job meta."""
	fields = []
	for col in schema_snapshot.get("columns", []):
		fields.append(pa.field(col["name"], _sql_type_to_arrow(col["type"])))
	return pa.schema(fields)


def _coerce_value(val):
	"""Coerce Python values to types pyarrow handles cleanly."""
	if isinstance(val, date) and not isinstance(val, datetime):
		return datetime(val.year, val.month, val.day)
	return val


def _rows_to_batch(rows: list[dict], columns: list[str], schema: pa.Schema) -> pa.RecordBatch:
	"""Convert a list of row dicts to a pyarrow RecordBatch."""
	col_data = {col: [_coerce_value(row.get(col)) for row in rows] for col in columns}
	return pa.RecordBatch.from_pydict(col_data, schema=schema)


def export_fact_data(
	config: Config,
	staging_dir: str,
	meta: dict,
	source_table: str,
	archive_type_name: str,
) -> tuple[str, int, dict[str, set]]:
	"""Export fact data to a Parquet file using server-side streaming.

	Args:
		config: Executor configuration.
		staging_dir: Path to the staging directory for this job.
		meta: Job meta JSON with query_filter, export_columns, schema_snapshot.
		source_table: MariaDB table name (e.g., 'tabMemora Practice Log').
		archive_type_name: Archive type key for the output filename.

	Returns:
		Tuple of (file_path, row_count, referenced_ids) where referenced_ids
		maps fact_column name → set of unique IDs seen in the exported data.
	"""
	query_filter = meta["query_filter"]
	export_columns = meta["export_columns"]
	schema_snapshot = meta.get("schema_snapshot", {})
	related_tables = meta.get("related_tables", [])

	# Determine which fact columns to track for dimension scoping
	dimension_fact_columns = [rt["fact_column"] for rt in related_tables]

	# Build SQL query
	columns_sql = ", ".join(f"`{col}`" for col in export_columns)
	filter_col = query_filter["filter_column"]
	sql = f"SELECT {columns_sql} FROM `{source_table}` WHERE `{filter_col}` >= %s AND `{filter_col}` < %s"
	params = (query_filter["date_from"], query_filter["date_to"])

	# Build pyarrow schema from snapshot
	arrow_schema = _build_arrow_schema(schema_snapshot) if schema_snapshot.get("columns") else None

	output_path = os.path.join(staging_dir, f"fact_{archive_type_name}.parquet")
	row_count = 0
	referenced_ids: dict[str, set] = defaultdict(set)
	writer = None

	try:
		with streaming_cursor(config) as cursor:
			cursor.execute(sql, params)

			while True:
				rows = cursor.fetchmany(config.chunk_size)
				if not rows:
					break

				# Collect referenced IDs for dimension scoping
				for fact_col in dimension_fact_columns:
					referenced_ids[fact_col].update(row[fact_col] for row in rows if row.get(fact_col))

				# Build batch
				if arrow_schema:
					batch = _rows_to_batch(rows, export_columns, arrow_schema)
				else:
					# Infer schema from first batch
					col_data = {col: [_coerce_value(row.get(col)) for row in rows] for col in export_columns}
					batch = pa.RecordBatch.from_pydict(col_data)
					arrow_schema = batch.schema

				if writer is None:
					writer = pq.ParquetWriter(output_path, arrow_schema)

				writer.write_batch(batch)
				row_count += len(rows)

	finally:
		if writer is not None:
			writer.close()

	# If no rows were exported, create an empty Parquet file with the schema
	if writer is None and arrow_schema:
		writer = pq.ParquetWriter(output_path, arrow_schema)
		writer.close()

	return output_path, row_count, dict(referenced_ids)


def export_dimension(
	config: Config,
	staging_dir: str,
	dim_schema: dict,
	referenced_ids: set,
) -> tuple[str, int]:
	"""Export a dimension snapshot scoped to referenced IDs.

	Args:
		config: Executor configuration.
		staging_dir: Path to the staging directory for this job.
		dim_schema: Dimension schema dict from YAML (entity, source_table, id_column, fields).
		referenced_ids: Set of IDs to include from the dimension table.

	Returns:
		Tuple of (file_path, row_count).
	"""
	entity = dim_schema["entity"]
	source_table = dim_schema["source_table"]
	id_column = dim_schema["id_column"]
	fields = dim_schema["fields"]

	output_path = os.path.join(staging_dir, f"dim_{entity}.parquet")

	if not referenced_ids:
		# No referenced IDs — write empty Parquet with correct columns
		empty_data = {f: [] for f in fields}
		table = pa.table(empty_data)
		pq.write_table(table, output_path)
		return output_path, 0

	columns_sql = ", ".join(f"`{f}`" for f in fields)
	all_rows = []

	# Batch the IN clause to avoid exceeding max_allowed_packet
	id_list = list(referenced_ids)
	batch_size = 10000

	from .db import get_connection

	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			for i in range(0, len(id_list), batch_size):
				batch_ids = id_list[i : i + batch_size]
				placeholders = ", ".join(["%s"] * len(batch_ids))
				sql = f"SELECT {columns_sql} FROM `{source_table}` WHERE `{id_column}` IN ({placeholders})"
				cursor.execute(sql, batch_ids)
				all_rows.extend(cursor.fetchall())
	finally:
		conn.close()

	if not all_rows:
		empty_data = {f: [] for f in fields}
		table = pa.table(empty_data)
		pq.write_table(table, output_path)
		return output_path, 0

	# Convert to columnar format and write Parquet
	col_data = {f: [_coerce_value(row.get(f)) for row in all_rows] for f in fields}
	table = pa.table(col_data)
	pq.write_table(table, output_path)

	return output_path, len(all_rows)
