"""Dimension refresh service for analytics lakehouse (T021).

Exports dimension tables (player, player_history, season, plan, review_item,
lesson) to Parquet files and transfers them to the analytics server.

Runs inside the Frappe process — uses ``frappe.db.sql()`` for queries and
``pyarrow`` for Parquet writing.  Transfer to the analytics server is done
via ``rsync`` over SSH.

Usage (from Frappe context)::

    from memora_admin.memora_admin.services.dimension_refresh import (
        refresh_dimension,
        refresh_all_dimensions,
    )

    # Refresh a single dimension
    refresh_dimension("player")

    # Refresh all 6 dimensions
    refresh_all_dimensions()
"""

import os
import subprocess
import tempfile

import frappe
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

# ---------------------------------------------------------------------------
# Schema directory — relative to the Frappe app root
# ---------------------------------------------------------------------------

_SCHEMA_DIR = os.path.join(
    os.path.dirname(  # memora_admin/memora_admin/memora_admin/services/
        os.path.dirname(  # memora_admin/memora_admin/memora_admin/
            os.path.dirname(  # memora_admin/memora_admin/  (package root)
                os.path.dirname(  # memora_admin/  (app root)
                    os.path.abspath(__file__),
                ),
            ),
        ),
    ),
    "archive_schemas",
    "dimensions",
)

# ---------------------------------------------------------------------------
# Dimension registry — (entity, version) for all supported dimensions
# ---------------------------------------------------------------------------

DIMENSION_REGISTRY = [
    ("player", "v3"),
    ("player_history", "v1"),
    ("season", "v1"),
    ("plan", "v1"),
    ("review_item", "v2"),
    ("lesson", "v1"),
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_schema(entity: str, version: str) -> dict:
    """Load a YAML dimension schema from disk.

    Returns the parsed dict with keys: entity, version, source_table,
    id_column, fields, query.
    """
    path = os.path.join(_SCHEMA_DIR, f"{entity}.{version}.yaml")
    with open(path) as fh:
        return yaml.safe_load(fh)


def _export_dimension(entity: str, version: str, output_dir: str):
    """Export a single dimension to a Parquet file.

    For full-refresh schemas that contain ``{placeholders}`` in the WHERE
    clause, the WHERE clause is stripped so that all rows are returned.

    The ``player_history`` dimension uses an SCD2 LEAD window query that
    has no WHERE clause, so it works as-is.

    Returns (output_path, row_count).
    """
    schema = _load_schema(entity, version)
    query = schema["query"]
    fields = schema["fields"]

    # Full refresh: strip WHERE ... IN ({placeholders}) if present
    if "{placeholders}" in query:
        # Remove the WHERE clause entirely — keep everything before it
        where_idx = query.upper().find("WHERE")
        if where_idx != -1:
            query = query[:where_idx].strip()

    rows = frappe.db.sql(query, as_dict=True)

    if not rows:
        col_data = {f: [] for f in fields}
    else:
        col_data = {f: [row.get(f) for row in rows] for f in fields}

    table = pa.table(col_data)
    output_path = os.path.join(output_dir, f"dim_{entity}.parquet")
    pq.write_table(table, output_path)

    return output_path, len(rows)


def _transfer_dimensions(output_dir: str) -> None:
    """Transfer exported Parquet files to the analytics server via rsync.

    Reads connection details from environment variables:
    - ``ANALYTICS_SSH_HOST``
    - ``ANALYTICS_SSH_USER``
    - ``ANALYTICS_SSH_KEY_PATH`` (optional)
    - ``ANALYTICS_REMOTE_PATH`` (default ``/data/analytics``)

    Silently skips if ``ANALYTICS_SSH_HOST`` is not set.
    """
    ssh_host = os.environ.get("ANALYTICS_SSH_HOST")
    ssh_user = os.environ.get("ANALYTICS_SSH_USER")
    ssh_key = os.environ.get("ANALYTICS_SSH_KEY_PATH")
    remote_path = os.environ.get("ANALYTICS_REMOTE_PATH", "/data/analytics")

    if not ssh_host:
        frappe.logger().warning(
            "ANALYTICS_SSH_HOST not set — skipping dimension transfer"
        )
        return

    dims_remote = f"{remote_path}/dimensions/"
    cmd = ["rsync", "-az"]
    if ssh_key:
        cmd.extend(["-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=no"])
    cmd.extend([f"{output_dir}/", f"{ssh_user}@{ssh_host}:{dims_remote}"])

    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def refresh_dimension(entity: str, version: str = None) -> int:
    """Refresh a single dimension: export to Parquet then transfer.

    Args:
        entity: Dimension entity name (e.g. "player", "season").
        version: Schema version.  If *None*, looked up from
            :data:`DIMENSION_REGISTRY`.

    Returns:
        Row count exported.

    Raises:
        ValueError: If *entity* is not in the registry and no *version*
            is supplied.
    """
    if version is None:
        version = dict(DIMENSION_REGISTRY).get(entity)
    if version is None:
        raise ValueError(f"Unknown dimension entity: {entity}")

    with tempfile.TemporaryDirectory() as tmpdir:
        path, count = _export_dimension(entity, version, tmpdir)
        _transfer_dimensions(tmpdir)
        frappe.logger().info(
            f"Dimension refresh: {entity} {version} — {count} rows exported"
        )

    return count


def refresh_all_dimensions() -> dict:
    """Full refresh of all 6 dimension Parquet files.

    Each dimension is exported independently.  If one fails, it is logged
    and the remaining dimensions are still processed.  After all exports,
    a single rsync transfer sends everything to the analytics server.

    Returns:
        Dict mapping entity name to row count (or -1 on failure).
    """
    results: dict[str, int] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        for entity, version in DIMENSION_REGISTRY:
            try:
                _path, count = _export_dimension(entity, version, tmpdir)
                results[entity] = count
            except Exception:
                frappe.log_error(title=f"Dimension refresh failed: {entity}")
                results[entity] = -1

        _transfer_dimensions(tmpdir)

    frappe.logger().info(f"Full dimension refresh complete: {results}")
    return results
