# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraAcademicPlan(Document):
	pass


@frappe.whitelist()
def get_grade_majors(doctype, txt, searchfield, start, page_len, filters):
	"""
	Return majors linked to the specified grade.
	Used by Plan form to filter Major dropdown.
	"""
	grade = filters.get("grade")
	if not grade:
		return []

	# Get majors from Grade's child table
	majors = frappe.get_all(
		"Memora Grade Major",
		filters={"parent": grade, "parenttype": "Memora Grade"},
		fields=["major"],
		pluck="major",
	)

	if not majors:
		return []

	# Build search query for Memora Major
	conditions = ["name IN %(majors)s"]
	if txt:
		conditions.append("major_title LIKE %(txt)s")

	return frappe.db.sql(
		f"""
		SELECT name, major_title
		FROM `tabMemora Major`
		WHERE {" AND ".join(conditions)}
		ORDER BY major_title
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"majors": majors,
			"txt": f"%{txt}%",
			"start": start,
			"page_len": page_len,
		},
	)
