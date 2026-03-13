"""Partition size analysis health check for the analytics lakehouse.

Scans lake directories for Parquet files and identifies partitions
with individual files smaller than a configurable threshold.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("memora-analytics")


def check_partition_sizes(
    lake_path: str,
    threshold_mb: int = 64,
) -> dict:
    """Analyse Parquet file sizes within lake partitions.

    Walks the *lake_path* directory tree looking for ``.parquet`` files.
    Any individual file smaller than *threshold_mb* megabytes is flagged.

    Returns a dict with:
        - status: "pass" or "warning"
        - total_partitions: number of partition directories containing
          at least one Parquet file
        - undersized_partitions: number of partitions with an
          undersized file
        - details: list of dicts with partition, file, and size_mb for
          every undersized file
    """
    lake = Path(lake_path)
    if not lake.is_dir():
        return {
            "status": "pass",
            "total_partitions": 0,
            "undersized_partitions": 0,
            "details": [],
        }

    threshold_bytes = threshold_mb * 1024 * 1024

    # Collect all partition directories that contain parquet files
    partition_dirs: dict[str, list[Path]] = {}
    for pq_file in lake.rglob("*.parquet"):
        partition_key = str(pq_file.parent.relative_to(lake))
        partition_dirs.setdefault(partition_key, []).append(pq_file)

    total_partitions = len(partition_dirs)
    if total_partitions == 0:
        return {
            "status": "pass",
            "total_partitions": 0,
            "undersized_partitions": 0,
            "details": [],
        }

    details: list[dict] = []
    undersized_partition_set: set[str] = set()

    for partition_key, files in sorted(partition_dirs.items()):
        for pq_file in files:
            size_bytes = pq_file.stat().st_size
            if size_bytes < threshold_bytes:
                size_mb = round(size_bytes / (1024 * 1024), 2)
                details.append({
                    "partition": partition_key,
                    "file": pq_file.name,
                    "size_mb": size_mb,
                })
                undersized_partition_set.add(partition_key)

    undersized_partitions = len(undersized_partition_set)
    status = "warning" if undersized_partitions > 0 else "pass"

    return {
        "status": status,
        "total_partitions": total_partitions,
        "undersized_partitions": undersized_partitions,
        "details": details,
    }
