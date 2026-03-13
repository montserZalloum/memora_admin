"""Rebuild daily and monthly aggregate tables from combined view.

Rebuilds ``practice_daily_agg`` and ``practice_monthly_agg`` from
``practice_log_combined`` per data-model.md section 2.5.
"""

from __future__ import annotations

import json
import logging
import sys
import time

import click

from analytics_cli.db import connect

log = logging.getLogger("memora-analytics")


@click.command("refresh-aggregates")
@click.option("--archive-type", required=True, help="Archive type (e.g. practice_log).")
@click.pass_obj
def refresh_aggregates(cfg, archive_type: str) -> None:
    """Rebuild daily and monthly aggregate tables from combined view."""
    t0 = time.monotonic()

    if archive_type != "practice_log":
        _emit_error(
            f"Unsupported archive-type for refresh-aggregates: {archive_type}"
        )

    try:
        with connect(cfg) as conn:
            # Daily aggregates
            conn.execute("""\
CREATE OR REPLACE TABLE practice_daily_agg AS
SELECT CAST(last_seen_at AS DATE) AS date,
       player_id,
       season_id,
       plan_id,
       SUM(attempt_count) AS total_attempts,
       SUM(correct_count) AS total_correct,
       COUNT(DISTINCT item_id) AS unique_items
FROM practice_log_combined
GROUP BY CAST(last_seen_at AS DATE), player_id, season_id, plan_id""")

            daily_rows = conn.execute(
                "SELECT COUNT(*) FROM practice_daily_agg"
            ).fetchone()[0]

            # Monthly aggregates
            conn.execute("""\
CREATE OR REPLACE TABLE practice_monthly_agg AS
SELECT STRFTIME(last_seen_at, '%Y-%m') AS year_month,
       player_id,
       season_id,
       plan_id,
       SUM(attempt_count) AS total_attempts,
       SUM(correct_count) AS total_correct,
       COUNT(DISTINCT item_id) AS unique_items,
       COUNT(DISTINCT CAST(last_seen_at AS DATE)) AS active_days
FROM practice_log_combined
GROUP BY STRFTIME(last_seen_at, '%Y-%m'), player_id, season_id, plan_id""")

            monthly_rows = conn.execute(
                "SELECT COUNT(*) FROM practice_monthly_agg"
            ).fetchone()[0]

    except Exception as exc:
        _emit_error(f"refresh-aggregates failed: {exc}")
        return

    duration_ms = int((time.monotonic() - t0) * 1000)
    click.echo(
        json.dumps(
            {
                "status": "ok",
                "daily_rows": daily_rows,
                "monthly_rows": monthly_rows,
                "duration_ms": duration_ms,
            }
        )
    )


def _emit_error(msg: str) -> None:
    """Emit a JSON error and exit."""
    click.echo(json.dumps({"status": "error", "error": msg}))
    sys.exit(1)
