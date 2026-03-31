# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraTopic(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("TPC-.#####.")

	def validate(self):
		if not self.is_new() and self.has_value_changed("unit"):
			self._cascade_unit_change()

	def _cascade_unit_change(self):
		"""When a topic moves to a different unit, resolve track+subject and cascade to lessons."""
		new_unit = self.unit
		unit_data = frappe.db.get_value(
			"Memora Unit", new_unit, ["track", "subject"], as_dict=True
		)

		if unit_data:
			self.track = unit_data.track
			self.subject = unit_data.subject

			frappe.db.sql(
				"""UPDATE `tabMemora Lesson`
				   SET unit = %s, track = %s, subject = %s
				   WHERE topic = %s
				     AND (unit != %s OR track != %s OR subject != %s)""",
				(
					new_unit, unit_data.track, unit_data.subject,
					self.name,
					new_unit, unit_data.track, unit_data.subject,
				),
			)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def topic_query(doctype, txt, searchfield, start, page_len, filters):
	"""Default link search: shows unit, track, subject, grades, and majors alongside the topic."""
	conditions = []
	if txt:
		conditions.append(
			"(tp.name LIKE %(txt)s OR tp.topic_title LIKE %(txt)s"
			" OR u.unit_title LIKE %(txt)s OR t.track_title LIKE %(txt)s"
			" OR sub.subject_title LIKE %(txt)s)"
		)

	where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

	return frappe.db.sql(
		f"""
		SELECT tp.name, tp.topic_title, u.unit_title, t.track_title, sub.subject_title,
		       GROUP_CONCAT(DISTINCT g.grade_title ORDER BY g.grade_title SEPARATOR ', ') AS grades,
		       GROUP_CONCAT(DISTINCT NULLIF(m.major_title, '') ORDER BY m.major_title SEPARATOR ', ') AS majors
		FROM `tabMemora Topic` tp
		LEFT JOIN `tabMemora Unit` u ON u.name = tp.unit
		LEFT JOIN `tabMemora Track` t ON t.name = tp.track
		LEFT JOIN `tabMemora Subject` sub ON sub.name = tp.subject
		LEFT JOIN `tabMemora Subject Applicability` sa
			ON sa.parent = sub.name AND sa.parenttype = 'Memora Subject'
		LEFT JOIN `tabMemora Grade` g ON g.name = sa.grade
		LEFT JOIN `tabMemora Major` m ON m.name = sa.major
		{where}
		GROUP BY tp.name, tp.topic_title, u.unit_title, t.track_title, sub.subject_title
		ORDER BY tp.topic_title
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"txt": f"%{txt}%", "page_len": page_len, "start": start},
	)
