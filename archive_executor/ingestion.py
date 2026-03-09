"""Remote ingestion + handoff module — calls analytics-side CLI commands via SSH.

The executor calls dedicated analytics-side commands (not ad-hoc SQL).
The analytics server has a CLI tool at config.analytics_cmd_path that accepts
structured commands and returns JSON results.

Interface contract (analytics-side tool):
  memora-analytics ingest-archive --manifest <path> --db <duckdb_path>
  memora-analytics ingest-live --manifest <path> --db <duckdb_path>
  memora-analytics handoff --archive-path <path> --filter '<json>' --db <duckdb_path>
  memora-analytics verify --manifest <path> --db <duckdb_path>

Each command outputs JSON to stdout and exits with code 0 (success) or 1 (failure).
"""

import json
import shlex

from .config import Config
from .logger import StructuredLogger
from .transfer import TransferError, _run_ssh_command


class IngestionError(Exception):
	"""Raised when an ingestion or handoff operation fails."""


def _parse_remote_json(stdout: str, stderr: str, operation: str) -> dict:
	"""Parse JSON response from analytics-side command."""
	try:
		return json.loads(stdout)
	except (json.JSONDecodeError, TypeError):
		raise IngestionError(
			f"{operation} returned invalid JSON. stdout={stdout[:1000]}, stderr={stderr[:1000]}"
		)


def ingest_archive_batch(
	config: Config,
	remote_path: str,
	manifest: dict,
	log: StructuredLogger,
) -> dict:
	"""Call analytics-side ingest command for archive data.

	The remote script loads Parquet into archive tables and verifies row counts.

	Returns:
		Dict with {success: bool, tables_loaded: int, errors: list}.
	"""
	manifest_path = f"{remote_path.rstrip('/')}/manifest.json"
	command = (
		f"{shlex.quote(config.analytics_cmd_path)} ingest-archive "
		f"--manifest {shlex.quote(manifest_path)} "
		f"--db {shlex.quote(config.duckdb_path)}"
	)

	log.info("ingest_archive_started", remote_path=remote_path)

	returncode, stdout, stderr = _run_ssh_command(config, command, timeout=config.ssh_timeout)

	if returncode != 0:
		result = _parse_remote_json(stdout, stderr, "ingest-archive")
		errors = result.get("errors", [stderr[:1000]])
		raise IngestionError(f"Archive ingestion failed: {errors}")

	result = _parse_remote_json(stdout, stderr, "ingest-archive")
	log.info("ingest_archive_completed", tables_loaded=result.get("tables_loaded", 0))
	return result


def ingest_live_snapshot(
	config: Config,
	remote_path: str,
	manifest: dict,
	log: StructuredLogger,
) -> dict:
	"""Call analytics-side ingest command for live snapshot.

	The remote script: staging table -> verify -> atomic swap.

	Returns:
		Dict with {success: bool, tables_swapped: int, errors: list}.
	"""
	manifest_path = f"{remote_path.rstrip('/')}/manifest.json"
	command = (
		f"{shlex.quote(config.analytics_cmd_path)} ingest-live "
		f"--manifest {shlex.quote(manifest_path)} "
		f"--db {shlex.quote(config.duckdb_path)}"
	)

	log.info("ingest_live_started", remote_path=remote_path)

	returncode, stdout, stderr = _run_ssh_command(config, command, timeout=config.ssh_timeout)

	if returncode != 0:
		result = _parse_remote_json(stdout, stderr, "ingest-live")
		errors = result.get("errors", [stderr[:1000]])
		raise IngestionError(f"Live ingestion failed: {errors}")

	result = _parse_remote_json(stdout, stderr, "ingest-live")
	log.info("ingest_live_completed", tables_swapped=result.get("tables_swapped", 0))
	return result


def handoff_archive(
	config: Config,
	archive_path: str,
	query_filter: dict,
	log: StructuredLogger,
) -> dict:
	"""Call analytics-side handoff command after archive ingestion.

	The remote script:
	1. Verifies archive data is queryable
	2. Removes overlapping date range from live DuckDB tables
	3. Confirms handoff success

	Returns:
		Dict with {success: bool, rows_removed_from_live: int, errors: list}.
	"""
	filter_json = json.dumps(query_filter)
	command = (
		f"{shlex.quote(config.analytics_cmd_path)} handoff "
		f"--archive-path {shlex.quote(archive_path)} "
		f"--filter {shlex.quote(filter_json)} "
		f"--db {shlex.quote(config.duckdb_path)}"
	)

	log.info("handoff_started", archive_path=archive_path)

	returncode, stdout, stderr = _run_ssh_command(config, command, timeout=config.ssh_timeout)

	if returncode != 0:
		result = _parse_remote_json(stdout, stderr, "handoff")
		errors = result.get("errors", [stderr[:1000]])
		raise IngestionError(f"Handoff failed: {errors}")

	result = _parse_remote_json(stdout, stderr, "handoff")
	log.info(
		"handoff_completed",
		rows_removed_from_live=result.get("rows_removed_from_live", 0),
	)
	return result


def verify_ingestion(
	config: Config,
	manifest: dict,
	remote_path: str,
	log: StructuredLogger,
) -> dict:
	"""Call analytics-side verify command.

	Returns:
		Dict with {valid: bool, errors: list}.
	"""
	manifest_path = f"{remote_path.rstrip('/')}/manifest.json"
	command = (
		f"{shlex.quote(config.analytics_cmd_path)} verify "
		f"--manifest {shlex.quote(manifest_path)} "
		f"--db {shlex.quote(config.duckdb_path)}"
	)

	log.info("verify_ingestion_started", remote_path=remote_path)

	returncode, stdout, stderr = _run_ssh_command(config, command, timeout=config.ssh_timeout)

	if returncode != 0:
		result = _parse_remote_json(stdout, stderr, "verify")
		errors = result.get("errors", [stderr[:1000]])
		return {"valid": False, "errors": errors}

	result = _parse_remote_json(stdout, stderr, "verify")
	log.info("verify_ingestion_completed", valid=result.get("valid", False))
	return result
