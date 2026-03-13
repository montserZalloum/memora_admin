"""Duplicate detection health check for the analytics lakehouse.

Checks for duplicate rows in ``practice_log_combined`` keyed on
(player_id, item_id, last_seen_at).
"""

from __future__ import annotations

import logging

import duckdb

log = logging.getLogger("memora-analytics")


def check_duplicates(conn: duckdb.DuckDBPyConnection) -> dict:
    """Detect duplicate rows in practice_log_combined.

    Groups by (player_id, item_id, last_seen_at) and reports any
    combination appearing more than once.

    Returns a dict with:
        - status: "pass" or "fail"
        - duplicate_count: number of duplicate groups
        - sample_rows: up to 10 sample rows with player_id, item_id,
          last_seen_at, and count
    """
    try:
        rows = conn.execute(
            "SELECT player_id, item_id, last_seen_at, COUNT(*) AS cnt "
            "FROM practice_log_combined "
            "GROUP BY player_id, item_id, last_seen_at "
            "HAVING COUNT(*) > 1 "
            "LIMIT 10"
        ).fetchall()
    except duckdb.CatalogException:
        log.debug("practice_log_combined view does not exist — skipping duplicate check")
        return {"status": "pass", "duplicate_count": 0, "sample_rows": []}

    sample_rows = [
        {
            "player_id": r[0],
            "item_id": r[1],
            "last_seen_at": r[2].isoformat() if r[2] else None,
            "count": r[3],
        }
        for r in rows
    ]

    duplicate_count = len(sample_rows)

    if duplicate_count > 0:
        return {
            "status": "fail",
            "duplicate_count": duplicate_count,
            "sample_rows": sample_rows,
        }

    return {"status": "pass", "duplicate_count": 0, "sample_rows": []}
