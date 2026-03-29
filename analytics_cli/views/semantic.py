"""DuckDB semantic view definitions for the analytics lakehouse.

All archive views read directly from Hive-partitioned Parquet files.
Dimension views read from individual Parquet files.
Combined views UNION ALL archive + live layers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from analytics_cli.config import Config

log = logging.getLogger("memora-analytics")

# ── Archive view definitions ─────────────────────────────────────────────────
# Maps view name → glob pattern relative to lake_path

ARCHIVE_VIEWS: dict[str, str] = {
    "practice_log_archive": "practice_log/**/*.parquet",
    "interaction_log_archive": "interaction_log/**/*.parquet",
    "memory_state_archive": "memory_state/**/*.parquet",
    "task_run_log_archive": "task_run_log/**/*.parquet",
    "structure_progress_snapshots": "structure_progress/**/*.parquet",
}

# ── Dimension view definitions ───────────────────────────────────────────────

DIMENSION_VIEWS: list[str] = [
    "dim_player",
    "dim_player_history",
    "dim_season",
    "dim_plan",
    "dim_review_item",
    "dim_lesson",
]

# ── Live table DDL (CREATE IF NOT EXISTS) ────────────────────────────────────

_PRACTICE_LOG_LIVE_DDL = """\
CREATE TABLE IF NOT EXISTS practice_log_live (
    player_id    VARCHAR,
    item_id      VARCHAR,
    first_seen_at TIMESTAMP,
    last_seen_at  TIMESTAMP,
    last_result   VARCHAR,
    attempt_count INTEGER,
    correct_count INTEGER,
    season_id    VARCHAR,
    plan_id      VARCHAR,
    scope_type   VARCHAR,
    sync_batch_id VARCHAR,
    schema_version VARCHAR,
    synced_at    TIMESTAMP
)"""

_MEMORY_STATE_CURRENT_DDL = """\
CREATE TABLE IF NOT EXISTS memory_state_current (
    name         BIGINT,
    season_seq   INTEGER,
    subject      VARCHAR,
    player       VARCHAR,
    item_id      VARCHAR,
    stability    DOUBLE,
    difficulty   DOUBLE,
    next_review  TIMESTAMP,
    lesson       VARCHAR,
    state        TINYINT,
    step         TINYINT,
    last_review  TIMESTAMP,
    modified     TIMESTAMP
)"""


# ── Public API ───────────────────────────────────────────────────────────────


def create_archive_views(
    conn: duckdb.DuckDBPyConnection,
    lake_path: str,
) -> list[str]:
    """Create archive fact views over Hive-partitioned Parquet.

    Views referencing directories without Parquet files are silently skipped.
    """
    created: list[str] = []
    for view_name, glob_pattern in ARCHIVE_VIEWS.items():
        full_path = f"{lake_path}/{glob_pattern}"
        try:
            conn.execute(
                f"CREATE OR REPLACE VIEW {view_name} AS "
                f"SELECT * FROM read_parquet('{full_path}', "
                f"hive_partitioning = true, union_by_name = true)"
            )
            created.append(view_name)
        except duckdb.IOException:
            log.debug("No data for %s — skipping", view_name)
    return created


def create_dimension_views(
    conn: duckdb.DuckDBPyConnection,
    dimensions_path: str,
) -> list[str]:
    """Create dimension views from individual Parquet files.

    Dimensions without a corresponding file are silently skipped.
    """
    created: list[str] = []
    for dim_name in DIMENSION_VIEWS:
        pq_path = f"{dimensions_path}/{dim_name}.parquet"
        if not Path(pq_path).exists():
            log.debug("Skipping %s — %s not found", dim_name, pq_path)
            continue
        try:
            conn.execute(
                f"CREATE OR REPLACE VIEW {dim_name} AS "
                f"SELECT * FROM read_parquet('{pq_path}')"
            )
            created.append(dim_name)
        except duckdb.IOException:
            log.debug("Cannot read %s — skipping", pq_path)
    return created


def ensure_live_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create live/current DuckDB tables if they don't exist."""
    conn.execute(_PRACTICE_LOG_LIVE_DDL)
    conn.execute(_MEMORY_STATE_CURRENT_DDL)


def create_combined_views(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Create combined views (archive UNION ALL live).

    Only creates a combined view if both the archive view and live table exist.
    """
    created: list[str] = []

    if _relation_exists(conn, "practice_log_archive") and _relation_exists(
        conn, "practice_log_live"
    ):
        conn.execute("""\
CREATE OR REPLACE VIEW practice_log_combined AS
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'archive' AS source
FROM practice_log_archive
UNION ALL
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'live' AS source
FROM practice_log_live""")
        created.append("practice_log_combined")

    if _relation_exists(conn, "memory_state_archive") and _relation_exists(
        conn, "memory_state_current"
    ):
        conn.execute("""\
CREATE OR REPLACE VIEW memory_state_combined AS
SELECT name, season_seq, subject, player, item_id,
       stability, difficulty, next_review,
       lesson, state, step, last_review, modified,
       'archive' AS source
FROM memory_state_archive
UNION ALL
SELECT name, season_seq, subject, player, item_id,
       stability, difficulty, next_review,
       lesson, state, step, last_review, modified,
       'current' AS source
FROM memory_state_current""")
        created.append("memory_state_combined")

    return created


def refresh_all_views(
    conn: duckdb.DuckDBPyConnection,
    cfg: Config,
) -> list[str]:
    """Create or refresh all semantic views. Returns names of views created."""
    created: list[str] = []
    created.extend(create_archive_views(conn, cfg.lake_path))
    created.extend(create_dimension_views(conn, cfg.dimensions_path))
    ensure_live_tables(conn)
    created.extend(create_combined_views(conn))
    return created


# ── Helpers ──────────────────────────────────────────────────────────────────


def _relation_exists(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Return True if a table or view with *name* exists."""
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 0")
        return True
    except duckdb.CatalogException:
        return False
