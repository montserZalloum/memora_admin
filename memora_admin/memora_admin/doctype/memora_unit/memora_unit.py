# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraUnit(Document):
	def validate(self):
		if not self.is_new() and self.has_value_changed("track"):
			self._cascade_track_change()

	def _cascade_track_change(self):
		"""When a unit moves to a different track, resolve subject from the new track and cascade."""
		new_track = self.track
		new_subject = frappe.db.get_value("Memora Track", new_track, "subject")

		if new_subject:
			self.subject = new_subject

		unit_name = self.name

		frappe.db.sql(
			"""UPDATE `tabMemora Topic`
			   SET track = %s, subject = %s
			   WHERE unit = %s AND (track != %s OR subject != %s)""",
			(new_track, new_subject, unit_name, new_track, new_subject),
		)
		frappe.db.sql(
			"""UPDATE `tabMemora Lesson`
			   SET track = %s, subject = %s
			   WHERE unit = %s AND (track != %s OR subject != %s)""",
			(new_track, new_subject, unit_name, new_track, new_subject),
		)


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
