"""File validation for archive outputs — checksums, row counts, file sizes."""

import hashlib
import json
import os
from datetime import datetime, timezone

import pyarrow.parquet as pq

from .config import Config
from .db import atomic_update


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


def verify_transfer(config: Config, job_name: str, destination_path: str) -> dict:
	"""Verify archive batch integrity at the destination by comparing checksums.

	Reads the manifest from the destination, computes SHA-256 of each file there,
	and compares against manifest checksums. Updates transfer_status on the job.

	Args:
		config: Executor config for DB access.
		job_name: Archive Job name (e.g., "ARCH-00001").
		destination_path: Path to the batch directory at the destination.

	Returns:
		Dict with: valid (bool), errors (list[str]), files_checked (int).
	"""
	errors = []

	# Load manifest from destination
	manifest_path = os.path.join(destination_path, "manifest.json")
	if not os.path.isfile(manifest_path):
		errors.append(f"Manifest not found at {manifest_path}")
		_update_transfer_status(config, job_name, "Transfer Failed", errors)
		return {"valid": False, "errors": errors, "files_checked": 0}

	with open(manifest_path, "r") as f:
		manifest = json.load(f)

	files_checked = 0
	manifest_files = manifest.get("files", [])

	# Verify each file listed in the manifest
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

	# Verify file count matches
	actual_file_count = len([
		f for f in os.listdir(destination_path)
		if f != "manifest.json" and os.path.isfile(os.path.join(destination_path, f))
	])
	if actual_file_count != len(manifest_files):
		errors.append(
			f"File count mismatch: manifest lists {len(manifest_files)}, "
			f"destination has {actual_file_count}"
		)

	# Update transfer status
	if errors:
		_update_transfer_status(config, job_name, "Transfer Failed", errors)
	else:
		_update_transfer_status(config, job_name, "Transferred", [])

	return {"valid": len(errors) == 0, "errors": errors, "files_checked": files_checked}


def _update_transfer_status(config: Config, job_name: str, status: str, errors: list[str]):
	"""Update the transfer_status and transferred_at fields on the job."""
	if status == "Transferred":
		atomic_update(
			config,
			"UPDATE `tabMemora Archive Job` "
			"SET transfer_status = %s, transferred_at = NOW() "
			"WHERE name = %s",
			(status, job_name),
		)
	else:
		error_detail = "; ".join(errors) if errors else ""
		atomic_update(
			config,
			"UPDATE `tabMemora Archive Job` "
			"SET transfer_status = %s, error_log = CONCAT(COALESCE(error_log, ''), %s) "
			"WHERE name = %s",
			(status, f"\n[Transfer] {error_detail}", job_name),
		)
