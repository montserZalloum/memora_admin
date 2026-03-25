# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraSubject(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def subject_query(doctype, txt, searchfield, start, page_len, filters):
	"""Default link search: shows aggregated grades and majors alongside the subject."""
	conditions = []
	if txt:
		conditions.append("(sub.name LIKE %(txt)s OR sub.subject_title LIKE %(txt)s)")

	where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

	return frappe.db.sql(
		f"""
		SELECT sub.name, sub.subject_title,
		       GROUP_CONCAT(DISTINCT g.grade_title ORDER BY g.grade_title SEPARATOR ', ') AS grades,
		       GROUP_CONCAT(DISTINCT NULLIF(m.major_title, '') ORDER BY m.major_title SEPARATOR ', ') AS majors
		FROM `tabMemora Subject` sub
		LEFT JOIN `tabMemora Subject Applicability` sa
			ON sa.parent = sub.name AND sa.parenttype = 'Memora Subject'
		LEFT JOIN `tabMemora Grade` g ON g.name = sa.grade
		LEFT JOIN `tabMemora Major` m ON m.name = sa.major
		{where}
		GROUP BY sub.name, sub.subject_title
		ORDER BY sub.subject_title
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"txt": f"%{txt}%", "page_len": page_len, "start": start},
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_applicable_subjects(doctype, txt, searchfield, start, page_len, filters):
	grade = filters.get("grade")
	major = filters.get("major")
	if not grade:
		return []

	conditions = [
		"sa.parent = sub.name",
		"sa.parenttype = 'Memora Subject'",
		"sa.grade = %(grade)s",
	]
	if major:
		conditions.append("(sa.major = %(major)s OR sa.major IS NULL OR sa.major = '')")
	if txt:
		conditions.append("(sub.name LIKE %(txt)s OR sub.subject_title LIKE %(txt)s)")

	return frappe.db.sql(
		f"""
		SELECT DISTINCT sub.name, sub.subject_title
		FROM `tabMemora Subject` sub
		INNER JOIN `tabMemora Subject Applicability` sa ON sa.parent = sub.name
		WHERE {" AND ".join(conditions)}
		ORDER BY sub.subject_title
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"grade": grade, "major": major, "txt": f"%{txt}%", "page_len": page_len, "start": start},
	)
