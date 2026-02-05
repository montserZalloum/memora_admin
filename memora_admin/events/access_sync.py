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

    cache = frappe.cache

    if method == "on_trash":
        # Remove from set regardless of is_premium (it's being deleted)
        cache.srem(redis_key, subject_id)
        frappe.logger().info(f"Plan subject {subject_id} removed from plan {plan_id}")
    elif not doc.is_premium:
        # Add to free set (is_premium=0 means free)
        cache.sadd(redis_key, subject_id)
        frappe.logger().info(f"Plan subject {subject_id} marked free in plan {plan_id}")
    else:
        # Remove from free set (is_premium=1 means paid)
        cache.srem(redis_key, subject_id)
        frappe.logger().info(f"Plan subject {subject_id} marked premium in plan {plan_id}")


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

    cache = frappe.cache
    redis_key = f"memora:plan:{plan_id}:free_subjects"

    # Clear and rebuild
    cache.delete_key(redis_key)
    if subjects:
        cache.sadd(redis_key, *subjects)

    frappe.logger().info(f"Rebuilt plan free subjects for {plan_id}: {len(subjects)} subjects")


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
    cache = frappe.cache

    if method == "on_trash":
        # Check if subject still has free content after this unit is deleted
        _update_subject_free_content_status(subject_id, cache, redis_key)
    elif doc.is_free:
        # Subject now has free content
        cache.sadd(redis_key, subject_id)
        frappe.logger().info(f"Subject {subject_id} now has free content (unit {doc.name})")
    else:
        # Check if subject still has other free content
        _update_subject_free_content_status(subject_id, cache, redis_key)


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
    cache = frappe.cache

    if method == "on_trash":
        # Check if subject still has free content after this topic is deleted
        _update_subject_free_content_status(subject_id, cache, redis_key)
    elif doc.is_free:
        # Subject now has free content
        cache.sadd(redis_key, subject_id)
        frappe.logger().info(f"Subject {subject_id} now has free content (topic {doc.name})")
    else:
        # Check if subject still has other free content
        _update_subject_free_content_status(subject_id, cache, redis_key)


def _update_subject_free_content_status(subject_id: str, cache, redis_key: str):
    """Check if subject still has any free units or topics and update Redis.

    Args:
        subject_id: The subject to check
        cache: Frappe cache object
        redis_key: The Redis key for subjects_with_free_content set
    """
    # Check for free units
    free_units = frappe.db.count(
        "Memora Unit",
        filters={
            "track": ["in", frappe.get_all("Memora Track", filters={"subject": subject_id}, pluck="name")],
            "is_free": 1,
        },
    )

    if free_units > 0:
        cache.sadd(redis_key, subject_id)
        return

    # Check for free topics
    tracks = frappe.get_all("Memora Track", filters={"subject": subject_id}, pluck="name")
    if not tracks:
        cache.srem(redis_key, subject_id)
        return

    units = frappe.get_all("Memora Unit", filters={"track": ["in", tracks]}, pluck="name")
    if not units:
        cache.srem(redis_key, subject_id)
        return

    free_topics = frappe.db.count(
        "Memora Topic",
        filters={"unit": ["in", units], "is_free": 1},
    )

    if free_topics > 0:
        cache.sadd(redis_key, subject_id)
    else:
        cache.srem(redis_key, subject_id)

    frappe.logger().info(
        f"Subject {subject_id} free content status: {free_units} free units, {free_topics} free topics"
    )


def rebuild_subjects_with_free_content():
    """Rebuild entire subjects_with_free_content set (for initial sync or repair)."""
    cache = frappe.cache
    redis_key = "memora:subjects_with_free_content"

    # Clear existing
    cache.delete_key(redis_key)

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
        cache.sadd(redis_key, *all_subjects)

    frappe.logger().info(f"Rebuilt subjects_with_free_content: {len(all_subjects)} subjects")
