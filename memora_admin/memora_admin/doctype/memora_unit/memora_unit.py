# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraUnit(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def unit_query(doctype, txt, searchfield, start, page_len, filters):
	"""Default link search: shows track, subject, grades, and majors alongside the unit."""
	conditions = []
	if txt:
		conditions.append(
			"(u.name LIKE %(txt)s OR u.unit_title LIKE %(txt)s"
			" OR t.track_title LIKE %(txt)s OR sub.subject_title LIKE %(txt)s)"
		)

	where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

	return frappe.db.sql(
		f"""
		SELECT u.name, u.unit_title, t.track_title, sub.subject_title,
		       GROUP_CONCAT(DISTINCT g.grade_title ORDER BY g.grade_title SEPARATOR ', ') AS grades,
		       GROUP_CONCAT(DISTINCT NULLIF(m.major_title, '') ORDER BY m.major_title SEPARATOR ', ') AS majors
		FROM `tabMemora Unit` u
		LEFT JOIN `tabMemora Track` t ON t.name = u.track
		LEFT JOIN `tabMemora Subject` sub ON sub.name = u.subject
		LEFT JOIN `tabMemora Subject Applicability` sa
			ON sa.parent = sub.name AND sa.parenttype = 'Memora Subject'
		LEFT JOIN `tabMemora Grade` g ON g.name = sa.grade
		LEFT JOIN `tabMemora Major` m ON m.name = sa.major
		{where}
		GROUP BY u.name, u.unit_title, t.track_title, sub.subject_title
		ORDER BY u.unit_title
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"txt": f"%{txt}%", "page_len": page_len, "start": start},
	)
