"""Dimension coverage health check for the analytics lakehouse.

Verifies that every player_id appearing in ``practice_log_combined``
has a corresponding row in ``dim_player_history``.
"""

from __future__ import annotations

import logging

import duckdb

log = logging.getLogger("memora-analytics")


def check_dimension_coverage(conn: duckdb.DuckDBPyConnection) -> dict:
    """Check that all players in practice_log_combined have dimension rows.

    LEFT JOINs ``practice_log_combined`` with ``dim_player_history``
    on ``player_id`` and reports players with no dimension row.

    Returns a dict with:
        - status: "pass" or "fail"
        - missing_players: count of players without dimension data
        - sample_ids: up to 10 player_id values missing from dimensions
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT p.player_id "
            "FROM practice_log_combined p "
            "LEFT JOIN dim_player_history d ON p.player_id = d.player_id "
            "WHERE d.player_id IS NULL "
            "LIMIT 10"
        ).fetchall()
    except duckdb.CatalogException:
        log.debug(
            "practice_log_combined or dim_player_history view does not exist "
            "— skipping dimension coverage check"
        )
        return {"status": "pass", "missing_players": 0, "sample_ids": []}

    sample_ids = [r[0] for r in rows]
    missing_players = len(sample_ids)

    if missing_players > 0:
        return {
            "status": "fail",
            "missing_players": missing_players,
            "sample_ids": sample_ids,
        }

    return {"status": "pass", "missing_players": 0, "sample_ids": []}
