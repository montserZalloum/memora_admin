"""File validation for archive outputs — checksums, row counts, file sizes, data quality."""

import hashlib
import os
from datetime import datetime

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


def compute_sha256(file_path: str) -> str:
	"""Compute the SHA-256 checksum of a file.

	Returns:
		Hex digest prefixed with 'sha256:'.
	"""
	h = hashlib.sha256()
	with open(file_path, "rb") as f:
		for chunk in iter(lambda: f.read(8192), b""):
			h.update(chunk)
	return f"sha256:{h.hexdigest()}"


def get_parquet_row_count(file_path: str) -> int:
	"""Read the row count from a Parquet file's metadata (no full scan)."""
	meta = pq.read_metadata(file_path)
	return meta.num_rows


def validate_file(file_path: str, expected_row_count: int) -> dict:
	"""Validate a Parquet file against expected row count.

	Args:
		file_path: Path to the Parquet file.
		expected_row_count: Expected number of rows.

	Returns:
		Dict with keys: valid (bool), file_path, filename, row_count,
		expected_row_count, checksum, size_bytes, errors (list[str]).
	"""
	errors = []
	filename = os.path.basename(file_path)
	size_bytes = os.path.getsize(file_path)

	# Verify row count
	actual_row_count = get_parquet_row_count(file_path)
	if actual_row_count != expected_row_count:
		errors.append(f"Row count mismatch: expected {expected_row_count}, got {actual_row_count}")

	# Compute checksum
	checksum = compute_sha256(file_path)

	return {
		"valid": len(errors) == 0,
		"file_path": file_path,
		"filename": filename,
		"row_count": actual_row_count,
		"expected_row_count": expected_row_count,
		"checksum": checksum,
		"size_bytes": size_bytes,
		"errors": errors,
	}


def verify_local_transfer(destination_path: str, manifest: dict) -> dict:
	"""Verify archive batch integrity at a local destination by comparing checksums.

	Args:
		destination_path: Path to the batch directory.
		manifest: Parsed manifest dict with 'files' list.

	Returns:
		Dict with: valid (bool), errors (list[str]), files_checked (int).
	"""
	errors = []
	files_checked = 0
	manifest_files = manifest.get("files", [])

	for file_entry in manifest_files:
		filename = file_entry["filename"]
		expected_checksum = file_entry["checksum"]
		file_path = os.path.join(destination_path, filename)

		if not os.path.isfile(file_path):
			errors.append(f"File missing at destination: {filename}")
			continue

		actual_checksum = compute_sha256(file_path)
		if actual_checksum != expected_checksum:
			errors.append(
				f"Checksum mismatch for {filename}: "
				f"expected {expected_checksum}, got {actual_checksum}"
			)

		files_checked += 1

	return {"valid": len(errors) == 0, "errors": errors, "files_checked": files_checked}


# ---------------------------------------------------------------------------
# Data Quality (DQ) validation — 16 hard-fail rules
# ---------------------------------------------------------------------------

_VALID_LAST_RESULT = {"Correct", "Incorrect"}

# Columns that must not contain nulls (DQ-01..DQ-07)
_NOT_NULL_COLUMNS = [
	"player_id",
	"item_id",
	"first_seen_at",
	"last_seen_at",
	"last_result",
	"attempt_count",
	"correct_count",
]


def validate_fact_quality(
	fact_path: str,
	dim_player_path: str | None = None,
	dim_review_item_path: str | None = None,
	scope_date_from: str | None = None,
	scope_date_to: str | None = None,
) -> dict:
	"""Validate fact data against 16 data quality rules.

	Args:
		fact_path: Path to the fact Parquet file.
		dim_player_path: Path to the player dimension Parquet (for referential check).
		dim_review_item_path: Path to the review_item dimension Parquet (for referential check).
		scope_date_from: Archive scope start date (inclusive) for DQ-13.
		scope_date_to: Archive scope end date (exclusive) for DQ-13.

	Returns:
		Dict with: passed (bool), results (list of per-rule dicts), warnings (list[str]).
	"""
	results = []
	warnings = []

	fact_table = pq.read_table(fact_path)
	row_count = fact_table.num_rows

	# Warning for empty dataset
	if row_count == 0:
		warnings.append("DQ-WARN: Fact table has 0 rows — valid but flagged")
		return {
			"passed": True,
			"results": [],
			"warnings": warnings,
		}

	fact_columns = set(fact_table.column_names)

	# DQ-01..DQ-07: Null checks on source columns
	for idx, col_name in enumerate(_NOT_NULL_COLUMNS, start=1):
		rule_id = f"DQ-{idx:02d}"
		if col_name not in fact_columns:
			results.append({"rule": rule_id, "passed": False, "detail": f"Column {col_name} missing from fact"})
			continue
		col = fact_table.column(col_name)
		null_count = col.null_count
		passed = null_count == 0
		results.append({
			"rule": rule_id,
			"passed": passed,
			"detail": f"{col_name} null_count={null_count}" if not passed else f"{col_name} OK",
		})

	# DQ-08: attempt_count >= 1
	if "attempt_count" in fact_columns:
		col = fact_table.column("attempt_count")
		min_val = pc.min(col).as_py()
		passed = min_val is not None and min_val >= 1
		results.append({
			"rule": "DQ-08",
			"passed": passed,
			"detail": f"attempt_count min={min_val}" if not passed else "attempt_count >= 1 OK",
		})
	else:
		results.append({"rule": "DQ-08", "passed": False, "detail": "attempt_count column missing"})

	# DQ-09: correct_count >= 0
	if "correct_count" in fact_columns:
		col = fact_table.column("correct_count")
		min_val = pc.min(col).as_py()
		passed = min_val is not None and min_val >= 0
		results.append({
			"rule": "DQ-09",
			"passed": passed,
			"detail": f"correct_count min={min_val}" if not passed else "correct_count >= 0 OK",
		})
	else:
		results.append({"rule": "DQ-09", "passed": False, "detail": "correct_count column missing"})

	# DQ-10: correct_count <= attempt_count
	if "correct_count" in fact_columns and "attempt_count" in fact_columns:
		correct = fact_table.column("correct_count")
		attempt = fact_table.column("attempt_count")
		violations = pc.sum(pc.greater(correct, attempt)).as_py()
		passed = violations == 0
		results.append({
			"rule": "DQ-10",
			"passed": passed,
			"detail": f"correct > attempt in {violations} rows" if not passed else "correct <= attempt OK",
		})
	else:
		results.append({"rule": "DQ-10", "passed": False, "detail": "correct_count or attempt_count missing"})

	# DQ-11: last_result values all in {'Correct', 'Incorrect'}
	if "last_result" in fact_columns:
		col = fact_table.column("last_result")
		unique_vals = pc.unique(col).to_pylist()
		invalid_vals = {v for v in unique_vals if v is not None and v not in _VALID_LAST_RESULT}
		passed = len(invalid_vals) == 0
		results.append({
			"rule": "DQ-11",
			"passed": passed,
			"detail": f"Invalid last_result values: {invalid_vals}" if not passed else "last_result values OK",
		})
	else:
		results.append({"rule": "DQ-11", "passed": False, "detail": "last_result column missing"})

	# DQ-12: first_seen_at <= last_seen_at
	if "first_seen_at" in fact_columns and "last_seen_at" in fact_columns:
		first = fact_table.column("first_seen_at")
		last = fact_table.column("last_seen_at")
		# Cast string columns to timestamp to ensure correct temporal comparison
		if pa.types.is_string(first.type) or pa.types.is_large_string(first.type):
			first = pc.cast(first, pa.timestamp("us"))
		if pa.types.is_string(last.type) or pa.types.is_large_string(last.type):
			last = pc.cast(last, pa.timestamp("us"))
		violations = pc.sum(pc.greater(first, last)).as_py()
		passed = violations == 0
		results.append({
			"rule": "DQ-12",
			"passed": passed,
			"detail": f"first_seen_at > last_seen_at in {violations} rows" if not passed
			else "first_seen_at <= last_seen_at OK",
		})
	else:
		results.append({"rule": "DQ-12", "passed": False, "detail": "first_seen_at or last_seen_at missing"})

	# DQ-13: last_seen_at within archive scope date range (if provided)
	if scope_date_from and scope_date_to and "last_seen_at" in fact_columns:
		col = fact_table.column("last_seen_at")
		# Cast string columns to timestamp to support both storage formats
		if pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
			col = pc.cast(col, pa.timestamp("us"))
		scope_from = pa.scalar(datetime.fromisoformat(scope_date_from), type=pa.timestamp("us"))
		scope_to = pa.scalar(datetime.fromisoformat(scope_date_to), type=pa.timestamp("us"))
		below = pc.sum(pc.less(col, scope_from)).as_py()
		above_or_eq = pc.sum(pc.greater_equal(col, scope_to)).as_py()
		out_of_range = (below or 0) + (above_or_eq or 0)
		passed = out_of_range == 0
		results.append({
			"rule": "DQ-13",
			"passed": passed,
			"detail": f"{out_of_range} rows outside scope [{scope_date_from}, {scope_date_to})"
			if not passed else "last_seen_at within scope OK",
		})
	else:
		# No scope provided — skip (not a failure)
		results.append({"rule": "DQ-13", "passed": True, "detail": "Skipped (no scope dates provided)"})

	# DQ-14: All player_id exist in dim_player Parquet
	if dim_player_path and "player_id" in fact_columns:
		dim_table = pq.read_table(dim_player_path)
		# Player v2 uses "player_id", v1 uses "name"
		if "player_id" in dim_table.column_names:
			dim_ids = set(dim_table.column("player_id").to_pylist())
		elif "name" in dim_table.column_names:
			dim_ids = set(dim_table.column("name").to_pylist())
		else:
			dim_ids = set()

		fact_ids = set(fact_table.column("player_id").to_pylist())
		orphans = fact_ids - dim_ids - {None}
		passed = len(orphans) == 0
		results.append({
			"rule": "DQ-14",
			"passed": passed,
			"detail": f"{len(orphans)} player_ids not in dimension" if not passed else "player referential OK",
		})
	else:
		results.append({"rule": "DQ-14", "passed": True, "detail": "Skipped (no dim_player_path)"})

	# DQ-15: All item_id exist in dim_review_item Parquet
	if dim_review_item_path and "item_id" in fact_columns:
		dim_table = pq.read_table(dim_review_item_path)
		if "item_id" in dim_table.column_names:
			dim_ids = set(dim_table.column("item_id").to_pylist())
		else:
			dim_ids = set()

		fact_ids = set(fact_table.column("item_id").to_pylist())
		orphans = fact_ids - dim_ids - {None}
		passed = len(orphans) == 0
		results.append({
			"rule": "DQ-15",
			"passed": passed,
			"detail": f"{len(orphans)} item_ids not in dimension" if not passed else "item referential OK",
		})
	else:
		results.append({"rule": "DQ-15", "passed": True, "detail": "Skipped (no dim_review_item_path)"})

	# DQ-16: No duplicate (player_id, item_id) pairs
	if "player_id" in fact_columns and "item_id" in fact_columns:
		# Use groupby to find duplicates efficiently
		grouped = fact_table.group_by(["player_id", "item_id"]).aggregate([("player_id", "count")])
		dup_count_col = grouped.column("player_id_count")
		max_dup = pc.max(dup_count_col).as_py()
		has_dups = max_dup is not None and max_dup > 1
		if has_dups:
			dups = pc.sum(pc.greater(dup_count_col, pa.scalar(1, type=dup_count_col.type))).as_py()
			results.append({
				"rule": "DQ-16",
				"passed": False,
				"detail": f"{dups} duplicate (player_id, item_id) pairs",
			})
		else:
			results.append({"rule": "DQ-16", "passed": True, "detail": "No duplicate pairs OK"})
	else:
		results.append({"rule": "DQ-16", "passed": False, "detail": "player_id or item_id missing"})

	all_passed = all(r["passed"] for r in results)
	return {
		"passed": all_passed,
		"results": results,
		"warnings": warnings,
	}
