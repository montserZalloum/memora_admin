"""Ingest live snapshot Parquet into DuckDB live table via atomic staging-swap.

Reads Parquet files from a batch directory, loads them into a staging table,
then atomically swaps to the practice_log_live table.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import click
import pyarrow.parquet as pq

from analytics_cli.db import connect

log = logging.getLogger("memora-analytics")


@click.command("ingest-live")
@click.option(
    "--batch-dir",
    required=True,
    help="Path to transferred live snapshot directory containing Parquet files.",
)
@click.pass_obj
def ingest_live(cfg, batch_dir: str) -> None:
    """Load live snapshot Parquet into DuckDB live table via atomic staging-swap."""
    t0 = time.monotonic()
    batch_path = Path(batch_dir)

    if not batch_path.is_dir():
        _emit_error(f"batch-dir does not exist: {batch_dir}")

    parquet_files = sorted(batch_path.glob("*.parquet"))
    if not parquet_files:
        _emit_error(f"No Parquet files found in {batch_dir}")

    batches: list[dict] = []
    ok_count = 0
    err_count = 0

    # Collect per-file stats
    for pf in parquet_files:
        try:
            meta = pq.read_metadata(str(pf))
            batches.append({"file": pf.name, "rows": meta.num_rows, "status": "ok"})
            ok_count += 1
        except Exception as exc:
            log.error("Cannot read %s: %s", pf.name, exc)
            batches.append(
                {"file": pf.name, "rows": 0, "status": "error", "error": str(exc)}
            )
            err_count += 1

    # Atomic load: staging table → swap
    if ok_count > 0:
        try:
            glob_pattern = str(batch_path / "*.parquet").replace("\\", "/")
            with connect(cfg) as conn:
                conn.execute(
                    f"CREATE TABLE _staging_practice_log AS "
                    f"SELECT * FROM read_parquet('{glob_pattern}', "
                    f"union_by_name = true)"
                )
                conn.execute(
                    "CREATE OR REPLACE TABLE practice_log_live AS "
                    "SELECT * FROM _staging_practice_log"
                )
                conn.execute("DROP TABLE IF EXISTS _staging_practice_log")
        except Exception as exc:
            log.error("Staging swap failed: %s", exc)
            _emit_error(f"Staging swap failed: {exc}")

    duration_ms = int((time.monotonic() - t0) * 1000)
    click.echo(
        json.dumps(
            {
                "status": "ok" if err_count == 0 else "error",
                "batches_ok": ok_count,
                "batches_error": err_count,
                "batches": batches,
                "duration_ms": duration_ms,
            }
        )
    )

    if err_count > 0:
        sys.exit(1)


def _emit_error(msg: str) -> None:
    """Emit a JSON error and exit."""
    click.echo(
        json.dumps(
            {"status": "error", "error": msg, "batches_ok": 0, "batches_error": 0}
        )
    )
    sys.exit(1)
