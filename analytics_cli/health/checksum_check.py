"""Checksum verification health check for the analytics lakehouse.

Verifies SHA-256 checksums of Parquet files against manifest entries
stored in ``manifests/archive/*.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger("memora-analytics")


def check_checksums(manifests_path: str) -> dict:
    """Verify SHA-256 checksums of Parquet files referenced by manifests.

    Scans ``manifests_path/archive/*.json`` for manifest files.  Each
    manifest is expected to contain a ``"files"`` list where each entry
    has ``"path"``, ``"sha256"``, and ``"rows"`` keys.

    Returns a dict with:
        - status: "pass" or "fail"
        - files_checked: number of files verified
        - mismatches: list of dicts with file, expected, and actual SHA-256
    """
    archive_dir = Path(manifests_path) / "archive"
    if not archive_dir.is_dir():
        return {"status": "pass", "files_checked": 0, "mismatches": []}

    manifest_files = sorted(archive_dir.glob("*.json"))
    if not manifest_files:
        return {"status": "pass", "files_checked": 0, "mismatches": []}

    files_checked = 0
    mismatches: list[dict] = []

    for manifest_file in manifest_files:
        try:
            manifest = json.loads(manifest_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Cannot read manifest %s: %s", manifest_file, exc)
            continue

        file_entries = manifest.get("files", [])
        for entry in file_entries:
            file_path = entry.get("path")
            expected_sha = entry.get("sha256")
            if not file_path or not expected_sha:
                continue

            target = Path(file_path)
            if not target.is_file():
                log.debug("File %s not found — skipping checksum", file_path)
                continue

            actual_sha = _sha256_file(target)
            files_checked += 1

            if actual_sha != expected_sha:
                mismatches.append({
                    "file": str(file_path),
                    "expected": expected_sha,
                    "actual": actual_sha,
                })

    status = "fail" if mismatches else "pass"
    return {
        "status": status,
        "files_checked": files_checked,
        "mismatches": mismatches,
    }


def _sha256_file(path: Path) -> str:
    """Compute the hex-encoded SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
