"""Access sync handlers for Frappe doc_events.

Sync subscription and season changes to Redis for O(1) access checks.
Per CONTEXT.md: immediate sync, sub-second propagation.

IMPORTANT: This module writes to TWO Redis instances:
1. Frappe cache (frappe.cache) - for Frappe-only data
2. FastAPI Redis (get_fastapi_redis()) - for data shared with FastAPI sidecar

The FastAPI sidecar uses a separate Redis instance without Frappe's site prefix.
"""
# Player identity is PLAYER-##### docname (not email). See Phase 32.

import json
import os
from pathlib import Path

import frappe
import redis
from dotenv import load_dotenv

# Load FastAPI .env file
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)


def get_fastapi_redis():
    """Get Redis connection for FastAPI sidecar.

    Uses the REDIS_URL from the FastAPI .env file.
    This is separate from frappe.cache which has site-specific prefixes.
    """
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(redis_url)


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
    r = get_fastapi_redis()
    redis_key = f"memora:season:{doc.name}"

    # Use single hset with mapping for atomic update
    r.hset(
        redis_key,
        mapping={
            "is_published": "1" if doc.is_published else "0",
            "start_date": str(doc.start_date),
            "end_date": str(doc.end_date),
            "season_seq": str(doc.season_seq),
        },
    )

    frappe.logger().info(f"Season {doc.name} synced to Redis")


def on_season_deleted(doc, method):
    """Remove season from Redis cache when deleted."""
    r = get_fastapi_redis()
    redis_key = f"memora:season:{doc.name}"
    r.delete(redis_key)

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
    # doc.player is the PLAYER-##### docname — use directly as Redis identity key
    user_id = doc.player
    access_key = doc.access_key

    r = get_fastapi_redis()
    redis_key = f"memora:access:{user_id}"

    if doc.is_active:
        r.sadd(redis_key, access_key)
        frappe.logger().info(f"Granted {access_key} to {user_id}")
    else:
        r.srem(redis_key, access_key)
        frappe.logger().info(f"Revoked {access_key} from {user_id}")

    # Notify the player to re-fetch subscriptions
    r.publish("memora:cache:invalidate", json.dumps({
        "type": "subscription_changed",
        "player_id": user_id,
    }))


def on_subscription_deleted(doc, method):
    """Remove grant when subscription is deleted."""
    # doc.player is the PLAYER-##### docname — use directly as Redis identity key
    user_id = doc.player

    r = get_fastapi_redis()
    redis_key = f"memora:access:{user_id}"
    r.srem(redis_key, doc.access_key)
    frappe.logger().info(f"Deleted grant {doc.access_key} from {user_id}")

    # Notify the player to re-fetch subscriptions
    r.publish("memora:cache:invalidate", json.dumps({
        "type": "subscription_changed",
        "player_id": user_id,
    }))


# =============================================================================
# Plan Subject Handlers (Level 1: Plan membership grants)
# =============================================================================


def on_plan_subject_changed(doc, method):
    """Sync plan free subjects to Redis when plan subject is added/updated/deleted.

    Per CONTEXT.md: is_premium=0 means subject is free in the plan.
    Maintains Redis set: memora:plan:{plan_id}:free_subjects
    """
    plan_id = doc.parent
    subject_id = doc.subject
    redis_key = f"memora:plan:{plan_id}:free_subjects"

    r = get_fastapi_redis()

    if method == "on_trash":
        # Remove from set regardless of is_premium (it's being deleted)
        r.srem(redis_key, subject_id)
        frappe.logger().info(f"Plan subject {subject_id} removed from plan {plan_id}")
    elif not doc.is_premium:
        # Add to free set (is_premium=0 means free)
        r.sadd(redis_key, subject_id)
        frappe.logger().info(f"Plan subject {subject_id} marked free in plan {plan_id}")
    else:
        # Remove from free set (is_premium=1 means paid)
        r.srem(redis_key, subject_id)
        frappe.logger().info(f"Plan subject {subject_id} marked premium in plan {plan_id}")

    # Notify connected clients on this plan to re-fetch subscriptions
    r.publish("memora:cache:invalidate", json.dumps({
        "type": "plan_subjects",
        "plan_id": plan_id,
    }))


def rebuild_plan_free_subjects(plan_id: str):
    """Rebuild entire plan free subjects set (for initial sync or repair).

    Args:
        plan_id: The plan identifier to rebuild
    """
    subjects = frappe.get_all(
        "Memora Plan Subject",
        filters={"parent": plan_id, "is_premium": 0},
        pluck="subject",
    )

    r = get_fastapi_redis()
    redis_key = f"memora:plan:{plan_id}:free_subjects"

    # Clear and rebuild
    r.delete(redis_key)
    if subjects:
        r.sadd(redis_key, *subjects)

    print(f"Rebuilt plan free subjects for {plan_id}: {len(subjects)} subjects")


# =============================================================================
# Free Content Handlers (Level 2 & 3: Unit/Topic is_free)
# =============================================================================


def on_unit_free_changed(doc, method):
    """Update subjects_with_free_content set when Unit.is_free changes.

    Maintains Redis set: memora:subjects_with_free_content
    """
    # Get subject via Track -> Subject relationship
    if not doc.track:
        return

    track = frappe.get_doc("Memora Track", doc.track)
    if not track.subject:
        return

    subject_id = track.subject
    redis_key = "memora:subjects_with_free_content"
    r = get_fastapi_redis()

    if method == "on_trash":
        # Check if subject still has free content after this unit is deleted
        _update_subject_free_content_status(subject_id, r, redis_key)
    elif doc.is_free:
        # Subject now has free content
        r.sadd(redis_key, subject_id)
        frappe.logger().info(f"Subject {subject_id} now has free content (unit {doc.name})")
    else:
        # Check if subject still has other free content
        _update_subject_free_content_status(subject_id, r, redis_key)


def on_topic_free_changed(doc, method):
    """Update subjects_with_free_content set when Topic.is_free changes.

    Maintains Redis set: memora:subjects_with_free_content
    """
    # Get subject via Unit -> Track -> Subject relationship
    if not doc.unit:
        return

    unit = frappe.get_doc("Memora Unit", doc.unit)
    if not unit.track:
        return

    track = frappe.get_doc("Memora Track", unit.track)
    if not track.subject:
        return

    subject_id = track.subject
    redis_key = "memora:subjects_with_free_content"
    r = get_fastapi_redis()

    if method == "on_trash":
        # Check if subject still has free content after this topic is deleted
        _update_subject_free_content_status(subject_id, r, redis_key)
    elif doc.is_free:
        # Subject now has free content
        r.sadd(redis_key, subject_id)
        frappe.logger().info(f"Subject {subject_id} now has free content (topic {doc.name})")
    else:
        # Check if subject still has other free content
        _update_subject_free_content_status(subject_id, r, redis_key)


def _update_subject_free_content_status(subject_id: str, r, redis_key: str):
    """Check if subject still has any free units or topics and update Redis.

    Single SQL with EXISTS for early exit — replaces 4 ORM queries.

    Args:
        subject_id: The subject to check
        r: Redis connection (from get_fastapi_redis())
        redis_key: The Redis key for subjects_with_free_content set
    """
    has_free = frappe.db.sql(
        """
        SELECT EXISTS(
            SELECT 1 FROM `tabMemora Unit` u
            INNER JOIN `tabMemora Track` t ON t.name = u.track
            WHERE t.subject = %(subject)s AND u.is_free = 1
            LIMIT 1
        ) OR EXISTS(
            SELECT 1 FROM `tabMemora Topic` tp
            INNER JOIN `tabMemora Unit` u ON u.name = tp.unit
            INNER JOIN `tabMemora Track` t ON t.name = u.track
            WHERE t.subject = %(subject)s AND tp.is_free = 1
            LIMIT 1
        ) AS has_free
        """,
        {"subject": subject_id},
    )[0][0]

    if has_free:
        r.sadd(redis_key, subject_id)
    else:
        r.srem(redis_key, subject_id)

    frappe.logger().info(f"Subject {subject_id} free content status: {'yes' if has_free else 'no'}")


def rebuild_subjects_with_free_content():
    """Rebuild entire subjects_with_free_content set (for initial sync or repair)."""
    r = get_fastapi_redis()
    redis_key = "memora:subjects_with_free_content"

    # Clear existing
    r.delete(redis_key)

    # Find all subjects with free units
    free_unit_subjects = frappe.db.sql(
        """
        SELECT DISTINCT t.subject
        FROM `tabMemora Track` t
        INNER JOIN `tabMemora Unit` u ON u.track = t.name
        WHERE u.is_free = 1
        """,
        as_dict=True,
    )

    # Find all subjects with free topics
    free_topic_subjects = frappe.db.sql(
        """
        SELECT DISTINCT t.subject
        FROM `tabMemora Track` t
        INNER JOIN `tabMemora Unit` u ON u.track = t.name
        INNER JOIN `tabMemora Topic` tp ON tp.unit = u.name
        WHERE tp.is_free = 1
        """,
        as_dict=True,
    )

    # Combine and add to set
    all_subjects = set()
    for row in free_unit_subjects:
        if row.subject:
            all_subjects.add(row.subject)
    for row in free_topic_subjects:
        if row.subject:
            all_subjects.add(row.subject)

    if all_subjects:
        r.sadd(redis_key, *all_subjects)

    print(f"Rebuilt subjects_with_free_content: {len(all_subjects)} subjects")
