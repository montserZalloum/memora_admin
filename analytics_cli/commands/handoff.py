"""Remove archived rows from live DuckDB tables to prevent double-counting.

Supports two modes:
- Date-range: DELETE from practice_log_live by date column and range
- Season: DELETE from memory_state_current by season_seq
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import click

from analytics_cli.db import connect

log = logging.getLogger("memora-analytics")

_VALID_DATE_COLUMNS = frozenset(
    {"last_seen_at", "first_seen_at", "timestamp", "completed_at"}
)


@click.command("handoff")
@click.option(
    "--archive-batch-dir",
    required=True,
    help="Path to archived batch directory (for audit trail).",
)
@click.option(
    "--date-column",
    default=None,
    help="Date column for range-based handoff.",
)
@click.option(
    "--from",
    "from_date",
    default=None,
    help="Start date (inclusive) YYYY-MM-DD.",
)
@click.option(
    "--to",
    "to_date",
    default=None,
    help="End date (inclusive) YYYY-MM-DD.",
)
@click.option(
    "--season-seq",
    type=int,
    default=None,
    help="Season sequence for season-based handoff.",
)
@click.option(
    "--archive-type",
    default=None,
    help="Archive type (e.g. memory_state).",
)
@click.pass_obj
def handoff(
    cfg,
    archive_batch_dir: str,
    date_column: str | None,
    from_date: str | None,
    to_date: str | None,
    season_seq: int | None,
    archive_type: str | None,
) -> None:
    """Remove archived rows from live DuckDB tables."""
    t0 = time.monotonic()
    batch_path = Path(archive_batch_dir)

    if not batch_path.is_dir():
        _emit_error(f"archive-batch-dir does not exist: {archive_batch_dir}")

    is_date_range = all(v is not None for v in (date_column, from_date, to_date))
    is_season = season_seq is not None and archive_type is not None

    if not is_date_range and not is_season:
        _emit_error(
            "Provide either --date-column/--from/--to or --season-seq/--archive-type"
        )

    if is_date_range:
        _handoff_date_range(cfg, date_column, from_date, to_date, t0)
    else:
        _handoff_season(cfg, season_seq, archive_type, t0)


def _handoff_date_range(
    cfg, date_column: str, from_date: str, to_date: str, t0: float
) -> None:
    """Delete rows from practice_log_live within the specified date range."""
    if date_column not in _VALID_DATE_COLUMNS:
        _emit_error(
            f"Invalid date_column: {date_column}. "
            f"Must be one of {sorted(_VALID_DATE_COLUMNS)}"
        )

    try:
        with connect(cfg) as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM practice_log_live"
            ).fetchone()[0]
            conn.execute(
                f"DELETE FROM practice_log_live "
                f"WHERE CAST({date_column} AS DATE) >= CAST(? AS DATE) "
                f"AND CAST({date_column} AS DATE) <= CAST(? AS DATE)",
                [from_date, to_date],
            )
            after = conn.execute(
                "SELECT COUNT(*) FROM practice_log_live"
            ).fetchone()[0]
            rows_removed = before - after
    except Exception as exc:
        _emit_error(f"Date-range handoff failed: {exc}")
        return  # unreachable, satisfies type checker

    duration_ms = int((time.monotonic() - t0) * 1000)
    click.echo(
        json.dumps(
            {
                "status": "ok",
                "mode": "date_range",
                "rows_removed": rows_removed,
                "date_column": date_column,
                "from": from_date,
                "to": to_date,
                "duration_ms": duration_ms,
            }
        )
    )


def _handoff_season(cfg, season_seq: int, archive_type: str, t0: float) -> None:
    """Delete rows from memory_state_current for the specified season."""
    try:
        with connect(cfg) as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM memory_state_current"
            ).fetchone()[0]
            conn.execute(
                "DELETE FROM memory_state_current WHERE season_seq = ?",
                [season_seq],
            )
            after = conn.execute(
                "SELECT COUNT(*) FROM memory_state_current"
            ).fetchone()[0]
            rows_removed = before - after
    except Exception as exc:
        _emit_error(f"Season handoff failed: {exc}")
        return

    duration_ms = int((time.monotonic() - t0) * 1000)
    click.echo(
        json.dumps(
            {
                "status": "ok",
                "mode": "season",
                "season_seq": season_seq,
                "rows_removed": rows_removed,
                "duration_ms": duration_ms,
            }
        )
    )


def _emit_error(msg: str) -> None:
    """Emit a JSON error and exit."""
    click.echo(json.dumps({"status": "error", "error": msg}))
    sys.exit(1)
