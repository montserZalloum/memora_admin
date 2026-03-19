# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Redis cache sync for Memora Plan Premium doc_events.

Registered in hooks.py:
  "Memora Plan Premium": {
      "after_insert": "memora_admin.memora_admin.events.premium_sync.on_premium_created",
      "on_update": "memora_admin.memora_admin.events.premium_sync.on_premium_updated",
  }

Cache structure (Redis HASH at memora:premium:{player}:{plan}):
  usable:      "1" | "0"
  reason:      "none" | "plan_mismatch" | "season_ended" | "revoked"
  season_end:  "2026-06-30"
  source_type: "purchase" | "admin"
  premium_id:  "PP-00001"
"""

import frappe

from memora_admin.memora_admin.services.premium.access_check import is_plan_premium_usable


def on_premium_created(doc, method):
	"""Cache premium usability state in Redis after creation."""
	_sync_premium_cache(doc)


def on_premium_updated(doc, method):
	"""Invalidate/update premium cache on status change (e.g. revoke)."""
	if doc.has_value_changed("status"):
		_sync_premium_cache(doc)


def _sync_premium_cache(doc):
	"""Compute usability and write to Redis hash."""
	try:
		from fastapi_app.core.redis_keys import premium_key
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		key = premium_key(doc.player, doc.plan)

		check = is_plan_premium_usable(doc.player, doc.plan)

		mapping = {
			"usable": "1" if check.usable else "0",
			"reason": check.reason,
			"season_end": str(check.season_end) if check.season_end else "",
			"source_type": check.source_type or "",
			"premium_id": check.premium_id or "",
		}

		r.hset(key, mapping=mapping)
	except Exception:
		frappe.log_error("Failed to sync premium cache to Redis")


def invalidate_premium_cache(player: str, plan: str):
	"""Delete the premium cache key. Used by plan change and admin revoke."""
	try:
		from fastapi_app.core.redis_keys import premium_key
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		r.delete(premium_key(player, plan))
	except Exception:
		frappe.log_error("Failed to invalidate premium cache")


def on_player_plan_changed(doc, method):
	"""Invalidate premium cache when player changes plan (R-007, T035).

	Called via doc_events on Memora Player Profile on_update.
	Only fires when plan has actually changed.
	"""
	if not doc.has_value_changed("plan"):
		return

	old_plan = doc.get_doc_before_save().get("plan") if doc.get_doc_before_save() else None
	new_plan = doc.plan

	if old_plan:
		invalidate_premium_cache(doc.name, old_plan)
	if new_plan and new_plan != old_plan:
		invalidate_premium_cache(doc.name, new_plan)
