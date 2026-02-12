# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraAdminFilter(Document):
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
def test_filter(filter_name, level=""):
	"""Test the filter by querying sample records at each content level."""
	doc = frappe.get_doc("Memora Admin Filter", filter_name)

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

	levels = [level] if level else ["Subject", "Track", "Unit", "Topic", "Lesson"]
	rows = []

	for lvl in levels:
		cfg = level_config[lvl]

		if lvl == "Subject":
			# Subject uses plan join
			samples = _get_subjects_for_filter(doc, limit=2)
			if not doc.academic_plan:
				# No plan set — count all subjects
				total = frappe.db.count("Memora Subject")
				sample_names = []
			else:
				total = frappe.db.sql(
					"""
					SELECT COUNT(*) FROM `tabMemora Plan Subject`
					WHERE parent = %s AND parenttype = 'Memora Academic Plan'
					""",
					doc.academic_plan,
				)[0][0]
				sample_names = [f"{s.name} ({s.title})" for s in samples]
			rows.append((lvl, total, ", ".join(sample_names) if sample_names else "-"))
			continue

		if lvl == "Track" and not _build_filters(doc, lvl):
			# No direct filter — try plan join
			samples = _get_tracks_for_filter(doc, limit=2)
			if doc.academic_plan:
				total = frappe.db.sql(
					"""
					SELECT COUNT(*)
					FROM `tabMemora Track` t
					INNER JOIN `tabMemora Plan Subject` ps ON ps.subject = t.subject
					WHERE ps.parent = %s AND ps.parenttype = 'Memora Academic Plan'
					""",
					doc.academic_plan,
				)[0][0]
			else:
				total = frappe.db.count("Memora Track")
				samples = []
			sample_names = [f"{s.name} ({s.title})" for s in samples]
			rows.append((lvl, total, ", ".join(sample_names) if sample_names else "-"))
			continue

		filters = _build_filters(doc, lvl)
		total = frappe.db.count(cfg["doctype"], filters)
		samples = frappe.get_all(
			cfg["doctype"],
			filters=filters,
			fields=["name", cfg["title_field"] + " as title"],
			order_by=cfg["title_field"],
			limit_page_length=2,
		)
		sample_names = [f"{s.name} ({s.title})" for s in samples]
		rows.append((lvl, total, ", ".join(sample_names) if sample_names else "-"))

	# Build HTML table
	html = '<table class="table table-bordered table-sm">'
	html += "<thead><tr><th>Level</th><th>Total</th><th>Samples</th></tr></thead><tbody>"
	for lvl, total, samples_str in rows:
		html += f"<tr><td>{lvl}</td><td>{total}</td><td>{samples_str}</td></tr>"
	html += "</tbody></table>"

	return html
