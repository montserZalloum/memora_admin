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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pyarrow.parquet as pq
import yaml

from .config import Config
from .exporter import export_incremental, export_snapshot
from .validator import validate_export
from .watermark import load_watermark, save_watermark

# All dataset names known to this exporter — used to validate ANALYTICS_DATASETS (T029).
KNOWN_DATASETS: frozenset[str] = frozenset({
	"practice_log",
	"item_mapping",
	"subjects",
	"tracks",
	"units",
	"topics",
	"lessons",
	"seasons",
	"grades",
	"majors",
	"academic_plans",
	"grade_majors",
})


@dataclass
class ExportResult:
	dataset: str
	success: bool
	row_count: int
	output_path: str
	violations: list[str] = field(default_factory=list)
	error: Optional[str] = None


def load_schema(schema_path: str) -> dict:
	"""Load and parse a YAML schema file. Returns the parsed dict."""
	with open(schema_path) as f:
		return yaml.safe_load(f)


def create_output_dir(path: str) -> None:
	"""Create the output directory (and any parents) if it does not exist."""
	os.makedirs(path, exist_ok=True)


def orchestrate_exports(config: Config, log: logging.Logger) -> dict[str, ExportResult]:
	"""Dispatch exports for each configured dataset.

	Returns a dict mapping dataset name → ExportResult.
	Datasets are filtered by config.analytics_datasets when non-empty.
	"""
	results: dict[str, ExportResult] = {}

	active = set(config.analytics_datasets) if config.analytics_datasets else None

	# --- US1: practice_log ---
	if active is None or "practice_log" in active:
		results["practice_log"] = _export_practice_log(config, log)

	# --- US2: item_mapping ---
	if active is None or "item_mapping" in active:
		results["item_mapping"] = _export_item_mapping(config, log)

	# --- US3: content hierarchy ---
	for schema_name in ("subjects", "tracks", "units", "topics", "lessons"):
		if active is None or schema_name in active:
			results[schema_name] = _export_full_snapshot_dataset(config, log, schema_name)

	# --- US4: academic context ---
	for schema_name in ("seasons", "grades", "majors", "academic_plans", "grade_majors"):
		if active is None or schema_name in active:
			results[schema_name] = _export_full_snapshot_dataset(config, log, schema_name)

	return results


def _export_practice_log(config: Config, log: logging.Logger) -> ExportResult:
	"""Export the practice_log dataset (US1)."""
	schema_path = os.path.join(config.analytics_schema_path, "practice_log.yaml")
	schema = load_schema(schema_path)

	output_path = os.path.join(config.analytics_output_path, schema["output_file"])
	wm_path = os.path.join(config.analytics_output_path, ".watermark.json")

	columns = [c["name"] for c in schema["columns"]]
	schema_def = schema["columns"]
	pk_columns = schema["primary_key"]
	dq_rules = schema["dq_rules"]

	watermark_data = load_watermark(wm_path) or {}
	dataset_wm = watermark_data.get("practice_log", {})
	last_wm = dataset_wm.get("last_watermark")

	try:
		use_incremental = (
			schema["mode"] == "incremental_watermark"
			and last_wm is not None
			and os.path.exists(output_path)
			and config.analytics_mode != "full"
		)

		if use_incremental:
			log.info("practice_log: incremental export (watermark=%s)", last_wm)
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
			log.info("practice_log: full snapshot export")
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
			log.warning("practice_log: DQ violations: %s", violations)
			return ExportResult(
				dataset="practice_log",
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
			"practice_log": {
				"last_watermark": new_last_wm,
				"last_export_at": datetime.utcnow().isoformat(),
				"last_row_count": count,
			},
		}
		save_watermark(wm_path, new_wm_data)

		log.info("practice_log: exported %d rows to %s", count, path)
		return ExportResult(
			dataset="practice_log",
			success=True,
			row_count=count,
			output_path=path,
			violations=[],
		)

	except Exception as exc:
		log.error("practice_log: export failed: %s", exc, exc_info=True)
		return ExportResult(
			dataset="practice_log",
			success=False,
			row_count=0,
			output_path=output_path,
			error=str(exc),
		)


def _export_item_mapping(config: Config, log: logging.Logger) -> ExportResult:
	"""Export the item_mapping dataset (US2)."""
	schema_path = os.path.join(config.analytics_schema_path, "item_mapping.yaml")
	schema = load_schema(schema_path)

	output_path = os.path.join(config.analytics_output_path, schema["output_file"])
	columns = [c["name"] for c in schema["columns"]]
	schema_def = schema["columns"]
	dq_rules = schema["dq_rules"]

	try:
		log.info("item_mapping: full snapshot export")
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
			log.warning("item_mapping: DQ violations: %s", violations)
			return ExportResult(
				dataset="item_mapping",
				success=False,
				row_count=count,
				output_path=path,
				violations=violations,
			)

		log.info("item_mapping: exported %d rows to %s", count, path)
		return ExportResult(
			dataset="item_mapping",
			success=True,
			row_count=count,
			output_path=path,
			violations=[],
		)

	except Exception as exc:
		log.error("item_mapping: export failed: %s", exc, exc_info=True)
		return ExportResult(
			dataset="item_mapping",
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

	# T029: Validate dataset names at startup
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

	# T030: ANALYTICS_MODE=incremental requires an existing watermark for practice_log
	active = set(config.analytics_datasets) if config.analytics_datasets else None
	if config.analytics_mode == "incremental" and (active is None or "practice_log" in active):
		wm_path = os.path.join(config.analytics_output_path, ".watermark.json")
		wm_data = load_watermark(wm_path)
		if wm_data is None or "practice_log" not in wm_data:
			log.error(
				"ANALYTICS_MODE=incremental but no practice_log watermark found at %s. "
				"Run in full or auto mode first.",
				wm_path,
			)
			return 2

	results = orchestrate_exports(config, log)

	failures = [r for r in results.values() if not r.success]
	if failures:
		log.error("Export failures: %s", [r.dataset for r in failures])
		return 1

	log.info("Analytics exporter complete. Datasets exported: %d", len(results))
	return 0
