"""Daily archive triggers for ended seasons.

This module wires two parallel scheduling paths:

1. Date-window archive jobs for generic archive types
2. Season-scoped archive jobs for archive types that declare trigger_mode=season

Both paths run daily at 01:20 (cron: 20 1 * * *) after season unpublish (01:10).
"""

import json
import os

import frappe
import yaml

# Path to archive_schemas/ directory within the app
_SCHEMA_REGISTRY_PATH = os.path.join(
	os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
	"archive_schemas",
)


def check_seasons_for_archive():
	"""Scan ended seasons and create Pending archive jobs for each archive type.

	Only considers seasons ended within the last 90 days to avoid unbounded growth.
	"""
	today = frappe.utils.today()

	cutoff = frappe.utils.add_days(today, -90)

	# Step 1: Find recently ended, unpublished seasons (within the last 90 days)
	ended_seasons = frappe.db.sql(
		"""
		SELECT name, start_date, end_date
		FROM `tabMemora Season`
		WHERE is_published = 0
		  AND end_date < %s
		  AND end_date >= %s
		""",
		(today, cutoff),
		as_dict=True,
	)

	if not ended_seasons:
		frappe.logger().info("Archive trigger: No ended seasons found")
		return

	# Step 2: Load all archive type definitions from YAML registry
	archive_types = _load_archive_types(_SCHEMA_REGISTRY_PATH)
	if not archive_types:
		frappe.logger().info("Archive trigger: No archive type YAMLs found in registry")
		return

	archive_types = [a for a in archive_types if not _is_season_scoped_archive_type(a)]
	if not archive_types:
		frappe.logger().info("Archive trigger: No date-window archive type YAMLs found in registry")
		return

	jobs_created = 0
	jobs_skipped = 0

	# Step 3: For each season x archive type, create an Archive Job
	for season in ended_seasons:
		for archive_type in archive_types:
			try:
				source_doctype = _format_source_doctype(archive_type["source_table"])
				archive_type_name = archive_type["archive_type"]

				# Explicit dedup check — no DB unique constraint exists
				already_exists = frappe.db.exists(
					"Memora Archive Job",
					{
						"source_doctype": source_doctype,
						"archive_scope": season.name,
						"archive_type": archive_type_name,
						"schema_version": archive_type["version"],
					},
				)
				if already_exists:
					jobs_skipped += 1
					continue

				meta = _build_meta_json(season, archive_type)

				job = _build_archive_job_doc(
					source_doctype=source_doctype,
					archive_scope=season.name,
					archive_type=archive_type_name,
					schema_version=archive_type["version"],
					job_meta=meta,
					post_archive_action="Keep",
				)
				job.flags.programmatic_creation = True
				job.insert(ignore_permissions=True)
				jobs_created += 1

			except Exception:
				frappe.log_error(
					title=f"Archive trigger failed for {season.name} / {archive_type.get('archive_type', 'unknown')}"
				)

	if jobs_created:
		frappe.db.commit()

	frappe.logger().info(
		f"Archive trigger complete: {jobs_created} job(s) created, "
		f"{jobs_skipped} duplicate(s) skipped, "
		f"{len(ended_seasons)} season(s) checked"
	)


def check_season_scoped_archives():
	"""Scan ended seasons and create Pending archive jobs for season-scoped types."""
	today = frappe.utils.today()

	ended_seasons = frappe.db.sql(
		"""
		SELECT name, season_seq, end_date
		FROM `tabMemora Season`
		WHERE is_published = 0
		  AND end_date < %s
		""",
		(today,),
		as_dict=True,
	)

	if not ended_seasons:
		frappe.logger().info("Season archive trigger: No ended seasons found")
		return

	archive_types = _load_archive_types(_SCHEMA_REGISTRY_PATH)
	if not archive_types:
		frappe.logger().info("Season archive trigger: No archive type YAMLs found in registry")
		return

	archive_types = [a for a in archive_types if _is_season_scoped_archive_type(a)]
	if not archive_types:
		frappe.logger().info("Season archive trigger: No season-scoped archive type YAMLs found in registry")
		return

	jobs_created = 0
	jobs_skipped = 0

	for season in ended_seasons:
		for archive_type in archive_types:
			try:
				source_doctype = _format_source_doctype(archive_type["source_table"])
				archive_type_name = archive_type["archive_type"]
				archive_scope = _build_season_archive_scope(season.season_seq)

				already_exists = frappe.db.exists(
					"Memora Archive Job",
					{
						"source_doctype": source_doctype,
						"archive_scope": archive_scope,
						"archive_type": archive_type_name,
						"schema_version": archive_type["version"],
						"status": ["!=", "Failed"],
					},
				)
				if already_exists:
					jobs_skipped += 1
					continue

				meta = _build_season_meta_json(season, archive_type)

				job = _build_archive_job_doc(
					source_doctype=source_doctype,
					archive_scope=archive_scope,
					archive_type=archive_type_name,
					schema_version=archive_type["version"],
					job_meta=meta,
					post_archive_action="Delete",
				)
				job.flags.programmatic_creation = True
				job.insert(ignore_permissions=True)
				jobs_created += 1

			except Exception:
				frappe.log_error(
					title=f"Season archive trigger failed for {season.name} / {archive_type.get('archive_type', 'unknown')}"
				)

	if jobs_created:
		frappe.db.commit()

	frappe.logger().info(
		f"Season archive trigger complete: {jobs_created} job(s) created, "
		f"{jobs_skipped} duplicate(s) skipped, "
		f"{len(ended_seasons)} season(s) checked"
	)


def _load_archive_types(registry_path: str) -> list[dict]:
	"""Load all archive type YAML files from the registry."""
	types_dir = os.path.join(registry_path, "archive_types")
	if not os.path.isdir(types_dir):
		return []

	results = []
	for filename in sorted(os.listdir(types_dir)):
		if filename.endswith(".yaml") or filename.endswith(".yml"):
			file_path = os.path.join(types_dir, filename)
			with open(file_path) as f:
				results.append(yaml.safe_load(f))
	return results


def _is_season_scoped_archive_type(archive_type: dict) -> bool:
	"""Return True when the archive type declares season-scoped trigger wiring."""
	return archive_type.get("trigger_mode") == "season"


def _build_related_tables(archive_type: dict) -> list[dict]:
	"""Build related_tables metadata from dimension references."""
	related_tables = []
	for dim in archive_type.get("dimensions", []):
		dim_schema = _load_dimension_schema(_SCHEMA_REGISTRY_PATH, dim["entity"], dim["schema_version"])
		entry = {
			"entity": dim["entity"],
			"schema_version": dim["schema_version"],
			"source_table": dim_schema["source_table"],
			"join_column": dim_schema["id_column"],
		}
		if "join_column" in dim:
			entry["fact_column"] = dim["join_column"]
		if dim.get("scope_source"):
			entry["scope_source"] = dim["scope_source"]
		related_tables.append(entry)
	return related_tables


def _build_meta_json(season: dict, archive_type: dict) -> dict:
	"""Build the meta JSON for an archive job from season + archive type data."""
	# Build query filter from season dates
	query_filter = {
		"date_from": str(season.start_date),
		"date_to": str(season.end_date),
		"filter_column": archive_type.get("scope_column", "last_seen_at"),
	}

	meta = {
		"query_filter": query_filter,
		"export_columns": archive_type.get("fact_columns", []),
		"related_tables": _build_related_tables(archive_type),
		"schema_snapshot": archive_type.get("schema_snapshot", {}),
	}
	if "fact_sql" in archive_type:
		meta["fact_sql"] = archive_type["fact_sql"]
	return meta


def _build_season_meta_json(season: dict, archive_type: dict) -> dict:
	"""Build season-scoped job_meta for archive types keyed by season_seq."""
	scope_column = archive_type.get("scope_column", "season_seq")

	meta = {
		"query_filter": {
			"season_seq": season.season_seq,
			"season_name": season.name,
			"filter_column": scope_column,
			"filter_type": "season",
		},
		"export_columns": archive_type.get("fact_columns", []),
		"related_tables": _build_related_tables(archive_type),
		"schema_snapshot": archive_type.get("schema_snapshot", {}),
		"scope_column": scope_column,
	}
	if "fact_sql" in archive_type:
		meta["fact_sql"] = archive_type["fact_sql"]
	return meta


def _build_archive_job_doc(
	*,
	source_doctype: str,
	archive_scope: str,
	archive_type: str,
	schema_version: str,
	job_meta: dict,
	post_archive_action: str,
):
	"""Construct a Memora Archive Job document payload."""
	return frappe.get_doc(
		{
			"doctype": "Memora Archive Job",
			"source_doctype": source_doctype,
			"archive_scope": archive_scope,
			"schema_version": schema_version,
			"archive_type": archive_type,
			"status": "Pending",
			"post_archive_action": post_archive_action,
			"job_meta": json.dumps(job_meta),
		}
	)


def _build_season_archive_scope(season_seq: int) -> str:
	"""Return the canonical archive_scope used by season-scoped archive jobs."""
	return f"season_{season_seq}"


def _load_dimension_schema(registry_path: str, entity: str, version: str) -> dict:
	"""Load a single dimension schema YAML."""
	file_path = os.path.join(registry_path, "dimensions", f"{entity}.{version}.yaml")
	if not os.path.isfile(file_path):
		raise FileNotFoundError(f"Dimension schema not found: {file_path}")
	with open(file_path) as f:
		return yaml.safe_load(f)


def _format_source_doctype(source_table: str) -> str:
	"""Convert a MariaDB table name like 'tabMemora Practice Log' to 'Memora Practice Log'."""
	if source_table.startswith("tab"):
		return source_table[3:]
	return source_table
