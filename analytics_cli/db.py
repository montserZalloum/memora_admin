"""DuckDB connection manager for memora-analytics CLI.

Supports file-based and in-memory modes with context manager pattern.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

import duckdb

if TYPE_CHECKING:
    from analytics_cli.config import Config


@contextmanager
def connect(cfg: Config) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Open a DuckDB connection from config, closing on exit.

    Uses the ``duckdb_path`` from *cfg*.  Pass ``":memory:"`` as the
    path (via env or CLI flag) for an in-memory database.
    """
    conn = duckdb.connect(cfg.duckdb_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def connect_memory() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Open an ephemeral in-memory DuckDB connection (for tests)."""
    conn = duckdb.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()
