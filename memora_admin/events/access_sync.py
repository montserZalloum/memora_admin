"""Access sync handlers for Frappe doc_events.

Sync subscription and season changes to Redis for O(1) access checks.
Per CONTEXT.md: immediate sync, sub-second propagation.

All Redis access goes through get_memora_redis() from utils.redis_connection,
which reads redis_memora from Frappe site_config (dedicated Memora instance on port 13001).
"""
# Player identity is PLAYER-##### docname (not email). See Phase 32.

import json

import frappe

from fastapi_app.core.redis_keys import (
	ACCESS_KEY_TTL,
	CH_PROGRESS_SCAN_PATTERN,
	PLAN_FREE_SUBJECTS_TTL,
	cache_invalidation_channel,
	ch_lbmeta_scan_pattern,
	ch_leaderboard_scan_pattern,
	plan_free_subjects_key,
	season_key,
	subjects_with_free_content_key,
)
from fastapi_app.core.redis_keys import (
	access_key as _access_key,
)
from memora_admin.utils.redis_connection import get_memora_redis


def get_fastapi_redis():
	"""Deprecated: use get_memora_redis() from memora_admin.utils.redis_connection instead."""
	import warnings

	warnings.warn(
		"get_fastapi_redis() is deprecated, use get_memora_redis() from memora_admin.utils.redis_connection",
		DeprecationWarning,
		stacklevel=2,
	)
	return get_memora_redis()


# =============================================================================
# Season Handlers (Gate 1)
# =============================================================================


def on_season_updated(doc, method):
	"""
	Sync season metadata to Redis on create/update.

	Per CONTEXT.md:
	- Gate 1 validates season is active and not expired
	- Uses Redis hash for atomic multi-field updates

	Also triggers Challenge Hub data reset when a season is unpublished
	or has ended (end_date < today).
	"""
	r = get_memora_redis()
	redis_key = season_key(doc.name)

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

	# Trigger Challenge Hub reset ONLY on a genuine 1 → 0 unpublish transition.
	# We cannot rely on has_value_changed() alone because on insert both
	# after_insert AND on_update fire with _doc_before_save as a blank doc
	# (blank.is_published is None, so `0 != None` is True → false positive).
	# Instead, explicitly verify the OLD value was truthy (i.e. the season
	# was actually published before this save).
	prev = getattr(doc, "_doc_before_save", None)
	was_published = prev and prev.get("is_published") if prev else False
	if was_published and not doc.is_published:
		try:
			reset_challenge_data(doc.name)
		except Exception:
			frappe.log_error(title=f"Challenge Hub reset failed for season {doc.name}")


def check_expired_seasons_challenge_reset():
	"""Daily job: unpublish seasons that have passed their end_date.

	The on_season_updated hook only fires on explicit unpublish (is_published 1→0).
	If a season simply expires by date without an admin action, this job catches it
	and sets is_published=0, which triggers the on_season_updated hook to perform
	the Challenge Hub reset.

	This approach avoids two problems:
	1. Repeated resets: the season is unpublished once, so the job won't re-trigger.
	2. Global progress wipe: reset_challenge_data is only called once via the hook,
	   not on every daily run for every expired season.

	Runs daily at 01:10 (registered in hooks.py scheduler_events).
	"""
	today = frappe.utils.today()

	# Find published seasons whose end_date has passed
	expired_seasons = frappe.get_all(
		"Memora Season",
		filters={"is_published": 1, "end_date": ["<", today]},
		pluck="name",
	)

	if not expired_seasons:
		return

	for season_id in expired_seasons:
		try:
			# Unpublish the season — this triggers on_season_updated hook which
			# detects the 1→0 transition and calls reset_challenge_data() exactly once.
			doc = frappe.get_doc("Memora Season", season_id)
			doc.is_published = 0
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			frappe.logger().info(f"Auto-unpublished expired season {season_id}")
		except Exception:
			frappe.log_error(title=f"Auto-unpublish failed for expired season {season_id}")


def on_season_deleted(doc, method):
	"""Remove season from Redis cache when deleted."""
	r = get_memora_redis()
	redis_key = season_key(doc.name)
	r.delete(redis_key)

	frappe.logger().info(f"Season {doc.name} removed from Redis")

	# Also clean up any Challenge Hub data for this season
	try:
		reset_challenge_data(doc.name)
	except Exception:
		frappe.log_error(title=f"Challenge Hub reset failed for deleted season {doc.name}")


def reset_challenge_data(season_id: str) -> dict:
	"""Reset all Challenge Hub data for a season (Redis + MariaDB).

	Cleans up:
	1. All challenge progress keys (memora:ch:progress:*)
	2. All challenge leaderboard keys for the season (memora:lb:ch:{season_id}:*)
	3. Dirty set entries (memora:dirty:ch_progress) — flush first to avoid data loss
	4. Attempt buffer (memora:ch:attempt_buffer) — flush first to avoid data loss
	5. tabMemora Challenge Progress rows for this season (MariaDB)

	Args:
		season_id: The season identifier to reset.

	Returns:
		dict with counts of deleted keys and rows.
	"""
	r = get_memora_redis()
	deleted_progress = 0
	deleted_leaderboard = 0

	# Step 1: Flush dirty challenge progress to MariaDB before clearing Redis
	# This ensures no pending data is lost
	_flush_dirty_challenge_data_before_reset()

	# Step 2: SCAN and DELETE all challenge progress keys
	# These are not season-scoped (player x subject), so we delete all of them
	# on season reset since challenge progress is season-specific conceptually
	cursor = 0
	while True:
		cursor, keys = r.scan(cursor=cursor, match=CH_PROGRESS_SCAN_PATTERN, count=200)
		if keys:
			deleted_progress += r.delete(*keys)
		if cursor == 0:
			break

	# Step 3: SCAN and DELETE all challenge leaderboard keys for this season
	# Pattern: memora:lb:ch:{season_id}:* covers plan and subject leaderboards
	lb_pattern = ch_leaderboard_scan_pattern(season_id)
	cursor = 0
	while True:
		cursor, keys = r.scan(cursor=cursor, match=lb_pattern, count=200)
		if keys:
			deleted_leaderboard += r.delete(*keys)
		if cursor == 0:
			break

	# Step 4: Also clean the leaderboard metadata keys (tieridx, tiercnt)
	lbmeta_pattern = ch_lbmeta_scan_pattern(season_id)
	cursor = 0
	while True:
		cursor, keys = r.scan(cursor=cursor, match=lbmeta_pattern, count=200)
		if keys:
			deleted_leaderboard += r.delete(*keys)
		if cursor == 0:
			break

	# Step 5: Delete MariaDB Challenge Progress rows for this season.
	# Records are not exported to the analytics server so there is no reason
	# to keep them after the season ends — they would just accumulate forever.
	deleted_db_rows = frappe.db.count(
		"Memora Challenge Progress", filters={"season": season_id}
	)
	if deleted_db_rows:
		frappe.db.delete("Memora Challenge Progress", {"season": season_id})

	frappe.logger().info(
		f"Challenge Hub reset for season {season_id}: "
		f"{deleted_progress} progress keys, {deleted_leaderboard} leaderboard keys, "
		f"{deleted_db_rows} DB rows deleted"
	)

	return {
		"season_id": season_id,
		"deleted_progress": deleted_progress,
		"deleted_leaderboard": deleted_leaderboard,
		"deleted_db_rows": deleted_db_rows,
	}


def _flush_dirty_challenge_data_before_reset():
	"""Flush any pending dirty challenge progress and attempt buffer to MariaDB.

	Called before season reset to ensure no data loss. If sync fails,
	we still proceed with the reset (data is in MariaDB already via
	periodic sync, and remaining dirty items are best-effort).
	"""
	try:
		from memora_admin.tasks.sync import sync_dirty_challenge_progress

		sync_dirty_challenge_progress()
		frappe.logger().info("Pre-reset challenge data flush completed")
	except Exception:
		frappe.log_error(title="Pre-reset challenge data flush failed (proceeding with reset)")


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

	r = get_memora_redis()
	redis_key = _access_key(user_id)

	if doc.is_active:
		r.sadd(redis_key, access_key)
		r.expire(redis_key, ACCESS_KEY_TTL)
		frappe.logger().info(f"Granted {access_key} to {user_id}")
	else:
		r.srem(redis_key, access_key)
		# Refresh TTL so remaining grants don't expire prematurely
		if r.exists(redis_key):
			r.expire(redis_key, ACCESS_KEY_TTL)
		frappe.logger().info(f"Revoked {access_key} from {user_id}")

	# Notify the player to re-fetch subscriptions
	r.publish(
		cache_invalidation_channel(),
		json.dumps(
			{
				"type": "subscription_changed",
				"player_id": user_id,
			}
		),
	)


def on_subscription_deleted(doc, method):
	"""Remove grant when subscription is deleted."""
	# doc.player is the PLAYER-##### docname — use directly as Redis identity key
	user_id = doc.player

	r = get_memora_redis()
	redis_key = _access_key(user_id)
	r.srem(redis_key, doc.access_key)
	frappe.logger().info(f"Deleted grant {doc.access_key} from {user_id}")

	# Notify the player to re-fetch subscriptions
	r.publish(
		cache_invalidation_channel(),
		json.dumps(
			{
				"type": "subscription_changed",
				"player_id": user_id,
			}
		),
	)


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
	redis_key = plan_free_subjects_key(plan_id)

	r = get_memora_redis()

	if method == "on_trash":
		# Remove from set regardless of is_premium (it's being deleted)
		r.srem(redis_key, subject_id)
		frappe.logger().info(f"Plan subject {subject_id} removed from plan {plan_id}")
	elif not doc.is_premium:
		# Add to free set (is_premium=0 means free)
		r.sadd(redis_key, subject_id)
		r.expire(redis_key, PLAN_FREE_SUBJECTS_TTL)
		frappe.logger().info(f"Plan subject {subject_id} marked free in plan {plan_id}")
	else:
		# Remove from free set (is_premium=1 means paid)
		r.srem(redis_key, subject_id)
		frappe.logger().info(f"Plan subject {subject_id} marked premium in plan {plan_id}")

	# Notify connected clients on this plan to re-fetch subscriptions
	r.publish(
		cache_invalidation_channel(),
		json.dumps(
			{
				"type": "plan_subjects",
				"plan_id": plan_id,
			}
		),
	)


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

	r = get_memora_redis()
	redis_key = plan_free_subjects_key(plan_id)

	# Clear and rebuild
	r.delete(redis_key)
	if subjects:
		r.sadd(redis_key, *subjects)
		r.expire(redis_key, PLAN_FREE_SUBJECTS_TTL)

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
	redis_key = subjects_with_free_content_key()
	r = get_memora_redis()

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
	redis_key = subjects_with_free_content_key()
	r = get_memora_redis()

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
	    r: Redis connection (from get_memora_redis())
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
	r = get_memora_redis()
	redis_key = subjects_with_free_content_key()

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
