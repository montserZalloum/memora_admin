# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraAdminFilter(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("FILT-.#####.")

	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_majors_for_grade(doctype, txt, searchfield, start, page_len, filters):
	"""Return majors linked to a Grade via the Memora Grade Major child table."""
	grade = filters.get("grade")
	if not grade:
		return []

	return frappe.db.sql(
		"""
		SELECT m.name, m.major_title
		FROM `tabMemora Grade Major` gm
		INNER JOIN `tabMemora Major` m ON m.name = gm.major
		WHERE gm.parent = %(grade)s
			AND gm.parenttype = 'Memora Grade'
			AND (m.name LIKE %(txt)s OR m.major_title LIKE %(txt)s)
		ORDER BY m.major_title
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"grade": grade, "txt": f"%{txt}%", "page_len": page_len, "start": start},
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_subjects_for_plan(doctype, txt, searchfield, start, page_len, filters):
	"""Return subjects that belong to a specific Academic Plan."""
	plan = filters.get("plan")
	if not plan:
		return []

	return frappe.db.sql(
		"""
		SELECT ps.subject, sub.subject_title
		FROM `tabMemora Plan Subject` ps
		INNER JOIN `tabMemora Subject` sub ON sub.name = ps.subject
		WHERE ps.parent = %(plan)s
			AND ps.parenttype = 'Memora Academic Plan'
			AND (ps.subject LIKE %(txt)s OR sub.subject_title LIKE %(txt)s)
		ORDER BY sub.subject_title
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"plan": plan, "txt": f"%{txt}%", "page_len": page_len, "start": start},
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_tracks_for_plan(doctype, txt, searchfield, start, page_len, filters):
	"""Return tracks whose subject belongs to a specific Academic Plan."""
	plan = filters.get("plan")
	if not plan:
		return []

	return frappe.db.sql(
		"""
		SELECT t.name, t.track_title
		FROM `tabMemora Track` t
		INNER JOIN `tabMemora Plan Subject` ps ON ps.subject = t.subject
		WHERE ps.parent = %(plan)s
			AND ps.parenttype = 'Memora Academic Plan'
			AND (t.name LIKE %(txt)s OR t.track_title LIKE %(txt)s)
		ORDER BY t.track_title
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"plan": plan, "txt": f"%{txt}%", "page_len": page_len, "start": start},
	)


def _build_filters(doc, level):
	"""Build Frappe ORM filters for the given level using the most specific filter available."""
	filters = {}

	if level == "Lesson":
		if doc.topic:
			filters["topic"] = doc.topic
		elif doc.unit:
			filters["unit"] = doc.unit
		elif doc.track:
			filters["track"] = doc.track
		elif doc.subject:
			filters["subject"] = doc.subject
	elif level == "Topic":
		if doc.unit:
			filters["unit"] = doc.unit
		elif doc.track:
			filters["track"] = doc.track
		elif doc.subject:
			filters["subject"] = doc.subject
	elif level == "Unit":
		if doc.track:
			filters["track"] = doc.track
		elif doc.subject:
			filters["subject"] = doc.subject
	elif level == "Track":
		if doc.subject:
			filters["subject"] = doc.subject
	elif level == "Subject":
		# Subject filtering uses plan join — handled separately
		pass

	return filters


def _get_subjects_for_filter(doc, limit=2):
	"""Get subjects via Plan Subject join when academic_plan is set."""
	if not doc.academic_plan:
		return []

	return frappe.db.sql(
		"""
		SELECT ps.subject AS name, sub.subject_title AS title
		FROM `tabMemora Plan Subject` ps
		INNER JOIN `tabMemora Subject` sub ON sub.name = ps.subject
		WHERE ps.parent = %(plan)s
			AND ps.parenttype = 'Memora Academic Plan'
		ORDER BY sub.subject_title
		LIMIT %(limit)s
		""",
		{"plan": doc.academic_plan, "limit": limit},
		as_dict=True,
	)


def _get_tracks_for_filter(doc, limit=2):
	"""Get tracks via Plan Subject join when only academic_plan is set (no subject)."""
	if not doc.academic_plan:
		return []

	return frappe.db.sql(
		"""
		SELECT t.name, t.track_title AS title
		FROM `tabMemora Track` t
		INNER JOIN `tabMemora Plan Subject` ps ON ps.subject = t.subject
		WHERE ps.parent = %(plan)s
			AND ps.parenttype = 'Memora Academic Plan'
		ORDER BY t.track_title
		LIMIT %(limit)s
		""",
		{"plan": doc.academic_plan, "limit": limit},
		as_dict=True,
	)


@frappe.whitelist()
def get_picker_items(level, parent_value="", plan=""):
	"""Return items for a given hierarchy level for the cascading button picker.

	Args:
		level: One of "subject", "track", "unit", "topic"
		parent_value: The parent item name (e.g., subject name for track level)
		plan: Academic Plan name (required for subject level, fallback for track level)

	Returns:
		List of dicts with name, title, sort_order
	"""
	level = level.lower()

	if level == "subject":
		if not plan:
			return []
		return frappe.db.sql(
			"""
			SELECT ps.subject AS name, sub.subject_title AS title, sub.sort_order
			FROM `tabMemora Plan Subject` ps
			INNER JOIN `tabMemora Subject` sub ON sub.name = ps.subject
			WHERE ps.parent = %(plan)s
				AND ps.parenttype = 'Memora Academic Plan'
			ORDER BY sub.sort_order, sub.subject_title
			""",
			{"plan": plan},
			as_dict=True,
		)

	if level == "track":
		if parent_value:
			return frappe.get_all(
				"Memora Track",
				filters={"subject": parent_value},
				fields=["name", "track_title as title", "sort_order"],
				order_by="sort_order, track_title",
			)
		if plan:
			return frappe.db.sql(
				"""
				SELECT t.name, t.track_title AS title, t.sort_order
				FROM `tabMemora Track` t
				INNER JOIN `tabMemora Plan Subject` ps ON ps.subject = t.subject
				WHERE ps.parent = %(plan)s
					AND ps.parenttype = 'Memora Academic Plan'
				ORDER BY t.sort_order, t.track_title
				""",
				{"plan": plan},
				as_dict=True,
			)
		return []

	if level == "unit":
		if not parent_value:
			return []
		return frappe.get_all(
			"Memora Unit",
			filters={"track": parent_value},
			fields=["name", "unit_title as title", "sort_order"],
			order_by="sort_order, unit_title",
		)

	if level == "topic":
		if not parent_value:
			return []
		return frappe.get_all(
			"Memora Topic",
			filters={"unit": parent_value},
			fields=["name", "topic_title as title", "sort_order"],
			order_by="sort_order, topic_title",
		)

	return []


@frappe.whitelist()
def test_filter(academic_plan="", subject="", track="", unit="", topic="", level=""):
	"""Test the filter by querying sample records at each content level."""
	doc = frappe._dict(
		academic_plan=academic_plan,
		subject=subject,
		track=track,
		unit=unit,
		topic=topic,
	)

	level_config = {
		"Subject": {
			"doctype": "Memora Subject",
			"title_field": "subject_title",
		},
		"Track": {
			"doctype": "Memora Track",
			"title_field": "track_title",
		},
		"Unit": {
			"doctype": "Memora Unit",
			"title_field": "unit_title",
		},
		"Topic": {
			"doctype": "Memora Topic",
			"title_field": "topic_title",
		},
		"Lesson": {
			"doctype": "Memora Lesson",
			"title_field": "lesson_title",
		},
	}

	level_colors = {
		"Subject": "#2490ef",
		"Track": "#ed8e1b",
		"Unit": "#29cd42",
		"Topic": "#7c5de4",
		"Lesson": "#e84d5a",
	}

	levels = [level] if level else ["Subject", "Track", "Unit", "Topic", "Lesson"]
	rows = []

	for lvl in levels:
		cfg = level_config[lvl]

		if lvl == "Subject":
			if doc.subject:
				title = frappe.db.get_value("Memora Subject", doc.subject, "subject_title") or ""
				samples = [{"name": doc.subject, "title": title}]
			elif doc.academic_plan:
				samples = _get_subjects_for_filter(doc, limit=2)
			else:
				samples = []
			rows.append((lvl, samples))
			continue

		if lvl == "Track" and not _build_filters(doc, lvl):
			if doc.academic_plan:
				samples = _get_tracks_for_filter(doc, limit=2)
			else:
				samples = []
			rows.append((lvl, samples))
			continue

		filters = _build_filters(doc, lvl)
		samples = frappe.get_all(
			cfg["doctype"],
			filters=filters,
			fields=["name", cfg["title_field"] + " as title"],
			order_by=cfg["title_field"],
			limit_page_length=2,
		)
		rows.append((lvl, samples))

	# Build HTML table
	badge_style = (
		"display:inline-block;padding:3px 10px;margin:2px 4px;border-radius:12px;"
		"font-size:12px;color:#fff;font-weight:500;"
	)
	html = '<table class="table table-bordered table-sm" style="margin-top:10px;">'
	html += "<thead><tr><th>Level</th><th>Samples (up to 2)</th></tr></thead><tbody>"
	for lvl, samples in rows:
		color = level_colors[lvl]
		level_badge = f'<span style="{badge_style}background-color:{color};">{lvl}</span>'
		if samples:
			sample_badges = " ".join(
				f'<span style="{badge_style}background-color:{color};">'
				f"{s.get('title', s.get('name', ''))}</span>"
				for s in samples
			)
		else:
			sample_badges = '<span style="color:#999;">-</span>'
		html += f"<tr><td>{level_badge}</td><td>{sample_badges}</td></tr>"
	html += "</tbody></table>"

	return html
