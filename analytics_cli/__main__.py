"""Memora Analytics CLI entry point.

All commands write JSON to stdout and logs to stderr.
Exit code 0 = success, non-zero = failure.

Usage::

    memora-analytics --help
    memora-analytics --duckdb-path /data/analytics.duckdb ingest-archive --batch-dir /tmp/batch
    python -m analytics_cli --help
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import click

from analytics_cli.config import Config

# ---------------------------------------------------------------------------
# Logging — always to stderr so stdout stays clean JSON
# ---------------------------------------------------------------------------

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("memora-analytics")

# ---------------------------------------------------------------------------
# Pass-through context object
# ---------------------------------------------------------------------------

pass_cfg = click.make_pass_decorator(Config, ensure=True)


def emit_json(data: dict[str, Any]) -> None:
    """Write *data* as a single JSON line to stdout."""
    click.echo(json.dumps(data))


def emit_error(message: str, **extra: Any) -> None:
    """Write an error JSON response and exit with code 1."""
    payload: dict[str, Any] = {"status": "error", "error": message}
    payload.update(extra)
    emit_json(payload)
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--duckdb-path",
    envvar="DUCKDB_PATH",
    default=None,
    help="Path to DuckDB database file. [env: DUCKDB_PATH]",
)
@click.option(
    "--lake-path",
    envvar="LAKE_PATH",
    default=None,
    help="Root path for Hive-partitioned lake directories. [env: LAKE_PATH]",
)
@click.option(
    "--dimensions-path",
    envvar="DIMENSIONS_PATH",
    default=None,
    help="Path to dimension Parquet files. [env: DIMENSIONS_PATH]",
)
@click.option(
    "--manifests-path",
    envvar="MANIFESTS_PATH",
    default=None,
    help="Path to manifest JSON files. [env: MANIFESTS_PATH]",
)
@click.pass_context
def cli(
    ctx: click.Context,
    duckdb_path: str | None,
    lake_path: str | None,
    dimensions_path: str | None,
    manifests_path: str | None,
) -> None:
    """Memora Analytics — DuckDB-based data lake management CLI."""
    ctx.ensure_object(dict)
    ctx.obj = Config.load(
        duckdb_path=duckdb_path,
        lake_path=lake_path,
        dimensions_path=dimensions_path,
        manifests_path=manifests_path,
    )


# ---------------------------------------------------------------------------
# Subcommand registration
# ---------------------------------------------------------------------------

from analytics_cli.commands.ingest_archive import ingest_archive  # noqa: E402
from analytics_cli.commands.ingest_live import ingest_live  # noqa: E402
from analytics_cli.commands.handoff import handoff  # noqa: E402
from analytics_cli.commands.refresh_recent import refresh_recent  # noqa: E402
from analytics_cli.commands.refresh_aggregates import refresh_aggregates  # noqa: E402
from analytics_cli.commands.mirror_status import mirror_status  # noqa: E402
from analytics_cli.commands.verify import verify  # noqa: E402
from analytics_cli.commands.compact import compact  # noqa: E402

cli.add_command(ingest_archive)
cli.add_command(ingest_live)
cli.add_command(handoff)
cli.add_command(refresh_recent)
cli.add_command(refresh_aggregates)
cli.add_command(mirror_status)
cli.add_command(verify)
cli.add_command(compact)


if __name__ == "__main__":
    cli()
