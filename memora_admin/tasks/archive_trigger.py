"""Daily job: detect ended seasons and create Pending archive jobs.

Queries seasons with is_published=0 AND end_date < CURDATE(), then for each
season loads all registered archive type YAMLs and creates one Archive Job per
type. Duplicate jobs (same source_doctype + archive_scope + archive_type)
are skipped via explicit existence check before insert.

Runs daily at 01:20 (cron: 20 1 * * *) — after season unpublish (01:10).
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
					},
				)
				if already_exists:
					jobs_skipped += 1
					continue

				meta = _build_meta_json(season, archive_type)

				job = frappe.get_doc(
					{
						"doctype": "Memora Archive Job",
						"source_doctype": source_doctype,
						"archive_scope": season.name,
						"schema_version": archive_type["version"],
						"archive_type": archive_type_name,
						"status": "Pending",
						"post_archive_action": "Keep",
						"job_meta": json.dumps(meta),
					}
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


def _build_meta_json(season: dict, archive_type: dict) -> dict:
	"""Build the meta JSON for an archive job from season + archive type data."""
	# Build query filter from season dates
	query_filter = {
		"date_from": str(season.start_date),
		"date_to": str(season.end_date),
		"filter_column": archive_type.get("scope_column", "last_seen_at"),
	}

	# Build related_tables from dimension references
	related_tables = []
	for dim in archive_type.get("dimensions", []):
		# Load the dimension schema to get source_table and id_column
		dim_schema = _load_dimension_schema(_SCHEMA_REGISTRY_PATH, dim["entity"], dim["schema_version"])
		entry = {
			"entity": dim["entity"],
			"schema_version": dim["schema_version"],
			"source_table": dim_schema["source_table"],
			"join_column": dim_schema["id_column"],
		}
		# Direct dimensions have a join_column in the YAML; derived ones don't
		if "join_column" in dim:
			entry["fact_column"] = dim["join_column"]
		if dim.get("scope_source"):
			entry["scope_source"] = dim["scope_source"]
		related_tables.append(entry)

	meta = {
		"query_filter": query_filter,
		"export_columns": archive_type.get("fact_columns", []),
		"related_tables": related_tables,
		"schema_snapshot": archive_type.get("schema_snapshot", {}),
	}
	if "fact_sql" in archive_type:
		meta["fact_sql"] = archive_type["fact_sql"]
	return meta


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
