"""Event handlers for syncing Review Item records on lesson save/delete.

Registered as doc_events on Memora Lesson in hooks.py.

Phase 035: Switched from synchronous extraction to dirty-set pattern.
on_lesson_save enqueues lesson name to Redis SET; a scheduled consumer
(sync_dirty_review_items, every 2 min) processes the queue with retry semantics.
"""

from __future__ import annotations

import frappe

from fastapi_app.core.redis_keys import dirty_review_items_key

DIRTY_KEY = dirty_review_items_key()


def on_lesson_save(doc, method):
	"""Enqueue lesson for Review Item extraction via dirty set.

	Always adds to dirty set so the scheduled consumer will process it.
	If is_reviewable=0, also performs immediate deletion of existing
	Review Items (students should not see items from a non-reviewable lesson).
	"""
	from memora_admin.utils.redis_connection import get_memora_redis

	r = get_memora_redis()
	r.sadd(DIRTY_KEY, doc.name)

	if not doc.is_reviewable:
		from memora_admin.api.review_items import delete_review_items_for_lesson

		try:
			count = delete_review_items_for_lesson(doc.name)
			if count:
				frappe.logger().info(
					f"Review Item cleanup for non-reviewable lesson {doc.name}: deleted={count}"
				)
		except Exception:
			frappe.log_error(f"Review Item cleanup failed for lesson {doc.name}")


def on_lesson_trash(doc, method):
	"""Delete all Review Items when a lesson is deleted (on_trash).

	Also SREMs from dirty set to prevent the consumer from processing
	a deleted lesson.
	"""
	from memora_admin.utils.redis_connection import get_memora_redis

	r = get_memora_redis()
	r.srem(DIRTY_KEY, doc.name)

	from memora_admin.api.review_items import delete_review_items_for_lesson

	try:
		count = delete_review_items_for_lesson(doc.name)
		if count:
			frappe.logger().info(f"Deleted {count} Review Items for trashed lesson {doc.name}")
	except Exception:
		frappe.log_error(f"Review Item cleanup failed for trashed lesson {doc.name}")
