"""Manifest generation for analytics dataset exports.

Each dataset produces a sidecar manifest file at {output_dir}/{dataset_key}.manifest.json
containing SHA-256 checksums, row counts, and file sizes for integrity verification.
"""

import hashlib
import json
import os
from datetime import datetime, timezone


def compute_sha256(file_path: str) -> str:
    """Compute the SHA-256 hex digest of a file using streaming 64KB chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def write_manifest(
    output_dir: str,
    dataset_key: str,
    files_info: list[dict],
    kind: str = "analytics",
) -> str:
    """Write a manifest JSON file for a dataset export.

    Args:
        output_dir: Directory containing the Parquet files.
        dataset_key: The dataset identifier (e.g., 'dim_player' or 'fact_challenge').
        files_info: List of dicts, each with keys:
            - filename: str (e.g., 'dim_player.parquet')
            - row_count: int
            - checksum: str (hex digest from compute_sha256)
            - size_bytes: int
        kind: Manifest kind identifier.

    Returns:
        Path to the written manifest file.
    """
    manifest = {
        "manifest_version": "1.0",
        "dataset_key": dataset_key,
        "kind": kind,
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "memora_admin",
        "files": [
            {
                "filename": fi["filename"],
                "row_count": fi["row_count"],
                "checksum": f"sha256:{fi['checksum']}",
                "size_bytes": fi["size_bytes"],
            }
            for fi in files_info
        ],
    }

    manifest_path = os.path.join(output_dir, f"{dataset_key}.manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return manifest_path
