"""Frappe API endpoints for Plan data.

Provides fallback data source for FastAPI when CDN files not yet generated.
"""

import json
import frappe


@frappe.whitelist(allow_guest=False)
def get_plan_manifest(plan_id: str) -> dict | None:
	"""
	Get plan manifest data.

	First tries to read from CDN files, falls back to generating on-the-fly.
	Used by FastAPI PlanService as fallback when cache is empty.

	Args:
	    plan_id: Memora Academic Plan document name

	Returns:
	    Plan manifest dict or None if plan not found
	"""
	# Try to read from generated CDN file first
	from memora_admin.memora_admin.services.build.storage import get_storage_backend

	storage = get_storage_backend()
	manifest_path = f"plans/{plan_id}/manifest.json"

	try:
		content = storage.read(manifest_path)
		if content:
			return json.loads(content.decode() if isinstance(content, bytes) else content)
	except Exception:
		pass  # Fall through to generation

	# File not found - generate on-the-fly (slower, but works)
	from memora_admin.memora_admin.services.build.plan_generator import generate_plan_json

	files = generate_plan_json(plan_id)

	# Find manifest in generated files
	for f in files:
		if f["filename"].endswith("manifest.json"):
			return json.loads(f["content"])

	return None
