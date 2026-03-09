"""Shared helpers for Review Item aggregates used by Challenge/plan builders."""

import frappe


def get_question_counts_by_topic(subject_id: str) -> dict[str, int]:
	"""Return Review Item counts grouped by topic for a subject."""
	rows = frappe.db.sql(
		"""
		SELECT topic, COUNT(*) AS cnt
		FROM `tabMemora Review Item`
		WHERE subject = %(subject)s
		GROUP BY topic
		""",
		{"subject": subject_id},
		as_dict=True,
	)
	return {row["topic"]: int(row["cnt"]) for row in rows}
