"""Parquet export for fact data and dimension snapshots."""

import decimal
import os
from collections import defaultdict
from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from .config import Config
from .db import get_connection, streaming_cursor, validate_identifier


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


def _coerce_value(val, target_type: pa.DataType | None = None):
	"""Coerce Python values to types pyarrow handles cleanly."""
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


def _rows_to_batch(rows: list[dict], columns: list[str], schema: pa.Schema) -> pa.RecordBatch:
	"""Convert a list of row dicts to a pyarrow RecordBatch."""
	col_data = {
		col: [_coerce_value(row.get(col), schema.field(col).type) for row in rows] for col in columns
	}
	return pa.RecordBatch.from_pydict(col_data, schema=schema)


def _extend_schema_with_metadata(arrow_schema: pa.Schema, export_metadata: dict) -> pa.Schema:
	"""Extend an arrow schema with export_metadata columns."""
	extra_fields = []
	for key in export_metadata:
		if key == "exported_at" or key == "synced_at":
			extra_fields.append(pa.field(key, pa.timestamp("us")))
		else:
			extra_fields.append(pa.field(key, pa.string()))
	return pa.schema(list(arrow_schema) + extra_fields)


def _inject_metadata_into_rows(rows: list[dict], export_metadata: dict) -> list[dict]:
	"""Inject export_metadata values into each row dict."""
	for row in rows:
		for key, val in export_metadata.items():
			row[key] = val
	return rows


def export_fact_data(
	config: Config,
	staging_dir: str,
	meta: dict,
	source_table: str,
	archive_type_name: str,
	mode: str = "filtered",
	export_metadata: dict | None = None,
	exclusion_ranges: list[tuple[str, str]] | None = None,
) -> tuple[str, int, dict[str, set]]:
	"""Export fact data to a Parquet file using server-side streaming.

	Args:
		config: Executor configuration.
		staging_dir: Path to the staging directory for this job.
		meta: Job meta JSON with query_filter, export_columns, schema_snapshot.
		source_table: MariaDB table name (e.g., 'tabMemora Practice Log').
		archive_type_name: Archive type key for the output filename.
		mode: "filtered" (default) uses WHERE clause from query_filter;
		      "full_snapshot" exports all rows with no WHERE clause.
		export_metadata: Optional dict of metadata columns to inject into each row.
		exclusion_ranges: Optional list of (date_from, date_to) tuples. When mode="full_snapshot",
		      rows with scope_column within these ranges are excluded.

	Returns:
		Tuple of (file_path, row_count, referenced_ids) where referenced_ids
		maps fact_column name → set of unique IDs seen in the exported data.
	"""
	export_columns = meta["export_columns"]
	schema_snapshot = meta.get("schema_snapshot", {})
	related_tables = meta.get("related_tables", [])

	# Determine which fact columns to track for dimension scoping (skip derived dims)
	dimension_fact_columns = [rt["fact_column"] for rt in related_tables if rt.get("fact_column")]

	# Validate all identifiers against allowlist before SQL interpolation
	validate_identifier(source_table)
	for col in export_columns:
		validate_identifier(col)

	# Check for custom fact SQL templates (used when fact requires JOIN enrichment)
	fact_sql_templates = meta.get("fact_sql", {})

	# Build SQL query
	columns_sql = ", ".join(f"`{col}`" for col in export_columns)

	if mode == "full_snapshot":
		# Full snapshot: no WHERE clause, export all rows
		if fact_sql_templates.get("full_snapshot"):
			sql = fact_sql_templates["full_snapshot"].strip()
			params: list = []
		else:
			sql = f"SELECT {columns_sql} FROM `{source_table}`"
			params = []

		# Apply exclusion ranges if provided
		if exclusion_ranges:
			scope_col = meta.get("scope_column") or meta.get("query_filter", {}).get("filter_column")
			if scope_col:
				validate_identifier(scope_col)
				exclusion_clauses = []
				for date_from, date_to in exclusion_ranges:
					if fact_sql_templates.get("full_snapshot"):
						exclusion_clauses.append(f"NOT (pl.`{scope_col}` >= %s AND pl.`{scope_col}` < %s)")
					else:
						exclusion_clauses.append(f"NOT (`{scope_col}` >= %s AND `{scope_col}` < %s)")
					params.extend([date_from, date_to])
				if exclusion_clauses:
					sql += " WHERE " + " AND ".join(exclusion_clauses)

		params = tuple(params)
	else:
		# Filtered: use query_filter
		query_filter = meta["query_filter"]
		filter_col = query_filter["filter_column"]
		validate_identifier(filter_col)

		if query_filter.get("filter_type") == "player_scope":
			# Player-scoped: handled below via batched streaming
			sql = None
			params = None
		elif query_filter.get("filter_type") == "season":
			# Season-scoped: single season_seq parameter
			if fact_sql_templates.get("filtered"):
				sql = fact_sql_templates["filtered"].strip()
			else:
				sql = (
					f"SELECT {columns_sql} FROM `{source_table}` "
					f"WHERE `{filter_col}` = %s "
					f"ORDER BY `{filter_col}`"
				)
			params = (query_filter["season_seq"],)
		else:
			# Date-range: existing behavior
			if fact_sql_templates.get("filtered"):
				sql = fact_sql_templates["filtered"].strip().replace("{filter_column}", filter_col)
			else:
				sql = (
					f"SELECT {columns_sql} FROM `{source_table}` "
					f"WHERE `{filter_col}` >= %s AND `{filter_col}` < %s "
					f"ORDER BY `{filter_col}`"
				)
			params = (query_filter["date_from"], query_filter["date_to"])

	# Build pyarrow schema from snapshot
	arrow_schema = _build_arrow_schema(schema_snapshot) if schema_snapshot.get("columns") else None

	# Extend schema with metadata columns if provided
	if export_metadata and arrow_schema:
		arrow_schema = _extend_schema_with_metadata(arrow_schema, export_metadata)

	# Build the full list of columns for batch construction (source + metadata)
	all_columns = list(export_columns)
	if export_metadata:
		all_columns.extend(export_metadata.keys())

	output_path = os.path.join(staging_dir, f"fact_{archive_type_name}.parquet")
	row_count = 0
	referenced_ids: dict[str, set] = defaultdict(set)
	writer = None

	def _process_rows(rows):
		"""Process a chunk of rows: collect IDs, inject metadata, write batch."""
		nonlocal writer, row_count, arrow_schema

		for fact_col in dimension_fact_columns:
			referenced_ids[fact_col].update(row[fact_col] for row in rows if row.get(fact_col))

		if export_metadata:
			rows = _inject_metadata_into_rows(rows, export_metadata)

		if arrow_schema:
			batch = _rows_to_batch(rows, all_columns, arrow_schema)
		else:
			col_data = {col: [_coerce_value(row.get(col)) for row in rows] for col in all_columns}
			batch = pa.RecordBatch.from_pydict(col_data)
			arrow_schema = batch.schema

		if writer is None:
			writer = pq.ParquetWriter(output_path, arrow_schema)

		writer.write_batch(batch)
		row_count += len(rows)

	try:
		if sql is None and query_filter.get("filter_type") == "player_scope":
			# Player-scoped: batch player_ids in groups, stream each batch
			player_ids = query_filter.get("player_ids", [])
			player_batch_size = 5000
			player_sql_template = fact_sql_templates.get("player_filtered")

			for i in range(0, len(player_ids), player_batch_size):
				batch_ids = player_ids[i : i + player_batch_size]

				placeholders = ", ".join(["%s"] * len(batch_ids))
				if player_sql_template:
					batch_sql = player_sql_template.strip().replace("{placeholders}", placeholders)
				else:
					validate_identifier(source_table)
					batch_sql = (
						f"SELECT {columns_sql} FROM `{source_table}` "
						f"WHERE `player_id` IN ({placeholders}) "
						f"ORDER BY `player_id`"
					)

				with streaming_cursor(config) as cursor:
					cursor.execute(batch_sql, tuple(batch_ids))
					while True:
						rows = cursor.fetchmany(config.chunk_size)
						if not rows:
							break
						_process_rows(rows)
		else:
			with streaming_cursor(config) as cursor:
				cursor.execute(sql, params)
				while True:
					rows = cursor.fetchmany(config.chunk_size)
					if not rows:
						break
					_process_rows(rows)

	finally:
		if writer is not None:
			writer.close()

	# If no rows were exported, create an empty Parquet file with the schema
	if writer is None:
		if arrow_schema:
			writer = pq.ParquetWriter(output_path, arrow_schema)
			writer.close()
		else:
			# No schema snapshot and no rows — create a minimal empty file
			# with string columns so downstream validators find a valid Parquet file
			fallback_fields = [pa.field(col, pa.string()) for col in all_columns]
			fallback_schema = pa.schema(fallback_fields)
			writer = pq.ParquetWriter(output_path, fallback_schema)
			writer.close()

	return output_path, row_count, dict(referenced_ids)


def _export_dimension_query(
	config: Config,
	staging_dir: str,
	dim_schema: dict,
	referenced_ids: set,
) -> tuple[str, int]:
	"""Export a dimension using a custom JOIN query from the schema.

	The query must contain a {placeholders} token that will be replaced
	with parameterized %s placeholders for the IN clause.
	"""
	entity = dim_schema["entity"]
	fields = dim_schema["fields"]
	query_template = dim_schema["query"]

	output_path = os.path.join(staging_dir, f"dim_{entity}.parquet")

	if not referenced_ids:
		empty_data = {f: [] for f in fields}
		table = pa.table(empty_data)
		pq.write_table(table, output_path)
		return output_path, 0

	all_rows = []
	id_list = list(referenced_ids)
	batch_size = 10000

	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			for i in range(0, len(id_list), batch_size):
				batch_ids = id_list[i : i + batch_size]
				placeholders = ", ".join(["%s"] * len(batch_ids))
				sql = query_template.replace("{placeholders}", placeholders)
				cursor.execute(sql, batch_ids)
				all_rows.extend(cursor.fetchall())
	finally:
		conn.close()

	if not all_rows:
		empty_data = {f: [] for f in fields}
		table = pa.table(empty_data)
		pq.write_table(table, output_path)
		return output_path, 0

	col_data = {f: [_coerce_value(row.get(f)) for row in all_rows] for f in fields}
	table = pa.table(col_data)
	pq.write_table(table, output_path)

	return output_path, len(all_rows)


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
	# If the schema has a custom query, use the JOIN-based export
	if "query" in dim_schema:
		return _export_dimension_query(config, staging_dir, dim_schema, referenced_ids)

	entity = dim_schema["entity"]
	source_table = dim_schema["source_table"]
	id_column = dim_schema["id_column"]
	fields = dim_schema["fields"]

	# Validate all identifiers against allowlist before SQL interpolation
	validate_identifier(source_table)
	validate_identifier(id_column)
	for f in fields:
		validate_identifier(f)

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
