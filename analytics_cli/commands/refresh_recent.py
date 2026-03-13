"""Rebuild rolling recent-window materialized table from combined view.

Rebuilds ``practice_recent`` from ``practice_log_combined`` filtered
to the last *window_days* days.
"""

from __future__ import annotations

import json
import logging
import sys
import time

import click

from analytics_cli.db import connect

log = logging.getLogger("memora-analytics")


@click.command("refresh-recent")
@click.option("--archive-type", required=True, help="Archive type (e.g. practice_log).")
@click.option(
    "--window-days",
    type=int,
    default=90,
    help="Number of recent days to include (default 90).",
)
@click.pass_obj
def refresh_recent(cfg, archive_type: str, window_days: int) -> None:
    """Rebuild rolling recent-N-days materialized table from combined view."""
    t0 = time.monotonic()

    if archive_type != "practice_log":
        _emit_error(f"Unsupported archive-type for refresh-recent: {archive_type}")

    try:
        with connect(cfg) as conn:
            conn.execute(
                f"CREATE OR REPLACE TABLE practice_recent AS "
                f"SELECT player_id, item_id, first_seen_at, last_seen_at, "
                f"       last_result, attempt_count, correct_count, "
                f"       season_id, plan_id, source "
                f"FROM practice_log_combined "
                f"WHERE last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '{window_days}' DAY"
            )
            row_count = conn.execute(
                "SELECT COUNT(*) FROM practice_recent"
            ).fetchone()[0]
            oldest = conn.execute(
                "SELECT MIN(last_seen_at) FROM practice_recent"
            ).fetchone()[0]
    except Exception as exc:
        _emit_error(f"refresh-recent failed: {exc}")
        return

    duration_ms = int((time.monotonic() - t0) * 1000)
    click.echo(
        json.dumps(
            {
                "status": "ok",
                "row_count": row_count,
                "window_days": window_days,
                "oldest_record": oldest.isoformat() if oldest else None,
                "duration_ms": duration_ms,
            }
        )
    )


def _emit_error(msg: str) -> None:
    """Emit a JSON error and exit."""
    click.echo(json.dumps({"status": "error", "error": msg}))
    sys.exit(1)
