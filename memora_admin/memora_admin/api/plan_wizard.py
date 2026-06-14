"""Frappe API endpoints for the Plan → Subject selection wizard.

Backs the `plan_subject_wizard` desk page:
  Step 1: admin picks an Academic Plan
  Step 2: wizard lists the subjects belonging to that plan so the admin picks one

Read-only endpoints; restricted to admin-style roles.
"""

import json

import frappe
from frappe import _

_ALLOWED_ROLES = {"System Manager", "Memora Admin", "Task Admin"}

# Levels reordered via their own `sort_order` field, keyed by the wizard level.
# Value is (doctype, parent_field) — mirrors the topic→lesson reorder dialog.
_ORDER_LEVELS = {
	"track": ("Memora Track", "subject"),
	"unit": ("Memora Unit", "track"),
	"topic": ("Memora Topic", "unit"),
	"lesson": ("Memora Lesson", "topic"),
}


def _check_access() -> None:
	"""Allow only admin-style roles to use the wizard endpoints."""
	if not _ALLOWED_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You don't have permission to use the plan wizard"), frappe.PermissionError)


def _title_map(doctype: str, ids: list[str], title_field: str) -> dict:
	"""Map record name → its title for a set of link ids (skips blanks)."""
	uniq = [i for i in set(ids) if i]
	if not uniq:
		return {}
	return {
		r.name: r.get(title_field)
		for r in frappe.get_all(doctype, filters={"name": ["in", uniq]}, fields=["name", title_field])
	}


@frappe.whitelist()
def get_plans() -> list[dict]:
	"""Return all Academic Plans for the wizard's first step.

	Grade/major/season are stored as hash-named links, so each plan is
	enriched with human-readable `grade_title`, `major_title`, `season_title`.
	Filtering is done client-side, so no server-side search arg.

	Returns:
	    List of plan dicts incl. the *_title fields.
	"""
	_check_access()

	plans = frappe.get_all(
		"Memora Academic Plan",
		fields=["name", "plan_name", "grade", "major", "season", "is_published"],
		order_by="plan_name asc",
	)

	grades = _title_map("Memora Grade", [p.grade for p in plans], "grade_title")
	majors = _title_map("Memora Major", [p.major for p in plans], "major_title")
	seasons = _title_map("Memora Season", [p.season for p in plans], "season_title")

	for p in plans:
		p["grade_title"] = grades.get(p.grade) or p.grade
		p["major_title"] = majors.get(p.major) or p.major
		p["season_title"] = seasons.get(p.season) or p.season

	return plans


@frappe.whitelist()
def get_plan_subjects(plan_id: str) -> list[dict]:
	"""Return the subjects attached to a given Academic Plan.

	Reads the plan's `plan_subjects` child rows (Memora Plan Subject) and
	enriches each with the subject's title/image from Memora Subject. The
	per-plan `alias_title` overrides the subject's own title when present.

	Args:
	    plan_id: Memora Academic Plan document name.

	Returns:
	    List of {subject, title, is_premium, image, in_linear, is_published}.
	"""
	_check_access()

	if not plan_id:
		return []

	if not frappe.db.exists("Memora Academic Plan", plan_id):
		frappe.throw(_("Plan {0} not found").format(plan_id))

	rows = frappe.get_all(
		"Memora Plan Subject",
		filters={"parent": plan_id, "parenttype": "Memora Academic Plan"},
		fields=["subject", "alias_title", "is_premium", "idx"],
		order_by="idx asc",
	)
	if not rows:
		return []

	subject_ids = [r.subject for r in rows if r.subject]
	subject_meta = {
		s.name: s
		for s in frappe.get_all(
			"Memora Subject",
			filters={"name": ["in", subject_ids]},
			fields=["name", "subject_title", "image", "in_linear", "is_published"],
		)
	}

	result = []
	for r in rows:
		meta = subject_meta.get(r.subject)
		if not meta:
			# Subject row pointing at a deleted/missing subject — skip safely.
			continue
		result.append(
			{
				"subject": r.subject,
				"title": r.alias_title or meta.subject_title,
				"is_premium": r.is_premium,
				"image": meta.image,
				"in_linear": meta.in_linear,
				"is_published": meta.is_published,
			}
		)
	return result


def _level_items(
	doctype: str,
	parent_field: str,
	parent_id: str | None,
	title_field: str,
	extra_fields: list[str] | None = None,
) -> list[dict]:
	"""Generic fetch for one hierarchy level (track/unit/topic/lesson).

	Returns every child (published or not) so admins can see drafts; each row
	gets a normalised `title` key derived from the level's title field.
	"""
	if not parent_id:
		return []

	fields = ["name", title_field, "is_published", "sort_order"] + (extra_fields or [])
	rows = frappe.get_all(
		doctype,
		filters={parent_field: parent_id},
		fields=fields,
		order_by="sort_order asc, idx asc",
	)
	for r in rows:
		r["title"] = r.get(title_field) or r["name"]
	return rows


@frappe.whitelist()
def get_subject_tracks(subject_id: str) -> list[dict]:
	"""Tracks belonging to a subject (wizard step 3)."""
	_check_access()
	return _level_items("Memora Track", "subject", subject_id, "track_title", ["is_linear"])


@frappe.whitelist()
def get_track_units(track_id: str) -> list[dict]:
	"""Units belonging to a track (wizard step 4)."""
	_check_access()
	return _level_items("Memora Unit", "track", track_id, "unit_title", ["is_linear", "is_free"])


@frappe.whitelist()
def get_unit_topics(unit_id: str) -> list[dict]:
	"""Topics belonging to a unit (wizard step 5)."""
	_check_access()
	return _level_items("Memora Topic", "unit", unit_id, "topic_title", ["is_linear", "is_free"])


@frappe.whitelist()
def get_topic_lessons(topic_id: str) -> list[dict]:
	"""Lessons belonging to a topic (wizard step 6 — leaf)."""
	_check_access()
	return _level_items(
		"Memora Lesson",
		"topic",
		topic_id,
		"lesson_title",
		["max_hearts", "bit_index", "is_reviewable"],
	)


@frappe.whitelist()
def save_order(level: str, parent_id: str, ordered_ids: str) -> dict:
	"""Persist a new ordering for one hierarchy level.

	`ordered_ids` is a JSON list of record ids in the desired order:
	  - track/unit/topic/lesson → each record's `sort_order` is rewritten to its
	    1-based position (same mechanism as the topic→lesson reorder dialog).
	  - subject → the plan's child rows are reordered via their `idx` (raw, so
	    it bypasses plan validation such as the ended-season guard).
	"""
	_check_access()

	if isinstance(ordered_ids, str):
		ordered_ids = json.loads(ordered_ids)
	ordered = list(dict.fromkeys(ordered_ids))  # de-dupe, preserve order

	if level == "subject":
		return _save_subject_order(parent_id, ordered)

	if level not in _ORDER_LEVELS:
		frappe.throw(_("Unknown level: {0}").format(level))

	doctype, parent_field = _ORDER_LEVELS[level]
	frappe.has_permission(doctype, "write", throw=True)

	# Guard against tampering: every id must belong to this parent.
	valid = set(frappe.get_all(doctype, filters={parent_field: parent_id}, pluck="name"))
	unknown = [n for n in ordered if n not in valid]
	if unknown:
		frappe.throw(_("Some items do not belong here: {0}").format(", ".join(unknown)))

	for position, name in enumerate(ordered, start=1):
		frappe.db.set_value(doctype, name, "sort_order", position, update_modified=False)

	return {"updated": len(ordered)}


def _save_subject_order(plan_id: str, ordered_subject_ids: list[str]) -> dict:
	"""Reorder a plan's subject child rows by rewriting their `idx`."""
	frappe.has_permission("Memora Academic Plan", "write", throw=True)

	rows = frappe.get_all(
		"Memora Plan Subject",
		filters={"parent": plan_id, "parenttype": "Memora Academic Plan"},
		fields=["name", "subject"],
	)
	by_subject = {r.subject: r.name for r in rows}

	unknown = [s for s in ordered_subject_ids if s not in by_subject]
	if unknown:
		frappe.throw(_("Some subjects do not belong to this plan: {0}").format(", ".join(unknown)))

	for position, subject_id in enumerate(ordered_subject_ids, start=1):
		frappe.db.set_value(
			"Memora Plan Subject", by_subject[subject_id], "idx", position, update_modified=False
		)

	return {"updated": len(ordered_subject_ids)}
