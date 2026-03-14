"""Analytics exporter orchestration.

Entry point: python3 -m analytics_exporter

Exit codes:
  0 — all exports succeeded
  1 — one or more exports failed
  2 — fatal configuration or startup error
"""

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pyarrow.parquet as pq
import yaml

from .config import Config
from .exporter import export_incremental, export_snapshot
from .manifest import compute_sha256, write_manifest
from .transfer import transfer_exports
from .validator import validate_export
from .watermark import load_watermark, save_watermark

# ---------------------------------------------------------------------------
# Dataset catalog
# ---------------------------------------------------------------------------

# All individual dataset names + multi-file group aliases.
KNOWN_DATASETS: frozenset[str] = frozenset({
	# Dimensions
	"dim_player",
	"dim_content_hierarchy",
	"dim_review_item",
	"dim_season",
	"dim_academic_plan",
	# Core facts
	"fact_interaction",
	"fact_memory_state",
	"fact_practice",
	"fact_subscription",
	"fact_voucher",
	"fact_challenge_attempt",
	"fact_challenge_detail",
	# Supplementary
	"fact_structure_progress",
	"fact_player_wallet",
	"dim_lesson_stage",
	"fact_content_report",
	"fact_live_challenge_event",
	"fact_live_challenge_participation",
	"fact_archive_job",
	"fact_task_run_log",
	"fact_build_queue",
	# Multi-file group aliases (selecting any member or the alias exports the full group)
	"fact_challenge",
	"fact_live_challenge",
	"fact_task_run",
})

# Multi-file group definitions: group_alias -> [schema_name, ...]
MULTI_FILE_GROUPS: dict[str, list[str]] = {
	"fact_challenge": ["fact_challenge_attempt", "fact_challenge_detail"],
	"fact_live_challenge": ["fact_live_challenge_event", "fact_live_challenge_participation"],
	"fact_task_run": ["fact_task_run_log", "fact_build_queue"],
}

# Dimension datasets (exported first — reference data for fact table joins)
_DIMENSION_DATASETS: list[str] = [
	"dim_player",
	"dim_content_hierarchy",
	"dim_review_item",
	"dim_season",
	"dim_academic_plan",
]

# Core fact datasets (exported after dimensions)
_CORE_FACT_SNAPSHOTS: list[str] = [
	"fact_memory_state",
	"fact_subscription",
	"fact_voucher",
]

# Supplementary snapshot datasets
_SUPPLEMENTARY_SNAPSHOTS: list[str] = [
	"fact_structure_progress",
	"fact_player_wallet",
	"dim_lesson_stage",
	"fact_content_report",
	"fact_archive_job",
]


@dataclass
class ExportResult:
	dataset: str
	success: bool
	row_count: int
	output_path: str
	violations: list[str] = field(default_factory=list)
	error: Optional[str] = None
	duration_sec: float = 0.0
	mode: str = ""


def load_schema(schema_path: str) -> dict:
	"""Load and parse a YAML schema file. Returns the parsed dict."""
	with open(schema_path) as f:
		return yaml.safe_load(f)


def create_output_dir(path: str) -> None:
	"""Create the output directory (and any parents) if it does not exist."""
	os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers: should a dataset be exported?
# ---------------------------------------------------------------------------

def _is_active(name: str, active: set[str] | None) -> bool:
	"""Check if a dataset or group should be exported given the active filter."""
	if active is None:
		return True
	if name in active:
		return True
	# Check if any member of a multi-file group is in active set
	for group_alias, members in MULTI_FILE_GROUPS.items():
		if name == group_alias and any(m in active for m in members):
			return True
		if name in members and group_alias in active:
			return True
	return False


# ---------------------------------------------------------------------------
# Export wrappers with manifest generation
# ---------------------------------------------------------------------------

def _export_snapshot_with_manifest(
	config: Config, log: logging.Logger, schema_name: str
) -> ExportResult:
	"""Export a full_snapshot dataset and write its manifest."""
	result = _export_full_snapshot_dataset(config, log, schema_name)
	if result.success:
		_write_single_manifest(config.analytics_output_path, result)
	return result


def _export_date_range_dataset(
	config: Config, log: logging.Logger, schema_name: str
) -> ExportResult:
	"""Export a date-range filtered dataset with manifest."""
	schema_path = os.path.join(config.analytics_schema_path, f"{schema_name}.yaml")
	schema = load_schema(schema_path)

	dataset = schema["dataset"]
	output_path = os.path.join(config.analytics_output_path, schema["output_file"])
	columns = [c["name"] for c in schema["columns"]]
	schema_def = schema["columns"]
	dq_rules = schema["dq_rules"]

	# Compute date range
	if config.analytics_interaction_from and config.analytics_interaction_to:
		from_date = config.analytics_interaction_from
		to_date = config.analytics_interaction_to
	else:
		today = datetime.utcnow().date()
		from_date = (today - timedelta(days=30)).isoformat()
		to_date = today.isoformat()

	try:
		log.info("%s: date-range export [%s, %s]", dataset, from_date, to_date)
		path, count = export_snapshot(
			config,
			schema["sql_full"],
			(from_date, to_date),
			columns,
			schema_def,
			output_path,
		)

		violations = validate_export(path, dq_rules)
		if violations:
			log.warning("%s: DQ violations: %s", dataset, violations)
			return ExportResult(
				dataset=dataset, success=False, row_count=count,
				output_path=path, violations=violations,
			)

		log.info("%s: exported %d rows to %s", dataset, count, path)
		result = ExportResult(
			dataset=dataset, success=True, row_count=count,
			output_path=path, violations=[],
		)
		_write_single_manifest(config.analytics_output_path, result)
		return result

	except Exception as exc:
		log.error("%s: export failed: %s", dataset, exc, exc_info=True)
		return ExportResult(
			dataset=dataset, success=False, row_count=0,
			output_path=output_path, error=str(exc),
		)


def _export_multi_file_dataset(
	config: Config, log: logging.Logger, group_key: str, schema_names: list[str]
) -> dict[str, ExportResult]:
	"""Export a multi-file dataset atomically. Both succeed or both fail.

	Returns a dict of schema_name -> ExportResult for each member.
	On failure, cleans up any partial files.
	"""
	results: dict[str, ExportResult] = {}
	try:
		for name in schema_names:
			result = _export_full_snapshot_dataset(config, log, name)
			results[name] = result
			if not result.success:
				raise _ExportError(
					f"{name} failed: {result.error or result.violations}"
				)

		# All succeeded — write combined manifest
		files_info = []
		for name in schema_names:
			r = results[name]
			filename = os.path.basename(r.output_path)
			checksum = compute_sha256(r.output_path)
			size_bytes = os.path.getsize(r.output_path)
			files_info.append({
				"filename": filename,
				"row_count": r.row_count,
				"checksum": checksum,
				"size_bytes": size_bytes,
			})
		write_manifest(config.analytics_output_path, group_key, files_info)
		return results

	except Exception:
		# Clean up any partial files on failure
		for r in results.values():
			if r.success and os.path.exists(r.output_path):
				try:
					os.remove(r.output_path)
				except OSError:
					pass
		# Mark all as failed if any failed
		for name in schema_names:
			if name not in results:
				schema_path = os.path.join(
					config.analytics_schema_path, f"{name}.yaml"
				)
				output_path = os.path.join(
					config.analytics_output_path, f"{name}.parquet"
				)
				results[name] = ExportResult(
					dataset=name, success=False, row_count=0,
					output_path=output_path,
					error="skipped due to group failure",
				)
		return results


class _ExportError(Exception):
	pass


def _write_single_manifest(output_dir: str, result: ExportResult) -> None:
	"""Write manifest for a single-file export result."""
	filename = os.path.basename(result.output_path)
	checksum = compute_sha256(result.output_path)
	size_bytes = os.path.getsize(result.output_path)
	write_manifest(output_dir, result.dataset, [{
		"filename": filename,
		"row_count": result.row_count,
		"checksum": checksum,
		"size_bytes": size_bytes,
	}])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def orchestrate_exports(config: Config, log: logging.Logger) -> dict[str, ExportResult]:
	"""Dispatch exports for each configured dataset.

	Returns a dict mapping dataset name -> ExportResult.
	Datasets are filtered by config.analytics_datasets when non-empty.
	Export order: dimensions -> core facts -> supplementary.
	"""
	results: dict[str, ExportResult] = {}

	active = set(config.analytics_datasets) if config.analytics_datasets else None

	# --- Dimension datasets (full snapshot with manifest) ---
	for ds in _DIMENSION_DATASETS:
		if _is_active(ds, active):
			t0 = time.monotonic()
			results[ds] = _export_snapshot_with_manifest(config, log, ds)
			results[ds].duration_sec = time.monotonic() - t0

	# --- Core fact datasets ---

	# fact_interaction (date-range)
	if _is_active("fact_interaction", active):
		t0 = time.monotonic()
		results["fact_interaction"] = _export_date_range_dataset(
			config, log, "fact_interaction"
		)
		results["fact_interaction"].duration_sec = time.monotonic() - t0

	# fact_memory_state, fact_subscription, fact_voucher (full snapshot)
	for ds in _CORE_FACT_SNAPSHOTS:
		if _is_active(ds, active):
			t0 = time.monotonic()
			results[ds] = _export_snapshot_with_manifest(config, log, ds)
			results[ds].duration_sec = time.monotonic() - t0

	# fact_practice (incremental watermark)
	if _is_active("fact_practice", active):
		t0 = time.monotonic()
		results["fact_practice"] = _export_fact_practice(config, log)
		results["fact_practice"].duration_sec = time.monotonic() - t0

	# fact_challenge (multi-file)
	if _is_active("fact_challenge", active):
		t0 = time.monotonic()
		group_results = _export_multi_file_dataset(
			config, log, "fact_challenge",
			MULTI_FILE_GROUPS["fact_challenge"],
		)
		elapsed = time.monotonic() - t0
		for r in group_results.values():
			r.duration_sec = elapsed / max(len(group_results), 1)
		results.update(group_results)

	# --- Supplementary datasets ---

	# Single-file snapshots
	for ds in _SUPPLEMENTARY_SNAPSHOTS:
		if _is_active(ds, active):
			t0 = time.monotonic()
			results[ds] = _export_snapshot_with_manifest(config, log, ds)
			results[ds].duration_sec = time.monotonic() - t0

	# fact_live_challenge (multi-file)
	if _is_active("fact_live_challenge", active):
		t0 = time.monotonic()
		group_results = _export_multi_file_dataset(
			config, log, "fact_live_challenge",
			MULTI_FILE_GROUPS["fact_live_challenge"],
		)
		elapsed = time.monotonic() - t0
		for r in group_results.values():
			r.duration_sec = elapsed / max(len(group_results), 1)
		results.update(group_results)

	# fact_task_run (multi-file)
	if _is_active("fact_task_run", active):
		t0 = time.monotonic()
		group_results = _export_multi_file_dataset(
			config, log, "fact_task_run",
			MULTI_FILE_GROUPS["fact_task_run"],
		)
		elapsed = time.monotonic() - t0
		for r in group_results.values():
			r.duration_sec = elapsed / max(len(group_results), 1)
		results.update(group_results)

	return results


# ---------------------------------------------------------------------------
# Dataset-specific exporters
# ---------------------------------------------------------------------------

def _export_fact_practice(config: Config, log: logging.Logger) -> ExportResult:
	"""Export the fact_practice dataset (incremental watermark) with manifest."""
	schema_path = os.path.join(config.analytics_schema_path, "fact_practice.yaml")
	schema = load_schema(schema_path)

	output_path = os.path.join(config.analytics_output_path, schema["output_file"])
	wm_path = os.path.join(config.analytics_output_path, ".watermark.json")

	columns = [c["name"] for c in schema["columns"]]
	schema_def = schema["columns"]
	pk_columns = schema["primary_key"]
	dq_rules = schema["dq_rules"]

	watermark_data = load_watermark(wm_path) or {}
	dataset_wm = watermark_data.get("fact_practice", {})
	last_wm = dataset_wm.get("last_watermark")

	try:
		use_incremental = (
			schema["mode"] == "incremental_watermark"
			and last_wm is not None
			and os.path.exists(output_path)
			and config.analytics_mode != "full"
		)

		if use_incremental:
			log.info("fact_practice: incremental export (watermark=%s)", last_wm)
			wm_dt = datetime.fromisoformat(last_wm)
			path, count = export_incremental(
				config,
				output_path,
				schema["sql_incremental"],
				(wm_dt,),
				columns,
				schema_def,
				pk_columns,
			)
		else:
			log.info("fact_practice: full snapshot export")
			path, count = export_snapshot(
				config,
				schema["sql_full"],
				(),
				columns,
				schema_def,
				output_path,
			)

		violations = validate_export(path, dq_rules)
		if violations:
			log.warning("fact_practice: DQ violations: %s", violations)
			return ExportResult(
				dataset="fact_practice",
				success=False,
				row_count=count,
				output_path=path,
				violations=violations,
			)

		# Compute new watermark from the merged output file
		table = pq.read_table(path)
		new_last_wm = last_wm  # preserve if no rows
		if table.num_rows > 0:
			ts_list = table.column(schema["watermark_column"]).to_pylist()
			max_ts = max(ts for ts in ts_list if ts is not None)
			new_last_wm = max_ts.isoformat() if hasattr(max_ts, "isoformat") else str(max_ts)

		new_wm_data = {
			**watermark_data,
			"fact_practice": {
				"last_watermark": new_last_wm,
				"last_export_at": datetime.utcnow().isoformat(),
				"last_row_count": count,
			},
		}
		save_watermark(wm_path, new_wm_data)

		log.info("fact_practice: exported %d rows to %s", count, path)
		result = ExportResult(
			dataset="fact_practice",
			success=True,
			row_count=count,
			output_path=path,
			violations=[],
			mode="incremental" if use_incremental else "full",
		)
		# Generate manifest for fact_practice
		_write_single_manifest(config.analytics_output_path, result)
		return result

	except Exception as exc:
		log.error("fact_practice: export failed: %s", exc, exc_info=True)
		return ExportResult(
			dataset="fact_practice",
			success=False,
			row_count=0,
			output_path=output_path,
			error=str(exc),
		)


def _export_full_snapshot_dataset(
	config: Config, log: logging.Logger, schema_name: str
) -> ExportResult:
	"""Export any full_snapshot dataset given its schema YAML file name (without .yaml)."""
	schema_path = os.path.join(config.analytics_schema_path, f"{schema_name}.yaml")
	schema = load_schema(schema_path)

	dataset = schema["dataset"]
	output_path = os.path.join(config.analytics_output_path, schema["output_file"])
	columns = [c["name"] for c in schema["columns"]]
	schema_def = schema["columns"]
	dq_rules = schema["dq_rules"]

	try:
		log.info("%s: full snapshot export", dataset)
		path, count = export_snapshot(
			config,
			schema["sql_full"],
			(),
			columns,
			schema_def,
			output_path,
		)

		violations = validate_export(path, dq_rules)
		if violations:
			log.warning("%s: DQ violations: %s", dataset, violations)
			return ExportResult(
				dataset=dataset,
				success=False,
				row_count=count,
				output_path=path,
				violations=violations,
			)

		log.info("%s: exported %d rows to %s", dataset, count, path)
		return ExportResult(
			dataset=dataset,
			success=True,
			row_count=count,
			output_path=path,
			violations=[],
		)

	except Exception as exc:
		log.error("%s: export failed: %s", dataset, exc, exc_info=True)
		return ExportResult(
			dataset=dataset,
			success=False,
			row_count=0,
			output_path=output_path,
			error=str(exc),
		)


# ---------------------------------------------------------------------------
# Logger + main
# ---------------------------------------------------------------------------

def _setup_logger(log_path: str) -> logging.Logger:
	"""Configure and return the analytics_exporter logger."""
	log = logging.getLogger("analytics_exporter")
	log.setLevel(logging.INFO)

	if log.handlers:
		return log

	formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

	ch = logging.StreamHandler(sys.stdout)
	ch.setLevel(logging.INFO)
	ch.setFormatter(formatter)
	log.addHandler(ch)

	if log_path:
		log_dir = os.path.dirname(log_path)
		if log_dir:
			os.makedirs(log_dir, exist_ok=True)
		fh = logging.FileHandler(log_path)
		fh.setLevel(logging.INFO)
		fh.setFormatter(formatter)
		log.addHandler(fh)

	return log


def main() -> int:
	"""Run the analytics exporter. Returns exit code 0, 1, or 2."""
	try:
		config = Config.from_env()
	except KeyError as exc:
		print(f"FATAL: missing required environment variable {exc}", file=sys.stderr)
		return 2

	log = _setup_logger(config.analytics_log_path)
	log.info("Analytics exporter starting")

	# Validate dataset names at startup
	if config.analytics_datasets:
		unknown = set(config.analytics_datasets) - KNOWN_DATASETS
		if unknown:
			log.error(
				"Unknown dataset names in ANALYTICS_DATASETS: %s. Known datasets: %s",
				sorted(unknown),
				sorted(KNOWN_DATASETS),
			)
			return 2

	create_output_dir(config.analytics_output_path)

	# ANALYTICS_MODE=incremental requires an existing watermark for fact_practice
	active = set(config.analytics_datasets) if config.analytics_datasets else None
	if config.analytics_mode == "incremental" and (active is None or "fact_practice" in active):
		wm_path = os.path.join(config.analytics_output_path, ".watermark.json")
		wm_data = load_watermark(wm_path)
		if wm_data is None or "fact_practice" not in wm_data:
			log.error(
				"ANALYTICS_MODE=incremental but no fact_practice watermark found at %s. "
				"Run in full or auto mode first.",
				wm_path,
			)
			return 2

	results = orchestrate_exports(config, log)

	# Print per-dataset summary lines
	for result in results.values():
		label = f"[{result.dataset}]"
		rows_field = f"rows={result.row_count}"
		dur = int(result.duration_sec)
		dur_field = f"duration={dur}s"
		status = "ok" if result.success else "failed"
		mode_suffix = f"  mode={result.mode}" if result.mode else ""
		line = f"{label:<40}{rows_field:<12}{dur_field:<14}status={status}{mode_suffix}"
		log.info(line)

	failures = [r for r in results.values() if not r.success]
	if failures:
		log.error("Export failures: %s", [r.dataset for r in failures])
		return 1

	# Transfer to analytics server (skipped if SSH not configured)
	if not transfer_exports(config):
		log.error("Transfer to analytics server failed")
		return 1

	log.info("Analytics exporter complete. Datasets exported: %d", len(results))
	return 0
