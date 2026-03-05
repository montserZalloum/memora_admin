"""Cache invalidation for Memora Announcement DocType.

Wired via hooks.py doc_events on after_insert, on_update, on_trash.
Uses two-pronged pattern: direct DEL + pubsub publish.
"""

import json

import frappe

from fastapi_app.core.redis_keys import announcements_active_key, cache_invalidation_channel


def on_announcement_changed(doc, method):
	"""Invalidate announcements cache when any announcement is created/updated/deleted."""
	_invalidate_announcements_cache()


def _invalidate_announcements_cache():
	"""Two-pronged cache invalidation for announcements.

	1. Direct Redis DEL for immediate effect
	2. Pubsub publish so FastAPI sidecar invalidates in-process
	"""
	from memora_admin.utils.redis_connection import get_memora_redis

	try:
		r = get_memora_redis()

		# 1. Direct cache delete
		r.delete(announcements_active_key())

		# 2. Pubsub notification for FastAPI sidecar
		r.publish(
			cache_invalidation_channel(),
			json.dumps(
				{
					"type": "announcements",
					"timestamp": str(frappe.utils.now()),
				}
			),
		)

	except Exception:
		pass
