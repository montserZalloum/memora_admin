"""Profile sync handlers for Frappe doc_events.

Sync profile updates to Redis cache for fast profile lookups in leaderboards.
Per CONTEXT.md: Profile cache invalidates within seconds, 1-hour TTL.
"""
# Player identity is PLAYER-##### docname (not email). See Phase 32.

import json
import time

import frappe

from fastapi_app.core.redis_keys import cache_invalidation_channel, profile_key
from memora_admin.utils.redis_connection import get_memora_redis

# Cache TTL: 1 hour per CONTEXT.md
CACHE_TTL = 3600


def on_player_profile_updated(doc, method):
	"""
	Push profile to Redis cache on create/update.

	Per CONTEXT.md:
	- Profile cache invalidates within seconds (push on update)
	- 1-hour TTL on cached profiles
	- Cache stores: {player_id, display_name, avatar}

	Uses two-pronged invalidation (direct SET + pubsub),
	matching the established pattern in catalog_sync.py.
	"""
	r = get_memora_redis()
	redis_key = profile_key(doc.name)

	# Build profile data
	profile_data = {
		"player_id": doc.name,
		"display_name": doc.display_name or "",
		"avatar": doc.avatar or "default_avatar",
	}

	# 1. Direct SET with TTL (immediate effect)
	r.set(redis_key, json.dumps(profile_data), ex=CACHE_TTL)

	# 2. Pubsub: notify FastAPI in-process caches
	invalidation_msg = json.dumps(
		{
			"type": "profile",
			"player_id": doc.name,
			"timestamp": time.time(),
		}
	)
	r.publish(cache_invalidation_channel(), invalidation_msg)

	frappe.logger().info(f"Profile {doc.name} synced to Redis")
