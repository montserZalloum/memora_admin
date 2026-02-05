"""Profile sync handlers for Frappe doc_events.

Sync profile updates to Redis cache for fast profile lookups in leaderboards.
Per CONTEXT.md: Profile cache invalidates within seconds, 1-hour TTL.
"""

import json
import time

import frappe

# Cache TTL: 1 hour per CONTEXT.md
CACHE_TTL = 3600


def on_player_profile_updated(doc, method):
	"""
	Push profile to Redis cache on create/update.

	Per CONTEXT.md:
	- Profile cache invalidates within seconds (push on update)
	- 1-hour TTL on cached profiles
	- Cache stores: {player_id, display_name, avatar}

	Also publishes invalidation message for any FastAPI instances
	that may have cached the profile in memory.
	"""
	cache = frappe.cache()
	redis_key = f"memora:profile:{doc.user}"

	# Build profile data
	profile_data = {
		"player_id": doc.user,
		"display_name": doc.display_name or "",
		"avatar": doc.avatar or "default_avatar",
	}

	# SET with TTL
	cache.set_value(redis_key, json.dumps(profile_data), expires_in_sec=CACHE_TTL)

	# Publish invalidation message for FastAPI ProfileService cache
	# This ensures any in-memory caches in FastAPI are also invalidated
	invalidation_msg = json.dumps({
		"type": "profile",
		"player_id": doc.user,
		"timestamp": time.time(),
	})
	cache.publish("memora:cache:invalidate", invalidation_msg)

	frappe.logger().info(f"Profile {doc.user} synced to Redis")
