# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Redis cache sync for Memora Live Event Access doc_events.

Registered in hooks.py:
  "Memora Live Event Access": {
      "after_insert": "memora_admin.memora_admin.events.event_access_sync.on_event_access_created",
      "on_update": "memora_admin.memora_admin.events.event_access_sync.on_event_access_updated",
  }

Cache structure (Redis HASH at memora:event_access:{player}:{event}):
  has_access:  "1" | "0"
  access_type: "purchase" | "voucher" | "admin"
  access_id:   "LEA-00001"
"""

import frappe


def on_event_access_created(doc, method):
	"""Cache event access state in Redis after creation."""
	_sync_event_access_cache(doc)


def on_event_access_updated(doc, method):
	"""Invalidate/update event access cache on status change (revoke/refund)."""
	if doc.has_value_changed("status"):
		_sync_event_access_cache(doc)


def _sync_event_access_cache(doc):
	"""Write event access state to Redis hash."""
	try:
		from fastapi_app.core.redis_keys import event_access_key
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		key = event_access_key(doc.player, doc.event)

		is_active = doc.status == "active"
		mapping = {
			"has_access": "1" if is_active else "0",
			"access_type": doc.access_type or "",
			"access_id": doc.name,
		}

		r.hset(key, mapping=mapping)
	except Exception:
		frappe.log_error("Failed to sync event access cache to Redis")


def invalidate_event_access_cache(player: str, event: str):
	"""Delete the event access cache key. Used by admin revoke/refund."""
	try:
		from fastapi_app.core.redis_keys import event_access_key
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		r.delete(event_access_key(player, event))
	except Exception:
		frappe.log_error("Failed to invalidate event access cache")
