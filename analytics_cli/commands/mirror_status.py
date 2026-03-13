"""Report per-season row counts and mirror health for monitoring.

Queries ``memory_state_current`` and ``memory_state_archive`` for
per-season row counts to give operators a view of the mirror state.
"""

from __future__ import annotations

import json
import logging
import sys
import time

import click
import duckdb

from analytics_cli.db import connect

log = logging.getLogger("memora-analytics")


@click.command("mirror-status")
@click.option("--archive-type", required=True, help="Archive type (e.g. memory_state).")
@click.pass_obj
def mirror_status(cfg, archive_type: str) -> None:
    """Report per-season row counts and mirror health."""
    t0 = time.monotonic()

    if archive_type != "memory_state":
        _emit_error(
            f"Unsupported archive-type for mirror-status: {archive_type}"
        )

    try:
        with connect(cfg) as conn:
            # Current mirror stats
            current_mirror = _query_current_mirror(conn)

            # Archived season stats
            archived_seasons = _query_archived_seasons(conn, cfg.lake_path)
    except Exception as exc:
        _emit_error(f"mirror-status failed: {exc}")
        return

    duration_ms = int((time.monotonic() - t0) * 1000)
    click.echo(
        json.dumps(
            {
                "status": "ok",
                "archive_type": archive_type,
                "current_mirror": current_mirror,
                "archived_seasons": archived_seasons,
                "duration_ms": duration_ms,
            }
        )
    )


def _query_current_mirror(conn: duckdb.DuckDBPyConnection) -> dict:
    """Query memory_state_current for per-season stats."""
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM memory_state_current"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT season_seq, COUNT(*) AS row_count, "
            "MAX(modified) AS latest_modified "
            "FROM memory_state_current "
            "GROUP BY season_seq ORDER BY season_seq"
        ).fetchall()
    except duckdb.CatalogException:
        return {"total_rows": 0, "seasons": []}

    seasons = [
        {
            "season_seq": r[0],
            "row_count": r[1],
            "latest_modified": r[2].isoformat() if r[2] else None,
        }
        for r in rows
    ]
    return {"total_rows": total, "seasons": seasons}


def _query_archived_seasons(
    conn: duckdb.DuckDBPyConnection, lake_path: str
) -> list[dict]:
    """Query memory_state_archive for per-season stats."""
    try:
        rows = conn.execute(
            "SELECT season_seq, COUNT(*) AS row_count "
            "FROM memory_state_archive "
            "GROUP BY season_seq ORDER BY season_seq"
        ).fetchall()
    except duckdb.CatalogException:
        return []

    return [
        {
            "season_seq": r[0],
            "parquet_path": f"{lake_path}/memory_state/season_seq={r[0]}",
            "row_count": r[1],
        }
        for r in rows
    ]


def _emit_error(msg: str) -> None:
    """Emit a JSON error and exit."""
    click.echo(json.dumps({"status": "error", "error": msg}))
    sys.exit(1)
