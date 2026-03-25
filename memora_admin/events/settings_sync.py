"""Gamification settings sync: Frappe → Redis on save.

Eagerly writes settings to Redis and publishes invalidation so the
FastAPI sidecar drops any stale in-process copy.

Follows two-pronged pattern: direct SET + pubsub publish.
"""

import json

import frappe

from fastapi_app.core.redis_keys import cache_invalidation_channel, gamification_settings_key
from memora_admin.utils.redis_connection import get_memora_redis


def on_settings_updated(doc, method):
	"""Push gamification settings to Redis when Memora Settings is saved."""
	try:
		payload = json.dumps(
			{
				"base_lesson_xp": doc.base_lesson_xp if doc.base_lesson_xp is not None else 100,
				"replay_xp": doc.replay_xp if doc.replay_xp is not None else 25,
				"max_streak_multiplier_percent": doc.max_streak_multiplier_percent
				if doc.max_streak_multiplier_percent is not None
				else 50,
				"max_devices_per_player": doc.max_devices_per_player
				if doc.max_devices_per_player is not None
				else 3,
				"default_max_hearts": doc.default_max_hearts if doc.default_max_hearts is not None else 5,
				"xp_per_heart": doc.xp_per_heart if doc.xp_per_heart is not None else 0,
				"session_timeout_days": doc.session_timeout_days
				if doc.session_timeout_days is not None
				else 30,
				"review_session_size": doc.review_session_size
				if doc.review_session_size is not None
				else 10,
			}
		)

		r = get_memora_redis()

		# 1. Eagerly write new values (no TTL — persistent until next save)
		r.set(gamification_settings_key(), payload)

		# 2. Pubsub notification so FastAPI sidecar invalidates in-process cache
		r.publish(
			cache_invalidation_channel(),
			json.dumps(
				{
					"type": "gamification_settings",
					"timestamp": str(frappe.utils.now()),
				}
			),
		)

		frappe.logger().info("Gamification settings synced to Redis")
	except Exception as e:
		frappe.logger().error(f"Failed to sync gamification settings to Redis: {e}")
