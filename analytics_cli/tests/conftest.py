"""Shared test fixtures for analytics_cli tests.

Provides:
- DuckDB in-memory connection fixture
- Temporary Hive-partitioned directory builder
- Sample Parquet file generators for practice_log, memory_state,
  interaction_log, task_run_log, and structure_progress schemas
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from analytics_cli.config import Config


# ---------------------------------------------------------------------------
# DuckDB in-memory connection
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> duckdb.DuckDBPyConnection:
    """Yield a fresh in-memory DuckDB connection per test."""
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Config fixture wired to tmp directories
# ---------------------------------------------------------------------------


@pytest.fixture()
def lake_dir(tmp_path: Path) -> Path:
    """Create and return a temporary lake root directory."""
    d = tmp_path / "lake"
    d.mkdir()
    return d


@pytest.fixture()
def dimensions_dir(tmp_path: Path) -> Path:
    """Create and return a temporary dimensions directory."""
    d = tmp_path / "dimensions"
    d.mkdir()
    return d


@pytest.fixture()
def manifests_dir(tmp_path: Path) -> Path:
    """Create and return a temporary manifests directory."""
    d = tmp_path / "manifests"
    d.mkdir()
    (d / "archive").mkdir()
    return d


@pytest.fixture()
def duckdb_path(tmp_path: Path) -> str:
    """Return path for a file-backed DuckDB (not created yet)."""
    return str(tmp_path / "test.duckdb")


@pytest.fixture()
def cfg(duckdb_path: str, lake_dir: Path, dimensions_dir: Path, manifests_dir: Path) -> Config:
    """Return a Config pointing at temporary directories."""
    return Config(
        duckdb_path=duckdb_path,
        lake_path=str(lake_dir),
        dimensions_path=str(dimensions_dir),
        manifests_path=str(manifests_dir),
    )


# ---------------------------------------------------------------------------
# Hive-partitioned directory builder
# ---------------------------------------------------------------------------


def _ensure_parents(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build_hive_dir(
    lake_dir: Path,
    dataset: str,
    partitions: dict[str, str],
    filename: str = "part-0000.parquet",
) -> Path:
    """Create a Hive-partitioned directory structure and return the file path.

    Example::

        path = build_hive_dir(lake, "practice_log",
                              {"year": "2025", "month": "06", "day": "15"})
        # => lake/practice_log/year=2025/month=06/day=15/part-0000.parquet
    """
    parts = "/".join(f"{k}={v}" for k, v in partitions.items())
    dest = lake_dir / dataset / parts / filename
    _ensure_parents(dest)
    return dest


# ---------------------------------------------------------------------------
# Sample Parquet generators
# ---------------------------------------------------------------------------

_PRACTICE_LOG_SCHEMA = pa.schema([
    ("player_id", pa.string()),
    ("item_id", pa.string()),
    ("first_seen_at", pa.timestamp("us")),
    ("last_seen_at", pa.timestamp("us")),
    ("last_result", pa.string()),
    ("attempt_count", pa.int32()),
    ("correct_count", pa.int32()),
    ("season_id", pa.string()),
    ("plan_id", pa.string()),
])

_INTERACTION_LOG_SCHEMA = pa.schema([
    ("name", pa.string()),
    ("player", pa.string()),
    ("lesson", pa.string()),
    ("stage_id", pa.string()),
    ("item_id", pa.string()),
    ("event_type", pa.string()),
    ("time_spent", pa.float64()),
    ("errors_count", pa.int32()),
    ("timestamp", pa.timestamp("us")),
    ("season_id", pa.string()),
    ("plan_id", pa.string()),
])

_MEMORY_STATE_SCHEMA = pa.schema([
    ("name", pa.int64()),
    ("season_seq", pa.int32()),
    ("subject", pa.string()),
    ("player", pa.string()),
    ("item_id", pa.string()),
    ("stage_id", pa.string()),
    ("stability", pa.float64()),
    ("difficulty", pa.float64()),
    ("next_review", pa.timestamp("us")),
    ("lesson", pa.string()),
    ("state", pa.int8()),
    ("step", pa.int8()),
    ("last_review", pa.timestamp("us")),
    ("modified", pa.timestamp("us")),
])

_TASK_RUN_LOG_SCHEMA = pa.schema([
    ("name", pa.string()),
    ("task_name", pa.string()),
    ("run_date", pa.date32()),
    ("started_at", pa.timestamp("us")),
    ("completed_at", pa.timestamp("us")),
    ("duration_sec", pa.float64()),
    ("status", pa.string()),
    ("triggered_by", pa.string()),
    ("processed_count", pa.int32()),
    ("failed_count", pa.int32()),
    ("error_message", pa.string()),
])

_STRUCTURE_PROGRESS_SCHEMA = pa.schema([
    ("snapshot_date", pa.date32()),
    ("player_id", pa.string()),
    ("plan_id", pa.string()),
    ("subject_id", pa.string()),
    ("completion_percentage", pa.float64()),
])


def write_practice_log_parquet(
    path: Path | str,
    rows: list[dict] | None = None,
    n: int = 5,
) -> Path:
    """Write a sample practice_log Parquet file.

    If *rows* is ``None``, generates *n* synthetic rows.
    """
    path = Path(path)
    if rows is None:
        rows = [
            {
                "player_id": f"PLR-{i:04d}",
                "item_id": f"ITEM-{i:04d}",
                "first_seen_at": datetime(2025, 6, 1, 10, 0, 0),
                "last_seen_at": datetime(2025, 6, 15, 14, 30, 0),
                "last_result": "correct",
                "attempt_count": i + 1,
                "correct_count": i,
                "season_id": "SEASON-001",
                "plan_id": "PLAN-001",
            }
            for i in range(n)
        ]
    table = pa.Table.from_pylist(rows, schema=_PRACTICE_LOG_SCHEMA)
    pq.write_table(table, str(path))
    return path


def write_interaction_log_parquet(
    path: Path | str,
    rows: list[dict] | None = None,
    n: int = 5,
) -> Path:
    """Write a sample interaction_log Parquet file."""
    path = Path(path)
    if rows is None:
        rows = [
            {
                "name": f"ILOG-{i:06d}",
                "player": f"PLR-{i:04d}",
                "lesson": f"LESSON-{i:03d}",
                "stage_id": f"STAGE-{i:02d}",
                "item_id": f"ITEM-{i:04d}",
                "event_type": "answer",
                "time_spent": 12.5 + i,
                "errors_count": i % 3,
                "timestamp": datetime(2025, 6, 15, 10, i, 0),
                "season_id": "SEASON-001",
                "plan_id": "PLAN-001",
            }
            for i in range(n)
        ]
    table = pa.Table.from_pylist(rows, schema=_INTERACTION_LOG_SCHEMA)
    pq.write_table(table, str(path))
    return path


def write_memory_state_parquet(
    path: Path | str,
    rows: list[dict] | None = None,
    n: int = 5,
    season_seq: int = 1,
) -> Path:
    """Write a sample memory_state Parquet file."""
    path = Path(path)
    if rows is None:
        rows = [
            {
                "name": 1000 + i,
                "season_seq": season_seq,
                "subject": "math",
                "player": f"PLR-{i:04d}",
                "item_id": f"ITEM-{i:04d}",
                "stage_id": "STAGE-01",
                "stability": 2.5 + i * 0.1,
                "difficulty": 0.3 + i * 0.05,
                "next_review": datetime(2025, 7, 1, 10, 0, 0),
                "lesson": "LESSON-001",
                "state": 2,
                "step": 1,
                "last_review": datetime(2025, 6, 15, 10, 0, 0),
                "modified": datetime(2025, 6, 15, 14, 30, 0),
            }
            for i in range(n)
        ]
    table = pa.Table.from_pylist(rows, schema=_MEMORY_STATE_SCHEMA)
    pq.write_table(table, str(path))
    return path


def write_task_run_log_parquet(
    path: Path | str,
    rows: list[dict] | None = None,
    n: int = 5,
) -> Path:
    """Write a sample task_run_log Parquet file."""
    path = Path(path)
    if rows is None:
        rows = [
            {
                "name": f"TLOG-{i:06d}",
                "task_name": f"task_{i}",
                "run_date": date(2025, 6, 15),
                "started_at": datetime(2025, 6, 15, 2, 0, 0),
                "completed_at": datetime(2025, 6, 15, 2, 5, 0),
                "duration_sec": 300.0 + i,
                "status": "Success",
                "triggered_by": "scheduler",
                "processed_count": 100 + i,
                "failed_count": 0,
                "error_message": None,
            }
            for i in range(n)
        ]
    table = pa.Table.from_pylist(rows, schema=_TASK_RUN_LOG_SCHEMA)
    pq.write_table(table, str(path))
    return path


def write_structure_progress_parquet(
    path: Path | str,
    rows: list[dict] | None = None,
    n: int = 5,
    snapshot_date: date | None = None,
) -> Path:
    """Write a sample structure_progress Parquet file."""
    path = Path(path)
    if snapshot_date is None:
        snapshot_date = date(2025, 6, 15)
    if rows is None:
        rows = [
            {
                "snapshot_date": snapshot_date,
                "player_id": f"PLR-{i:04d}",
                "plan_id": "PLAN-001",
                "subject_id": f"SUBJ-{i:02d}",
                "completion_percentage": 50.0 + i * 10,
            }
            for i in range(n)
        ]
    table = pa.Table.from_pylist(rows, schema=_STRUCTURE_PROGRESS_SCHEMA)
    pq.write_table(table, str(path))
    return path


def write_dimension_parquet(
    path: Path | str,
    name: str,
    rows: list[dict],
    schema: pa.Schema | None = None,
) -> Path:
    """Write a dimension Parquet file (arbitrary schema)."""
    path = Path(path)
    if schema is not None:
        table = pa.Table.from_pylist(rows, schema=schema)
    else:
        table = pa.Table.from_pylist(rows)
    pq.write_table(table, str(path))
    return path
