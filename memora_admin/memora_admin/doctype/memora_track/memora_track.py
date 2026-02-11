# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraTrack(Document):
	pass


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
