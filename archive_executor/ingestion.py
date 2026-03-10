"""Remote ingestion + handoff module — calls analytics-side CLI commands via SSH.

The executor calls dedicated analytics-side commands (not ad-hoc SQL).
The analytics server has a CLI tool at config.analytics_cmd_path that accepts
structured commands and returns JSON results.

Interface contract (analytics-side tool):
  memora-analytics ingest-archive [--batch-dir DIR]
  memora-analytics ingest-live [--batch-dir DIR]
  memora-analytics handoff [--archive-batch-dir DIR] --date-column COL --from DATE --to DATE
  memora-analytics verify

Each command outputs JSON to stdout and exits with code 0 (success) or non-zero (failure).
Log lines are written to stderr before the final JSON block.
"""

import json
import shlex

from .config import Config
from .logger import StructuredLogger
from .transfer import TransferError, _run_ssh_command


class IngestionError(Exception):
	"""Raised when an ingestion or handoff operation fails."""


def _parse_remote_json(stdout: str, stderr: str, operation: str) -> dict:
	"""Parse JSON response from analytics-side command.

	The CLI writes log lines before the final JSON block; extract the last
	top-level JSON object from stdout.
	"""
	# Try direct parse first (analytics CLI outputs JSON-only to stdout)
	try:
		return json.loads(stdout.strip())
	except json.JSONDecodeError:
		pass
	# Fallback: find the first top-level '{' (handles log lines prepended to JSON)
	first_brace = stdout.find("{")
	if first_brace >= 0:
		try:
			return json.loads(stdout[first_brace:])
		except json.JSONDecodeError:
			pass
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
		Dict with {status, batches_ok, batches_error, ...}.
	"""
	command = (
		f"{shlex.quote(config.analytics_cmd_path)} ingest-archive "
		f"--batch-dir {shlex.quote(remote_path.rstrip('/'))}"
	)

	log.info("ingest_archive_started", remote_path=remote_path)

	returncode, stdout, stderr = _run_ssh_command(config, command, timeout=config.ssh_timeout)

	result = _parse_remote_json(stdout, stderr, "ingest-archive")

	if returncode != 0 or result.get("batches_error", 0) > 0:
		errors = (
			result.get("error")
			or result.get("batches", [{}])[0].get("error")
			or stderr[:500]
		)
		raise IngestionError(f"Archive ingestion failed: {errors}")

	log.info("ingest_archive_completed", batches_ok=result.get("batches_ok", 0))
	return result


def ingest_live_snapshot(
	config: Config,
	remote_path: str,
	manifest: dict,
	log: StructuredLogger,
) -> dict:
	"""Call analytics-side ingest command for live snapshot.

	Returns:
		Dict with {status, batches_ok, batches_error, ...}.
	"""
	command = (
		f"{shlex.quote(config.analytics_cmd_path)} ingest-live "
		f"--batch-dir {shlex.quote(remote_path.rstrip('/'))}"
	)

	log.info("ingest_live_started", remote_path=remote_path)

	returncode, stdout, stderr = _run_ssh_command(config, command, timeout=config.ssh_timeout)

	result = _parse_remote_json(stdout, stderr, "ingest-live")

	if returncode != 0 or result.get("batches_error", 0) > 0:
		errors = (
			result.get("error")
			or result.get("batches", [{}])[0].get("error")
			or stderr[:500]
		)
		raise IngestionError(f"Live ingestion failed: {errors}")

	log.info("ingest_live_completed", batches_ok=result.get("batches_ok", 0))
	return result


def handoff_archive(
	config: Config,
	archive_path: str,
	query_filter: dict,
	log: StructuredLogger,
) -> dict:
	"""Call analytics-side handoff command after archive ingestion.

	Removes the archived date range from live DuckDB tables to prevent
	double-counting.

	Returns:
		Dict with analytics-side response.
	"""
	date_from = query_filter.get("date_from", "")
	date_to = query_filter.get("date_to", "")
	date_column = query_filter.get("filter_column", "last_seen_at")

	command = (
		f"{shlex.quote(config.analytics_cmd_path)} handoff "
		f"--archive-batch-dir {shlex.quote(archive_path.rstrip('/'))} "
		f"--date-column {shlex.quote(date_column)} "
		f"--from {shlex.quote(str(date_from))} "
		f"--to {shlex.quote(str(date_to))}"
	)

	log.info("handoff_started", archive_path=archive_path)

	returncode, stdout, stderr = _run_ssh_command(config, command, timeout=config.ssh_timeout)

	result = _parse_remote_json(stdout, stderr, "handoff")

	if returncode != 0:
		errors = result.get("error", stderr[:500])
		raise IngestionError(f"Handoff failed: {errors}")

	log.info("handoff_completed", status=result.get("status"))
	return result


def verify_ingestion(
	config: Config,
	manifest: dict,
	remote_path: str,
	log: StructuredLogger,
) -> dict:
	"""Call analytics-side verify command.

	Returns:
		Dict with {status, checks}.
	"""
	command = shlex.quote(config.analytics_cmd_path) + " verify"

	log.info("verify_ingestion_started", remote_path=remote_path)

	returncode, stdout, stderr = _run_ssh_command(config, command, timeout=config.ssh_timeout)

	result = _parse_remote_json(stdout, stderr, "verify")

	if returncode != 0 or result.get("status") != "ok":
		log.warning("verify_ingestion_failed", status=result.get("status"))
		return {"valid": False, "errors": [result.get("status", "unknown")]}

	log.info("verify_ingestion_completed", status=result.get("status"))
	return {"valid": True, "checks": result.get("checks", {})}
