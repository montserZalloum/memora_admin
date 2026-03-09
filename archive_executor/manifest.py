"""Manifest builder for archive batches."""

import json
import os
from datetime import datetime, timezone


def build_manifest(
	staging_dir: str,
	batch_id: str,
	source_doctype: str,
	archive_scope: str,
	schema_version: str,
	snapshot_taken_at: str,
	files: list[dict],
) -> str:
	"""Write a manifest.json file to the staging directory.

	Args:
		staging_dir: Path to the staging directory for this batch.
		batch_id: Archive job name (e.g., 'ARCH-00001').
		source_doctype: Source DocType name (e.g., 'Memora Practice Log').
		archive_scope: Season ID (e.g., 'SEAS-00027').
		schema_version: Schema version string (e.g., 'v1').
		snapshot_taken_at: ISO timestamp of when dimension snapshots were taken.
		files: List of file entry dicts, each with keys:
			- role: 'fact' or 'dimension'
			- filename: File name within the batch directory
			- row_count: Number of rows in the file
			- checksum: SHA-256 hash prefixed with 'sha256:'
			- size_bytes: File size in bytes
			- entity (dimension only): Dimension entity name
			- snapshot_schema_version (dimension only): Schema version
			- scope (dimension only): e.g., 'batch_referenced'
			- referenced_by (dimension only): Fact column that references this dimension

	Returns:
		Path to the written manifest.json file.
	"""
	manifest = {
		"batch_id": batch_id,
		"source_doctype": source_doctype,
		"archive_scope": archive_scope,
		"schema_version": schema_version,
		"created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"snapshot_taken_at": snapshot_taken_at,
		"files": files,
	}

	output_path = os.path.join(staging_dir, "manifest.json")
	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(manifest, f, indent=2, default=str)

	return output_path
