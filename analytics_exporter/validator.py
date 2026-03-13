"""Data quality validation for exported Parquet files.

Supported rule types:
  unique_key  — no duplicate values in the specified column(s)
  not_null    — no null values in the specified column
  min_value   — no values below the specified minimum in the column
  min_rows    — table must have at least the specified number of rows

validate_export() returns a list of violation messages.
An empty list means all rules passed.
"""

import pyarrow.parquet as pq


def validate_export(parquet_path: str, dq_rules: list[dict]) -> list[str]:
	"""Validate a Parquet file against a list of DQ rules.

	Args:
		parquet_path: Path to the Parquet file to validate.
		dq_rules: List of rule dicts, each with at minimum {id, type}.

	Returns:
		List of violation message strings. Empty list means all rules passed.
	"""
	if not dq_rules:
		return []

	table = pq.read_table(parquet_path)
	violations: list[str] = []

	for rule in dq_rules:
		rule_type = rule["type"]
		rule_id = rule.get("id", "unknown")

		if rule_type == "unique_key":
			cols = rule["columns"]
			arrays = [table.column(c).to_pylist() for c in cols]
			keys = list(zip(*arrays)) if len(cols) > 1 else list(arrays[0])
			if len(keys) != len(set(keys)):
				violations.append(
					f"{rule_id}: duplicate values found in column(s) {cols}"
				)

		elif rule_type == "not_null":
			col = rule["column"]
			null_count = table.column(col).null_count
			if null_count > 0:
				violations.append(
					f"{rule_id}: column '{col}' has {null_count} null value(s)"
				)

		elif rule_type == "min_value":
			col = rule["column"]
			min_val = rule["min"]
			values = [v for v in table.column(col).to_pylist() if v is not None]
			if values and min(values) < min_val:
				violations.append(
					f"{rule_id}: column '{col}' has values below minimum {min_val}"
				)

		elif rule_type == "min_rows":
			min_rows = rule["min"]
			if table.num_rows < min_rows:
				violations.append(
					f"{rule_id}: table has {table.num_rows} row(s), expected >= {min_rows}"
				)

	return violations
