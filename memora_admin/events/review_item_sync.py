"""Event handlers for syncing Review Item records on lesson save/delete.

Registered as doc_events on Memora Lesson in hooks.py.
"""

from __future__ import annotations

import frappe


def on_lesson_save(doc, method):
	"""Sync Review Items when a lesson is saved (on_update).

	Extracts items from non-skippable stages, upserts records,
	and deletes orphans.
	"""
	from memora_admin.api.review_items import sync_review_items

	try:
		result = sync_review_items(doc)
		if result["created"] or result["updated"] or result["deleted"]:
			frappe.logger().info(
				f"Review Item sync for {doc.name}: "
				f"created={result['created']}, updated={result['updated']}, deleted={result['deleted']}"
			)
	except Exception:
		frappe.log_error(f"Review Item sync failed for lesson {doc.name}")


def on_lesson_trash(doc, method):
	"""Delete all Review Items when a lesson is deleted (on_trash)."""
	from memora_admin.api.review_items import delete_review_items_for_lesson

	try:
		count = delete_review_items_for_lesson(doc.name)
		if count:
			frappe.logger().info(f"Deleted {count} Review Items for trashed lesson {doc.name}")
	except Exception:
		frappe.log_error(f"Review Item cleanup failed for trashed lesson {doc.name}")
