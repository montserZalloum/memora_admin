"""Access sync handlers for Frappe doc_events.

Sync subscription and season changes to Redis for O(1) access checks.
Per CONTEXT.md: immediate sync, sub-second propagation.
"""

import frappe


# =============================================================================
# Season Handlers (Gate 1)
# =============================================================================


def on_season_updated(doc, method):
    """
    Sync season metadata to Redis on create/update.

    Per CONTEXT.md:
    - Gate 1 validates season is active and not expired
    - Uses Redis hash for atomic multi-field updates
    """
    cache = frappe.cache
    redis_key = f"memora:season:{doc.name}"

    # Use single hset with mapping for atomic update
    cache.hset(
        redis_key,
        mapping={
            "is_published": "1" if doc.is_published else "0",
            "start_date": str(doc.start_date),
            "end_date": str(doc.end_date),
        },
    )

    frappe.logger().info(f"Season {doc.name} synced to Redis")


def on_season_deleted(doc, method):
    """Remove season from Redis cache when deleted."""
    cache = frappe.cache
    redis_key = f"memora:season:{doc.name}"
    cache.delete_value(redis_key)

    frappe.logger().info(f"Season {doc.name} removed from Redis")


# =============================================================================
# Subscription Handlers (Gate 2)
# =============================================================================


def on_subscription_change(doc, method):
    """
    Sync subscription grant to Redis on create/update.

    Per CONTEXT.md:
    - Immediate sync (sub-second propagation)
    - Add grant if is_active, remove if not
    - Grants are additive and permanent until revoked
    """
    player_id = doc.player
    access_key = doc.access_key

    # Get player's user_id from Player Profile (if player field is docname)
    # If player field is already user_id, use directly
    try:
        if frappe.db.exists("Memora Player Profile", player_id):
            player_doc = frappe.get_doc("Memora Player Profile", player_id)
            user_id = player_doc.user
        else:
            # Assume player field contains user_id directly
            user_id = player_id
    except Exception:
        user_id = player_id

    if not user_id:
        frappe.log_error(
            f"No user linked to player {player_id}",
            "Access Sync Error"
        )
        return

    cache = frappe.cache
    redis_key = f"memora:access:{user_id}"

    if doc.is_active:
        cache.sadd(redis_key, access_key)
        frappe.logger().info(f"Granted {access_key} to {user_id}")
    else:
        cache.srem(redis_key, access_key)
        frappe.logger().info(f"Revoked {access_key} from {user_id}")


def on_subscription_deleted(doc, method):
    """Remove grant when subscription is deleted."""
    player_id = doc.player

    try:
        if frappe.db.exists("Memora Player Profile", player_id):
            player_doc = frappe.get_doc("Memora Player Profile", player_id)
            user_id = player_doc.user
        else:
            user_id = player_id
    except Exception:
        user_id = player_id

    if user_id:
        cache = frappe.cache
        redis_key = f"memora:access:{user_id}"
        cache.srem(redis_key, doc.access_key)
        frappe.logger().info(f"Deleted grant {doc.access_key} from {user_id}")
