"""Configuration module for memora-analytics CLI.

Loads paths from environment variables with CLI flag overrides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


_DEFAULTS = {
    "DUCKDB_PATH": "analytics.duckdb",
    "LAKE_PATH": "lake",
    "DIMENSIONS_PATH": "dimensions",
    "MANIFESTS_PATH": "manifests",
}


@dataclass(frozen=True)
class Config:
    """Immutable analytics configuration."""

    duckdb_path: str
    lake_path: str
    dimensions_path: str
    manifests_path: str

    @classmethod
    def load(
        cls,
        *,
        duckdb_path: str | None = None,
        lake_path: str | None = None,
        dimensions_path: str | None = None,
        manifests_path: str | None = None,
    ) -> Config:
        """Build config: CLI flags override env vars override defaults."""
        return cls(
            duckdb_path=duckdb_path or os.environ.get("DUCKDB_PATH", _DEFAULTS["DUCKDB_PATH"]),
            lake_path=lake_path or os.environ.get("LAKE_PATH", _DEFAULTS["LAKE_PATH"]),
            dimensions_path=dimensions_path or os.environ.get("DIMENSIONS_PATH", _DEFAULTS["DIMENSIONS_PATH"]),
            manifests_path=manifests_path or os.environ.get("MANIFESTS_PATH", _DEFAULTS["MANIFESTS_PATH"]),
        )
