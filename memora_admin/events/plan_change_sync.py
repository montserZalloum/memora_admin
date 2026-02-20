"""Plan change sync handler for session invalidation.

When admin changes a player's plan in Frappe Desk, invalidate their session
so they must re-login to get a new token with the updated plan_id.

Per CONTEXT.md: No graceful transition - immediate invalidation acceptable.
"""
# Player identity is PLAYER-##### docname (not email). See Phase 32.

import json
import time

import frappe

from memora_admin.api.utils import invalidate_player_season_seq
from memora_admin.events.access_sync import get_fastapi_redis


def on_player_profile_plan_changed(doc, method):
    """Invalidate player session when plan field changes.

    Per CONTEXT.md:
    - Immediate invalidation, player must re-login
    - No graceful transition needed

    Uses two-pronged invalidation (direct delete + pubsub),
    matching the established pattern in catalog_sync.py.
    """
    # Only act if plan field actually changed
    if not doc.has_value_changed("plan"):
        return

    # Invalidate cached season_seq (Frappe-side cache)
    invalidate_player_season_seq(doc.name)

    r = get_fastapi_redis()

    # 1. Direct delete: invalidate session + player_plan cache immediately
    # Key pattern matches SessionService: memora:session:{player_id}
    session_key = f"memora:session:{doc.name}"
    player_plan_key = f"memora:player_plan:{doc.name}"
    r.delete(session_key, player_plan_key)

    # 2. Pubsub: notify FastAPI in-process caches
    invalidation_msg = json.dumps({
        "type": "session",
        "player_id": doc.name,
        "reason": "plan_changed",
        "timestamp": time.time(),
    })
    r.publish("memora:cache:invalidate", invalidation_msg)

    frappe.logger().info(f"Session + player_plan cache invalidated for {doc.name} due to plan change")
