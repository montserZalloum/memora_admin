"""Weekly structure progress snapshot pipeline.

Extracts structure progress rows joined with player profiles, writes a
Parquet partition per snapshot date, and builds a manifest. Designed for
weekly cron execution (typically on Sundays) but can be triggered manually
via the CLI: python -m archive_executor.snapshot --snapshot-date YYYY-MM-DD
"""

import hashlib
import json
import os
import datetime
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import pymysql
import yaml

from . import manifest as manifest_mod
from .db import get_connection

SNAPSHOT_SCHEMA = pa.schema([
    pa.field("snapshot_date", pa.date32(), nullable=False),
    pa.field("player_id", pa.utf8(), nullable=False),
    pa.field("plan_id", pa.utf8(), nullable=False),
    pa.field("subject_id", pa.utf8(), nullable=False),
    pa.field("completion_percentage", pa.float64(), nullable=False),
])

_DATASET_KEY = "structure_progress_snapshot"
_FACT_FILENAME = "fact_structure_progress.parquet"

_VALID_ROWS_SQL = """
SELECT
  %s AS snapshot_date,
  sp.`player`                AS player_id,
  pp.`plan`                  AS plan_id,
  sp.`subject`               AS subject_id,
  sp.`completion_percentage`
FROM `tabMemora Structure Progress` sp
INNER JOIN `tabMemora Player Profile` pp
  ON sp.`player` = pp.`name`
WHERE pp.`plan` IS NOT NULL
ORDER BY sp.`player`, sp.`subject`
"""

_REJECTED_ROWS_SQL = """
SELECT
  SUM(CASE WHEN pp.`name` IS NULL THEN 1 ELSE 0 END) AS no_profile,
  SUM(CASE WHEN pp.`name` IS NOT NULL AND pp.`plan` IS NULL THEN 1 ELSE 0 END) AS null_plan
FROM `tabMemora Structure Progress` sp
LEFT JOIN `tabMemora Player Profile` pp
  ON sp.`player` = pp.`name`
WHERE pp.`name` IS NULL OR pp.`plan` IS NULL
"""


def _extract_valid_rows(conn, snapshot_date: str):
    """Execute valid-row extraction SQL and return the streaming SSDictCursor."""
    cursor = conn.cursor(pymysql.cursors.SSDictCursor)
    cursor.execute(_VALID_ROWS_SQL, (snapshot_date,))
    return cursor


def _count_rejected_rows(conn) -> tuple[int, int]:
    """Return (no_profile_count, null_plan_count) for rows excluded from the snapshot."""
    with conn.cursor() as cursor:
        cursor.execute(_REJECTED_ROWS_SQL)
        row = cursor.fetchone()
    no_profile = int(row["no_profile"] or 0)
    null_plan = int(row["null_plan"] or 0)
    return no_profile, null_plan


_WRITE_BATCH_SIZE = 10_000


def _write_parquet(rows_iter, snapshot_date: str, staging_dir: str) -> tuple[str, int]:
    """Stream rows from cursor into a Parquet file; return (file_path, row_count).

    Writes an empty Parquet with the correct schema if no rows are available (FR-013).
    Rows are flushed to the ParquetWriter in record-batch increments of _WRITE_BATCH_SIZE
    so peak memory is bounded regardless of result-set size.
    """
    import datetime as _dt

    date_val = _dt.date.fromisoformat(snapshot_date)
    snapshot_days = (date_val - _dt.date(1970, 1, 1)).days  # days since epoch for date32

    os.makedirs(staging_dir, exist_ok=True)
    file_path = os.path.join(staging_dir, _FACT_FILENAME)
    row_count = 0

    buf_snapshot_dates: list = []
    buf_player_ids: list = []
    buf_plan_ids: list = []
    buf_subject_ids: list = []
    buf_completion_pcts: list = []

    def _flush(writer: pq.ParquetWriter) -> None:
        batch = pa.record_batch(
            {
                "snapshot_date": pa.array(buf_snapshot_dates, type=pa.date32()),
                "player_id": pa.array(buf_player_ids, type=pa.utf8()),
                "plan_id": pa.array(buf_plan_ids, type=pa.utf8()),
                "subject_id": pa.array(buf_subject_ids, type=pa.utf8()),
                "completion_percentage": pa.array(buf_completion_pcts, type=pa.float64()),
            },
            schema=SNAPSHOT_SCHEMA,
        )
        writer.write_batch(batch)
        buf_snapshot_dates.clear()
        buf_player_ids.clear()
        buf_plan_ids.clear()
        buf_subject_ids.clear()
        buf_completion_pcts.clear()

    with pq.ParquetWriter(file_path, schema=SNAPSHOT_SCHEMA, compression="snappy") as writer:
        for row in rows_iter:
            buf_snapshot_dates.append(snapshot_days)
            buf_player_ids.append(row["player_id"])
            buf_plan_ids.append(row["plan_id"])
            buf_subject_ids.append(row["subject_id"])
            buf_completion_pcts.append(
                float(row["completion_percentage"]) if row["completion_percentage"] is not None else None
            )
            row_count += 1
            if row_count % _WRITE_BATCH_SIZE == 0:
                _flush(writer)

        if buf_snapshot_dates:
            _flush(writer)

    return file_path, row_count


def _build_snapshot_manifest(staging_dir: str, snapshot_date: str, parquet_path: str, row_count: int) -> str:
    """Compute checksum + size of Parquet file and write manifest.json; return manifest path."""
    sha256 = hashlib.sha256()
    with open(parquet_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    checksum = "sha256:" + sha256.hexdigest()
    size_bytes = os.path.getsize(parquet_path)

    return manifest_mod.build_manifest(
        staging_dir=staging_dir,
        batch_id=f"SNAP-{snapshot_date}",
        dataset_key=_DATASET_KEY,
        kind="snapshot",
        schema_version="1.0",
        source="memora_admin",
        scope_key=snapshot_date,
        files=[
            {
                "role": "fact",
                "entity": "structure_progress",
                "filename": _FACT_FILENAME,
                "row_count": row_count,
                "checksum": checksum,
                "size_bytes": size_bytes,
            }
        ],
    )


def _atomic_swap(staging_dir: str, final_dir: str) -> bool:
    """Atomically replace final_dir with staging_dir.

    If final_dir already exists, renames it to final_dir.old before swapping,
    then removes the .old directory. Cleans up any stale .old directory from a
    previous crashed swap before starting.

    Returns:
        True if an existing snapshot was overwritten, False for a new partition.
    """
    old_dir = final_dir + ".old"

    # Recover from a previous crashed swap: if .old exists but final_dir
    # does not, the crash happened after moving final_dir aside but before
    # the new staging_dir took its place.  Restore .old so we don't lose
    # the only good snapshot.
    if os.path.isdir(old_dir):
        if not os.path.isdir(final_dir):
            os.rename(old_dir, final_dir)
        else:
            shutil.rmtree(old_dir)

    overwriting = os.path.isdir(final_dir)
    if overwriting:
        os.rename(final_dir, old_dir)

    os.rename(staging_dir, final_dir)

    if os.path.isdir(old_dir):
        shutil.rmtree(old_dir)

    return overwriting


_SNAPSHOT_SCHEMA_REL = os.path.join("snapshot_types", "structure_progress.v1.yaml")


def _run_dq_validation(config, parquet_path: str) -> dict:
    """Load DQ rules from the snapshot schema YAML and validate the Parquet file.

    Logs a WARNING for each violated rule. Returns the full validation result dict
    so callers can inspect or surface it in the summary.
    """
    from .validator import validate_fact_quality_generic

    schema_yaml_path = os.path.join(config.schema_registry_path, _SNAPSHOT_SCHEMA_REL)
    with open(schema_yaml_path, "r") as f:
        schema_def = yaml.safe_load(f)

    dq_rules = schema_def.get("dq_rules", [])
    return validate_fact_quality_generic(parquet_path, dq_rules)


def run_snapshot(config, snapshot_date: str | None = None) -> dict:
    """Orchestrate the weekly structure progress snapshot pipeline.

    Steps:
      1. Resolve snapshot_date to the most recent Sunday if not provided.
      2. Create staging directory, cleaning up any stale staging from a previous crash.
      3. Extract valid rows from DB and write Parquet.
      4. Count rejected rows and log warnings.
      5. Build manifest.
      6. Atomic swap staging → final directory.

    Returns:
        dict with keys: snapshot_date, row_count, rejected_no_profile, rejected_null_plan.
    """
    from .db import get_connection
    from .logger import StructuredLogger
    from .transfer import transfer_batch, verify_remote_checksums

    logger = StructuredLogger(log_path=config.log_path)

    if snapshot_date is None:
        today = datetime.date.today()
        # weekday(): Monday=0 … Sunday=6; days to subtract to reach Sunday
        days_since_sunday = (today.weekday() + 1) % 7
        snapshot_date = (today - datetime.timedelta(days=days_since_sunday)).isoformat()

    staging_dir = os.path.join(
        config.snapshot_output_path, "structure_progress", ".staging", snapshot_date
    )
    final_dir = os.path.join(
        config.snapshot_output_path, "structure_progress", snapshot_date
    )

    # Clean up stale staging from a previous crashed run
    if os.path.isdir(staging_dir):
        shutil.rmtree(staging_dir)
    os.makedirs(staging_dir, exist_ok=True)

    dq_result: dict = {"passed": True, "results": [], "warnings": []}
    conn = get_connection(config)
    try:
        cursor = _extract_valid_rows(conn, snapshot_date)
        parquet_path, row_count = _write_parquet(cursor, snapshot_date, staging_dir)
        cursor.close()

        no_profile, null_plan = _count_rejected_rows(conn)
        if no_profile > 0 or null_plan > 0:
            logger.warning(
                "snapshot_rejected_rows",
                snapshot_date=snapshot_date,
                rejected_no_profile=no_profile,
                rejected_null_plan=null_plan,
                total_rejected=no_profile + null_plan,
            )

        dq_result = _run_dq_validation(config, parquet_path)
        if not dq_result["passed"]:
            failed_rules = [r for r in dq_result["results"] if not r["passed"]]
            for rule in failed_rules:
                logger.warning(
                    "snapshot_dq_violation",
                    snapshot_date=snapshot_date,
                    rule=rule["rule"],
                    detail=rule["detail"],
                )
            # Abort: do not publish a snapshot that fails DQ validation
            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir)
            raise RuntimeError(
                f"Snapshot DQ validation failed for {snapshot_date}: "
                f"{len(failed_rules)} rule(s) violated"
            )
        _build_snapshot_manifest(staging_dir, snapshot_date, parquet_path, row_count)
        overwritten = _atomic_swap(staging_dir, final_dir)
        if overwritten:
            logger.warning(
                "snapshot_overwrite",
                snapshot_date=snapshot_date,
                final_dir=final_dir,
            )
    finally:
        conn.close()

    # Transfer to analytics server if SSH is configured
    remote_path = None
    if config.has_ssh_config() and config.remote_snapshot_path:
        batch_id = f"SNAP-{snapshot_date}"
        remote_path = transfer_batch(
            config=config,
            local_dir=final_dir,
            remote_base_path=config.remote_snapshot_path,
            job_name=batch_id,
            log=logger,
        )

        manifest_path = os.path.join(final_dir, "manifest.json")
        with open(manifest_path, "r") as f:
            manifest_data = json.load(f)

        verification = verify_remote_checksums(
            config=config,
            remote_path=remote_path,
            manifest=manifest_data,
            log=logger,
        )
        if not verification["valid"]:
            logger.warning(
                "snapshot_transfer_checksum_failed",
                snapshot_date=snapshot_date,
                errors=verification["errors"],
            )
            raise RuntimeError(
                f"Snapshot transfer checksum verification failed for {snapshot_date}: "
                f"{verification['errors']}"
            )
    elif not config.has_ssh_config():
        logger.warning(
            "snapshot_transfer_skipped",
            snapshot_date=snapshot_date,
            reason="SSH not configured",
        )
    elif not config.remote_snapshot_path:
        logger.warning(
            "snapshot_transfer_skipped",
            snapshot_date=snapshot_date,
            reason="REMOTE_SNAPSHOT_PATH not set",
        )

    logger.info(
        "snapshot_complete",
        snapshot_date=snapshot_date,
        row_count=row_count,
        rejected_no_profile=no_profile,
        rejected_null_plan=null_plan,
        remote_path=remote_path,
    )

    return {
        "snapshot_date": snapshot_date,
        "row_count": row_count,
        "rejected_no_profile": no_profile,
        "rejected_null_plan": null_plan,
        "dq_passed": dq_result["passed"],
        "remote_path": remote_path,
    }


if __name__ == "__main__":
    import argparse
    import sys

    from archive_executor.config import Config
    from archive_executor.logger import StructuredLogger

    parser = argparse.ArgumentParser(
        description="Generate a weekly structure progress snapshot."
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="Snapshot date in YYYY-MM-DD format (default: most recent Sunday).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and validate but skip the atomic swap step.",
    )
    args = parser.parse_args()

    cfg = Config.from_env()
    logger = StructuredLogger(log_path=cfg.log_path)

    if args.dry_run:
        import tempfile

        snapshot_date = args.snapshot_date
        if snapshot_date is None:
            today = datetime.date.today()
            days_since_sunday = (today.weekday() + 1) % 7
            snapshot_date = (today - datetime.timedelta(days=days_since_sunday)).isoformat()

        from archive_executor.db import get_connection

        with tempfile.TemporaryDirectory(prefix="memora_snap_dryrun_") as staging_dir:
            conn = get_connection(cfg)
            try:
                cursor = _extract_valid_rows(conn, snapshot_date)
                parquet_path, row_count = _write_parquet(cursor, snapshot_date, staging_dir)
                cursor.close()
                no_profile, null_plan = _count_rejected_rows(conn)
            finally:
                conn.close()

            dq_result = _run_dq_validation(cfg, parquet_path)
            dq_passed = dq_result["passed"]
            if not dq_passed:
                failed_rules = [r for r in dq_result["results"] if not r["passed"]]
                for rule in failed_rules:
                    logger.warning(
                        "snapshot_dq_violation",
                        snapshot_date=snapshot_date,
                        rule=rule["rule"],
                        detail=rule["detail"],
                    )

        logger.info(
            "snapshot_dry_run_complete",
            snapshot_date=snapshot_date,
            row_count=row_count,
            rejected_no_profile=no_profile,
            rejected_null_plan=null_plan,
            dq_passed=dq_passed,
        )
        print(f"DRY RUN: snapshot_date={snapshot_date}, row_count={row_count}, "
              f"rejected_no_profile={no_profile}, rejected_null_plan={null_plan}, "
              f"dq_passed={dq_passed}")
        sys.exit(0)

    result = run_snapshot(cfg, snapshot_date=args.snapshot_date)
    print(
        f"Snapshot complete: date={result['snapshot_date']}, rows={result['row_count']}, "
        f"rejected_no_profile={result['rejected_no_profile']}, "
        f"rejected_null_plan={result['rejected_null_plan']}"
    )
