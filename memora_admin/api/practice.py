"""Frappe API for Practice Arena hierarchy metadata."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_practice_hierarchy_meta(subject_id: str) -> dict | None:
	"""Get titles and Review Item counts for a subject's practice hierarchy.

	Returns a flat lookup structure (NOT nested) for fast cache-and-merge
	with the existing SubjectHierarchy structure in FastAPI.

	Returns:
	    {
	        "subject_title": "الرياضيات",
	        "tracks": {"TRK-00001": {"title": "الجبر"}},
	        "units": {"UNI-00001": {"title": "المعادلات", "track": "TRK-00001"}},
	        "topics": {"TOP-00001": {"title": "المعادلات الخطية", "unit": "UNI-00001"}},
	        "item_counts": {"TOP-00001": 45, "TOP-00002": 35},
	    }
	"""
	if not frappe.db.exists("Memora Subject", subject_id):
		return None

	subject_title = frappe.db.get_value("Memora Subject", subject_id, "subject_title") or subject_id

	tracks = frappe.get_all(
		"Memora Track",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "track_title"],
		order_by="idx asc",
	)

	units = frappe.get_all(
		"Memora Unit",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "unit_title", "track"],
		order_by="idx asc",
	)

	topics = frappe.get_all(
		"Memora Topic",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "topic_title", "unit"],
		order_by="idx asc",
	)

	# Review Item counts grouped by topic
	item_counts_raw = frappe.db.sql(
		"""
		SELECT topic, COUNT(*) as cnt
		FROM `tabMemora Review Item`
		WHERE subject = %s
		GROUP BY topic
		""",
		subject_id,
		as_dict=True,
	)

	return {
		"subject_title": subject_title,
		"tracks": {t.name: {"title": t.track_title or t.name} for t in tracks},
		"units": {u.name: {"title": u.unit_title or u.name, "track": u.track} for u in units},
		"topics": {t.name: {"title": t.topic_title or t.name, "unit": t.unit} for t in topics},
		"item_counts": {r.topic: r.cnt for r in item_counts_raw},
	}


@frappe.whitelist(allow_guest=False)
def execute_practice_query(sql: str, params: list | None = None) -> list[dict]:
	"""Execute a read-only practice query and return results.

	Used by FastAPI PracticeService for question selection queries
	(SELECT from Review Item + Practice Log).

	Restricted to System Manager — accepts raw SQL.
	"""
	frappe.only_for("System Manager")
	return frappe.db.sql(sql, tuple(params) if params else (), as_dict=True)


@frappe.whitelist(allow_guest=False)
def execute_practice_log_upsert(sql: str, params: list | None = None) -> None:
	"""Execute a Practice Log INSERT...ON DUPLICATE KEY UPDATE.

	Used by FastAPI PracticeService for batch submit persistence.
	Commits after execution.

	Restricted to System Manager — accepts raw SQL.
	"""
	frappe.only_for("System Manager")
	frappe.db.sql(sql, tuple(params) if params else ())
	frappe.db.commit()
