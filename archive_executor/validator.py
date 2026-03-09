"""File validation for archive outputs — checksums, row counts, file sizes."""

import hashlib
import os

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
