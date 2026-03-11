"""Archive job scheduler.

Creates daily Pending archive jobs for records older than the retention window.

Usage:
    python -m archive_executor.scheduler --archive-type interaction_log --retention-days 14

Cron:
    30 1 * * * /opt/memora-archive/venv/bin/python -m archive_executor.scheduler \
                --archive-type interaction_log --retention-days 14
"""

import argparse
import json
import sys
from datetime import date, timedelta

from .config import Config
from .db import get_connection
from .schemas import load_archive_type


def _next_job_name(conn) -> str:
    """Generate the next ARCH-XXXXX job name by querying the max existing."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM `tabMemora Archive Job` "
            "WHERE name REGEXP '^ARCH-[0-9]+$' "
            "ORDER BY CAST(SUBSTRING(name, 6) AS UNSIGNED) DESC "
            "LIMIT 1"
        )
        row = cursor.fetchone()
    next_num = (int(row["name"].split("-", 1)[1]) + 1) if row else 1
    return f"ARCH-{next_num:05d}"


def _build_job_meta(archive_schema: dict, date_from: str, date_to: str) -> str:
    """Build job_meta JSON from YAML archive schema for a specific date window."""
    scope_column = archive_schema.get("scope_column", "timestamp")

    related_tables = []
    for dim in archive_schema.get("dimensions", []):
        entry = {"entity": dim["entity"], "schema_version": dim["schema_version"]}
        if dim.get("scope_source") == "derived":
            entry["scope_source"] = "derived"
        else:
            entry["fact_column"] = dim.get("join_column", dim["entity"])
        related_tables.append(entry)

    meta = {
        "query_filter": {
            "date_from": date_from,
            "date_to": date_to,
            "filter_column": scope_column,
        },
        "export_columns": archive_schema.get("fact_columns", []),
        "schema_snapshot": archive_schema.get("schema_snapshot", {}),
        "related_tables": related_tables,
        "fact_sql": archive_schema.get("fact_sql", {}),
        "scope_column": scope_column,
    }
    return json.dumps(meta)


def _job_exists(conn, source_doctype: str, archive_scope: str, schema_version: str) -> bool:
    """Return True if a non-Failed job exists for the given (doctype, scope, version)."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM `tabMemora Archive Job` "
            "WHERE source_doctype = %s AND archive_scope = %s AND schema_version = %s "
            "  AND status NOT IN ('Failed') "
            "LIMIT 1",
            (source_doctype, archive_scope, schema_version),
        )
        return cursor.fetchone() is not None


def _insert_pending_job(
    conn,
    name: str,
    source_doctype: str,
    archive_type: str,
    archive_scope: str,
    schema_version: str,
    job_meta: str,
) -> None:
    """Insert a new Pending archive job."""
    sql = (
        "INSERT INTO `tabMemora Archive Job` "
        "(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
        " `source_doctype`, `archive_scope`, `schema_version`, `archive_type`, "
        " `status`, `priority`, `retry_count`, `post_archive_action`, "
        " `source_deleted`, `sync_paused`, "
        " `duration_seconds`, `row_count`, `file_size_bytes`, "
        " `job_meta`) "
        "VALUES (%s, NOW(), NOW(), 'scheduler', 'scheduler', 0, 0, "
        "        %s, %s, %s, %s, "
        "        'Pending', 'Normal', 0, 'Delete', "
        "        0, 0, 0, 0, 0, "
        "        %s)"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (name, source_doctype, archive_scope, schema_version, archive_type, job_meta))
    conn.commit()


def create_pending_jobs(
    config: Config,
    archive_type: str,
    retention_days: int,
) -> list[str]:
    """Create daily Pending archive jobs for records older than retention_days.

    1. Loads archive type schema from YAML registry.
    2. Queries source table for MIN of the scope column.
    3. Computes archive window: [min_date, today - retention_days).
    4. For each day in the window, creates a Pending job unless a non-Failed
       job already exists for that (source_doctype, date, schema_version).

    Args:
        config: Executor configuration.
        archive_type: Archive type key (e.g., 'interaction_log').
        retention_days: Days to retain in production.

    Returns:
        List of created job names.
    """
    archive_schema = load_archive_type(config.schema_registry_path, archive_type, "v1")

    source_table = archive_schema["source_table"]
    source_doctype = source_table.removeprefix("tab")
    schema_version = "v1"
    scope_column = archive_schema.get("scope_column", "timestamp")

    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT MIN(`{scope_column}`) AS min_ts FROM `{source_table}`"
            )
            row = cursor.fetchone()

        if not row or row["min_ts"] is None:
            return []

        min_ts = row["min_ts"]
        min_date = min_ts.date() if hasattr(min_ts, "date") else date.fromisoformat(str(min_ts)[:10])

        cutoff_date = date.today() - timedelta(days=retention_days)
        if min_date >= cutoff_date:
            return []

        created_names = []
        current_date = min_date

        while current_date < cutoff_date:
            next_date = current_date + timedelta(days=1)
            date_str = current_date.isoformat()
            next_date_str = next_date.isoformat()

            if _job_exists(conn, source_doctype, date_str, schema_version):
                current_date = next_date
                continue

            job_name = _next_job_name(conn)
            job_meta = _build_job_meta(archive_schema, date_str, next_date_str)
            _insert_pending_job(
                conn=conn,
                name=job_name,
                source_doctype=source_doctype,
                archive_type=archive_type,
                archive_scope=date_str,
                schema_version=schema_version,
                job_meta=job_meta,
            )
            created_names.append(job_name)
            current_date = next_date

        return created_names
    finally:
        conn.close()


def _build_season_job_meta(archive_schema: dict, season_seq: int, season_name: str) -> str:
    """Build job_meta JSON from YAML archive schema for a season-scoped job."""
    scope_column = archive_schema.get("scope_column", "season_seq")

    related_tables = []
    for dim in archive_schema.get("dimensions", []):
        entry = {"entity": dim["entity"], "schema_version": dim["schema_version"]}
        if dim.get("scope_source") == "derived":
            entry["scope_source"] = "derived"
        else:
            entry["fact_column"] = dim.get("join_column", dim["entity"])
        related_tables.append(entry)

    meta = {
        "query_filter": {
            "season_seq": season_seq,
            "season_name": season_name,
            "filter_column": scope_column,
            "filter_type": "season",
        },
        "export_columns": archive_schema.get("fact_columns", []),
        "schema_snapshot": archive_schema.get("schema_snapshot", {}),
        "related_tables": related_tables,
        "fact_sql": archive_schema.get("fact_sql", {}),
        "scope_column": scope_column,
    }
    return json.dumps(meta)


def create_season_archive_jobs(
    config: Config,
    archive_type: str = "memory_state",
) -> list[dict]:
    """Create Pending archive jobs for ended seasons without existing jobs.

    Queries `tabMemora Season` for seasons where end_date < CURDATE() and no
    non-Failed archive job exists for that season.

    Args:
        config: Executor configuration.
        archive_type: Archive type key (default 'memory_state').

    Returns:
        List of dicts with season_name, season_seq, end_date, and job_name.
    """
    archive_schema = load_archive_type(config.schema_registry_path, archive_type, "v1")
    source_table = archive_schema["source_table"]
    source_doctype = source_table.removeprefix("tab")
    schema_version = "v1"

    conn = get_connection(config)
    try:
        # Find ended seasons with no existing non-Failed archive job
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT s.name AS season_name, s.season_seq, s.end_date "
                "FROM `tabMemora Season` s "
                "WHERE s.end_date < CURDATE() "
                "  AND NOT EXISTS ( "
                "    SELECT 1 FROM `tabMemora Archive Job` aj "
                "    WHERE aj.source_doctype = %s "
                "      AND aj.archive_scope = CONCAT('season_', s.season_seq) "
                "      AND aj.schema_version = %s "
                "      AND aj.status NOT IN ('Failed') "
                "  ) "
                "ORDER BY s.season_seq",
                (source_doctype, schema_version),
            )
            eligible_seasons = cursor.fetchall()

        if not eligible_seasons:
            return []

        created = []
        for season in eligible_seasons:
            season_seq = season["season_seq"]
            season_name = season["season_name"]
            end_date = season["end_date"]
            archive_scope = f"season_{season_seq}"

            job_name = _next_job_name(conn)
            job_meta = _build_season_job_meta(archive_schema, season_seq, season_name)
            _insert_pending_job(
                conn=conn,
                name=job_name,
                source_doctype=source_doctype,
                archive_type=archive_type,
                archive_scope=archive_scope,
                schema_version=schema_version,
                job_meta=job_meta,
            )
            created.append({
                "season_name": season_name,
                "season_seq": season_seq,
                "end_date": str(end_date),
                "job_name": job_name,
            })

        return created
    finally:
        conn.close()


def main():
    """CLI entry point for the archive job scheduler."""
    parser = argparse.ArgumentParser(description="Create pending archive jobs")
    parser.add_argument("--archive-type", required=True, help="Archive type (e.g., interaction_log, memory_state)")
    parser.add_argument("--mode", choices=["date", "season"], default="date",
                        help="Scheduling mode: 'date' for date-range (default), 'season' for season-based")
    parser.add_argument("--retention-days", type=int, default=14,
                        help="Days to retain in production (required for date mode, ignored for season mode)")
    args = parser.parse_args()

    config = Config.from_env()

    if args.mode == "season":
        created = create_season_archive_jobs(config, args.archive_type)
        result = {
            "archive_type": args.archive_type,
            "mode": "season",
            "jobs_created": len(created),
            "eligible_seasons": created,
            "job_names": [c["job_name"] for c in created],
        }
    else:
        created = create_pending_jobs(config, args.archive_type, args.retention_days)
        result = {
            "archive_type": args.archive_type,
            "mode": "date",
            "retention_days": args.retention_days,
            "jobs_created": len(created),
            "job_names": created,
        }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
