"""Orchestrate all data lake health checks and report results.

Runs duplicate detection, checksum verification, dimension coverage,
and partition size analysis, then emits a single JSON summary.
"""

from __future__ import annotations

import json
import logging
import sys
import time

import click

from analytics_cli.db import connect
from analytics_cli.health.checksum_check import check_checksums
from analytics_cli.health.dimension_coverage import check_dimension_coverage
from analytics_cli.health.duplicate_check import check_duplicates
from analytics_cli.health.partition_analysis import check_partition_sizes

log = logging.getLogger("memora-analytics")


@click.command("verify")
@click.pass_obj
def verify(cfg) -> None:
    """Run all data lake health checks and emit a JSON summary."""
    t0 = time.monotonic()

    checks: dict = {}

    try:
        with connect(cfg) as conn:
            checks["duplicates"] = check_duplicates(conn)
            checks["dimension_coverage"] = check_dimension_coverage(conn)
    except Exception as exc:
        _emit_error(f"verify failed during DB checks: {exc}")
        return

    try:
        checks["checksums"] = check_checksums(cfg.manifests_path)
    except Exception as exc:
        _emit_error(f"verify failed during checksum check: {exc}")
        return

    try:
        checks["partition_sizes"] = check_partition_sizes(cfg.lake_path)
    except Exception as exc:
        _emit_error(f"verify failed during partition analysis: {exc}")
        return

    # Determine overall status
    all_statuses = [c["status"] for c in checks.values()]
    if any(s == "fail" for s in all_statuses):
        overall = "error"
    elif any(s == "warning" for s in all_statuses):
        overall = "warning"
    else:
        overall = "ok"

    duration_ms = int((time.monotonic() - t0) * 1000)
    click.echo(
        json.dumps({
            "status": overall,
            "checks": checks,
            "duration_ms": duration_ms,
        })
    )


def _emit_error(msg: str) -> None:
    """Emit a JSON error and exit."""
    click.echo(json.dumps({"status": "error", "error": msg}))
    sys.exit(1)
