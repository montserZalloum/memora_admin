"""Manifest builder for archive batches.

Manifest schema version 1.0 — compatible with the analytics-side Manifest Pydantic model.
"""

import json
import os
from datetime import datetime, timezone


def build_manifest(
	staging_dir: str,
	batch_id: str,
	dataset_key: str,
	kind: str,
	schema_version: str,
	source: str,
	files: list[dict],
	scope_key: str | None = None,
) -> str:
	"""Write a manifest.json file to the staging directory.

	Args:
		staging_dir: Path to the staging directory for this batch.
		batch_id: Job name (e.g., 'ARCH-00001', 'LSYNC-00001').
		dataset_key: Dataset identifier (e.g., 'practice_log_archive', 'practice_log_live').
		kind: Batch kind — 'archive' or 'live'.
		schema_version: Dataset schema version string (e.g., '1.0').
		source: Producer identifier (e.g., 'memora_admin').
		files: List of file entry dicts. Required keys per entry:
			- role: 'fact' or 'dimension'
			- entity: Entity name (e.g., 'practice_log', 'player')
			- filename: File name within the batch directory
			- row_count: Number of rows in the file
			- checksum: SHA-256 hash prefixed with 'sha256:'
			- size_bytes: File size in bytes
			- snapshot_schema_version (dimension only): Schema version of dimension snapshot
		scope_key: Optional scope identifier (e.g., season name 'SEAS-00027').

	Returns:
		Path to the written manifest.json file.
	"""
	manifest: dict = {
		"manifest_version": "1.0",
		"dataset_key": dataset_key,
		"kind": kind,
		"batch_id": batch_id,
		"schema_version": schema_version,
		"created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"source": source,
		"files": files,
	}
	if scope_key:
		manifest["scope_key"] = scope_key

	output_path = os.path.join(staging_dir, "manifest.json")
	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(manifest, f, indent=2, default=str)

	return output_path
