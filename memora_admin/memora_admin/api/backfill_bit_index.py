"""
One-time backfill script to assign bit_index values to existing lessons.

Assigns bit_index in the same traversal order as the hierarchy API:
  Track (idx asc) -> Unit (idx asc) -> Topic (idx asc) -> Lesson (idx asc)

This ensures consistency between DB-stored bit_index and the runtime
hierarchy allocation. Also updates each subject's last_bit_index counter.

Usage:
  bench --site {site} execute memora_admin.memora_admin.api.backfill_bit_index.backfill_all
"""

import frappe


def backfill_all():
	"""Backfill bit_index for all lessons across all subjects."""
	subjects = frappe.get_all("Memora Subject", fields=["name", "subject_title"])

	if not subjects:
		print("No subjects found.")
		return

	for subject in subjects:
		backfill_subject(subject.name)

	frappe.db.commit()
	print("\nBackfill complete. Changes committed.")


def backfill_subject(subject_id: str):
	"""
	Backfill bit_index for all lessons in a subject.

	Traverses the hierarchy in the same order as hierarchy.py:
	Track (idx asc) -> Unit (idx asc) -> Topic (idx asc) -> Lesson (idx asc)
	"""
	print(f"\n--- Subject: {subject_id} ---")

	bit_index = 0

	tracks = frappe.get_all(
		"Memora Track",
		filters={"subject": subject_id},
		fields=["name"],
		order_by="idx asc",
	)

	for track in tracks:
		units = frappe.get_all(
			"Memora Unit",
			filters={"track": track.name},
			fields=["name"],
			order_by="idx asc",
		)

		for unit in units:
			topics = frappe.get_all(
				"Memora Topic",
				filters={"unit": unit.name},
				fields=["name"],
				order_by="idx asc",
			)

			for topic in topics:
				lessons = frappe.get_all(
					"Memora Lesson",
					filters={"topic": topic.name},
					fields=["name"],
					order_by="idx asc",
				)

				for lesson in lessons:
					frappe.db.set_value(
						"Memora Lesson",
						lesson.name,
						"bit_index",
						bit_index,
						update_modified=False,
					)
					print(f"  {lesson.name} -> bit_index={bit_index}")
					bit_index += 1

	# Update the subject's counter to the next available index
	frappe.db.set_value(
		"Memora Subject",
		subject_id,
		"last_bit_index",
		bit_index,
		update_modified=False,
	)
	print(f"  Subject {subject_id}: last_bit_index={bit_index} ({bit_index} lessons)")
