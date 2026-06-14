"""Daily live sync trigger — creates Pending live sync jobs.

Loads sync_types/ YAMLs from the schema registry and creates one
Memora Live Sync Job per type. Designed to run at 03:00 daily,
before the live sync executor cron at 03:05.

Schedule: Daily at 03:00 via hooks.py
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

# Cooldown between manual syncs (seconds)
MANUAL_SYNC_COOLDOWN = 900  # 15 minutes


def is_live_sync_enabled() -> bool:
	"""Whether live analytics sync to the analytics server is enabled.

	Reads the `live_sync_enabled` checkbox from Memora Settings. Defaults to
	True when the value has never been persisted (e.g. Settings not saved since
	the field was added), preserving pre-toggle behavior.
	"""
	value = frappe.db.get_single_value("Memora Settings", "live_sync_enabled")
	if value is None:
		return True
	return bool(value)


def trigger_daily_live_sync():
	"""Load sync_types/ YAMLs and create Pending live sync jobs.

	Skips if a Pending/Processing/Exported/Transferred/Ingested job already
	exists for the same sync_type (prevents duplicate runs).

	No-op if live analytics sync is disabled in Memora Settings.
	"""
	if not is_live_sync_enabled():
		frappe.logger().info("Live sync trigger: Live analytics sync is disabled in Memora Settings")
		return

	sync_types = _load_sync_types(_SCHEMA_REGISTRY_PATH)
	if not sync_types:
		frappe.logger().info("Live sync trigger: No sync type YAMLs found")
		return

	jobs_created = 0
	jobs_skipped = 0

	for sync_type in sync_types:
		type_name = sync_type.get("sync_type", "")
		version = sync_type.get("version", "v1")

		# Check if an active job already exists
		active_exists = frappe.db.exists(
			"Memora Live Sync Job",
			{
				"sync_type": type_name,
				"status": ["in", ["Pending", "Processing", "Exported", "Transferred", "Ingested"]],
			},
		)
		if active_exists:
			jobs_skipped += 1
			continue

		try:
			meta = _build_live_sync_meta(sync_type)

			job = frappe.get_doc({
				"doctype": "Memora Live Sync Job",
				"sync_type": type_name,
				"schema_version": version,
				"status": "Pending",
				"triggered_by": "Cron",
				"job_meta": json.dumps(meta),
			})
			job.flags.programmatic_creation = True
			job.insert(ignore_permissions=True)
			jobs_created += 1

		except Exception:
			frappe.log_error(
				title=f"Live sync trigger failed for {type_name}"
			)

	if jobs_created:
		frappe.db.commit()

	frappe.logger().info(
		f"Live sync trigger: {jobs_created} job(s) created, {jobs_skipped} skipped"
	)


@frappe.whitelist()
def trigger_manual_sync():
	"""Manual 'Sync Now' button API. Checks 15-min cooldown against last completed job.

	Returns error if cooldown not elapsed, otherwise creates Pending job(s).
	"""
	frappe.only_for("System Manager")

	if not is_live_sync_enabled():
		frappe.throw(
			"Live analytics sync is disabled in Memora Settings.",
			frappe.ValidationError,
		)

	# Check cooldown: last completed job within 15 minutes
	last_completed = frappe.get_all(
		"Memora Live Sync Job",
		filters={"status": "Completed"},
		fields=["completed_at"],
		order_by="completed_at DESC",
		limit=1,
	)

	if last_completed:
		last_ts = last_completed[0].completed_at
		if last_ts:
			elapsed = (frappe.utils.now_datetime() - last_ts).total_seconds()
			if elapsed < MANUAL_SYNC_COOLDOWN:
				remaining = int(MANUAL_SYNC_COOLDOWN - elapsed)
				frappe.throw(
					f"Cooldown active. Please wait {remaining} seconds before triggering another sync.",
					frappe.ValidationError,
				)

	# Also check for active jobs
	active_exists = frappe.db.exists(
		"Memora Live Sync Job",
		{"status": ["in", ["Pending", "Processing", "Exported", "Transferred", "Ingested"]]},
	)
	if active_exists:
		frappe.throw(
			"A live sync job is already in progress. Please wait for it to complete.",
			frappe.ValidationError,
		)

	sync_types = _load_sync_types(_SCHEMA_REGISTRY_PATH)
	if not sync_types:
		frappe.throw("No sync types configured.", frappe.ValidationError)

	jobs_created = 0
	for sync_type in sync_types:
		type_name = sync_type.get("sync_type", "")
		version = sync_type.get("version", "v1")

		# Per-sync_type dedup to prevent TOCTOU race with concurrent requests
		already_exists = frappe.db.exists(
			"Memora Live Sync Job",
			{
				"sync_type": type_name,
				"status": ["in", ["Pending", "Processing", "Exported", "Transferred", "Ingested"]],
			},
		)
		if already_exists:
			continue

		meta = _build_live_sync_meta(sync_type)

		job = frappe.get_doc({
			"doctype": "Memora Live Sync Job",
			"sync_type": type_name,
			"schema_version": version,
			"status": "Pending",
			"triggered_by": "Manual",
			"job_meta": json.dumps(meta),
		})
		job.flags.programmatic_creation = True
		job.insert(ignore_permissions=True)
		jobs_created += 1

	frappe.db.commit()
	return {"status": "success", "jobs_created": jobs_created}


def _load_sync_types(registry_path: str) -> list[dict]:
	"""Load all sync type YAML files from the registry."""
	types_dir = os.path.join(registry_path, "sync_types")
	if not os.path.isdir(types_dir):
		return []

	results = []
	for filename in sorted(os.listdir(types_dir)):
		if filename.endswith(".yaml") or filename.endswith(".yml"):
			file_path = os.path.join(types_dir, filename)
			with open(file_path) as f:
				results.append(yaml.safe_load(f))
	return results


def _build_live_sync_meta(sync_type: dict) -> dict:
	"""Build the meta JSON for a live sync job from sync type data."""
	meta = {
		"sync_type": sync_type.get("sync_type"),
		"source_table": sync_type.get("source_table"),
		"mode": sync_type.get("mode", "full_snapshot"),
		"export_columns": sync_type.get("fact_columns", []),
		"schema_snapshot": sync_type.get("schema_snapshot", {}),
		"related_tables": [
			{
				k: v
				for k, v in {
					"entity": d["entity"],
					"schema_version": d["schema_version"],
					"fact_column": d.get("join_column"),
					"scope_source": d.get("scope_source"),
				}.items()
				if v is not None
			}
			for d in sync_type.get("dimensions", [])
		],
	}
	if "fact_sql" in sync_type:
		meta["fact_sql"] = sync_type["fact_sql"]
	return meta
