"""Plan change sync handler for session invalidation.

When admin changes a player's plan in Frappe Desk, invalidate their session
so they must re-login to get a new token with the updated plan_id.

Per CONTEXT.md: No graceful transition - immediate invalidation acceptable.
"""

import json
import time

import frappe


def on_player_profile_plan_changed(doc, method):
    """Invalidate player session when plan field changes.

    Per CONTEXT.md:
    - Immediate invalidation, player must re-login
    - No graceful transition needed

    Uses direct Redis key deletion (same as SessionService.invalidate_session).
    """
    # Only act if plan field actually changed
    if not doc.has_value_changed("plan"):
        return

    cache = frappe.cache()

    # Delete session key to invalidate all tokens
    # Key pattern matches SessionService: memora:session:{user_id}
    session_key = f"memora:session:{doc.user}"
    cache.delete_value(session_key)

    # Publish invalidation message for any FastAPI in-memory caches
    # (Though session validation is Redis-based, this is good practice)
    invalidation_msg = json.dumps({
        "type": "session",
        "player_id": doc.user,
        "reason": "plan_changed",
        "timestamp": time.time(),
    })
    cache.publish("memora:cache:invalidate", invalidation_msg)

    frappe.logger().info(f"Session invalidated for {doc.user} due to plan change")
