# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
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
def get_topic_lessons(topic):
	"""Return lessons for a topic ordered by sort_order (then title) for the reorder dialog."""
	if not topic:
		return []

	frappe.has_permission("Memora Lesson", "read", throw=True)

	return frappe.get_all(
		"Memora Lesson",
		filters={"topic": topic},
		fields=["name", "lesson_title", "sort_order", "is_published"],
		order_by="sort_order asc, lesson_title asc",
	)


@frappe.whitelist()
def save_lesson_order(topic, ordered_lessons):
	"""Persist a new lesson ordering for a topic.

	`ordered_lessons` is a JSON-encoded list of lesson names in the desired order.
	Each lesson's `sort_order` is rewritten to its 1-based position.
	"""
	if not topic:
		frappe.throw(_("Topic is required."))

	frappe.has_permission("Memora Lesson", "write", throw=True)

	if isinstance(ordered_lessons, str):
		ordered_lessons = json.loads(ordered_lessons)

	# Guard against tampering: every submitted lesson must belong to this topic.
	valid_names = set(
		frappe.get_all(
			"Memora Lesson", filters={"topic": topic}, pluck="name"
		)
	)
	submitted = list(dict.fromkeys(ordered_lessons))  # de-dupe, preserve order
	unknown = [name for name in submitted if name not in valid_names]
	if unknown:
		frappe.throw(_("Some lessons do not belong to this topic: {0}").format(", ".join(unknown)))

	for position, lesson_name in enumerate(submitted, start=1):
		frappe.db.set_value("Memora Lesson", lesson_name, "sort_order", position, update_modified=False)

	return {"updated": len(submitted)}


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
