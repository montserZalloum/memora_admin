# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraTrack(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("Track-.#####.")

	def validate(self):
		if not self.is_new() and self.has_value_changed("subject"):
			self._cascade_subject_change()

	def _cascade_subject_change(self):
		"""When a track moves to a different subject, update all children."""
		new_subject = self.subject
		track_name = self.name

		frappe.db.sql(
			"""UPDATE `tabMemora Unit` SET subject = %s
			   WHERE track = %s AND subject != %s""",
			(new_subject, track_name, new_subject),
		)
		frappe.db.sql(
			"""UPDATE `tabMemora Topic` SET subject = %s
			   WHERE track = %s AND subject != %s""",
			(new_subject, track_name, new_subject),
		)
		frappe.db.sql(
			"""UPDATE `tabMemora Lesson` SET subject = %s
			   WHERE track = %s AND subject != %s""",
			(new_subject, track_name, new_subject),
		)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def track_query(doctype, txt, searchfield, start, page_len, filters):
	"""Default link search: shows subject title alongside the track."""
	conditions = []
	if txt:
		conditions.append(
			"(t.name LIKE %(txt)s OR t.track_title LIKE %(txt)s OR sub.subject_title LIKE %(txt)s)"
		)

	where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

	return frappe.db.sql(
		f"""
		SELECT t.name, t.track_title, sub.subject_title,
		       GROUP_CONCAT(DISTINCT g.grade_title ORDER BY g.grade_title SEPARATOR ', ') AS grades,
		       GROUP_CONCAT(DISTINCT NULLIF(m.major_title, '') ORDER BY m.major_title SEPARATOR ', ') AS majors
		FROM `tabMemora Track` t
		LEFT JOIN `tabMemora Subject` sub ON sub.name = t.subject
		LEFT JOIN `tabMemora Subject Applicability` sa
			ON sa.parent = sub.name AND sa.parenttype = 'Memora Subject'
		LEFT JOIN `tabMemora Grade` g ON g.name = sa.grade
		LEFT JOIN `tabMemora Major` m ON m.name = sa.major
		{where}
		GROUP BY t.name, t.track_title, sub.subject_title
		ORDER BY t.track_title
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"txt": f"%{txt}%", "page_len": page_len, "start": start},
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
