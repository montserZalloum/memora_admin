"""Frappe API for announcements cache hydration."""

import frappe
from frappe.utils import today


@frappe.whitelist(allow_guest=False)
def get_active_announcements() -> list[dict]:
	"""Return all published announcements with target plan data.

	Called by FastAPI AnnouncementService on cache miss.
	Filters by is_published=1, then returns all fields needed for
	client-side date/plan filtering.

	Returns:
		List of announcement dicts matching the cached data shape.
	"""
	announcements = frappe.get_all(
		"Memora Announcement",
		filters={"is_published": 1},
		fields=[
			"name",
			"title_ar",
			"title_en",
			"body_ar",
			"body_en",
			"target_audience",
			"display_frequency",
			"effective_start_date",
			"effective_end_date",
			"creation",
		],
		order_by="creation desc",
	)

	if not announcements:
		return []

	# Batch-fetch target plans for all announcements
	ann_names = [a.name for a in announcements]
	target_plans = frappe.get_all(
		"Memora Announcement Target Plan",
		filters={"parent": ["in", ann_names]},
		fields=["parent", "plan"],
	)
	plans_by_ann: dict[str, list[str]] = {}
	for tp in target_plans:
		plans_by_ann.setdefault(tp.parent, []).append(tp.plan)

	result = []
	for ann in announcements:
		# Map display_frequency to API format (lowercase, underscored)
		freq = (ann.display_frequency or "always").lower().replace(" ", "_")

		result.append({
			"id": ann.name,
			"title_ar": ann.title_ar,
			"title_en": ann.title_en,
			"body_ar": ann.body_ar,
			"body_en": ann.body_en,
			"target_audience": "all" if ann.target_audience == "All Players" else "specific_plans",
			"target_plans": plans_by_ann.get(ann.name, []),
			"display_frequency": freq,
			"effective_start_date": str(ann.effective_start_date) if ann.effective_start_date else None,
			"effective_end_date": str(ann.effective_end_date) if ann.effective_end_date else None,
			"created_at": str(ann.creation),
		})

	return result
