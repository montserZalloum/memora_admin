"""Shared utilities for Frappe-side API modules."""

from __future__ import annotations

import frappe
import redis as _redis

from fastapi_app.core.redis_keys import mastery_key

# FSRS stability threshold for mature memory classification (days)
MASTERY_MATURE_THRESHOLD = 21.0

_SEASON_SEQ_CACHE_TTL = 86400  # 24 hours
_MASTERY_COUNTER_TTL = 300  # 5 minutes — self-heal window for drifted counters


def get_player_season_seq(player_id: str) -> int:
	"""Get the season_seq for a player's plan, with 24h cache.

	Resolves: Player Profile -> Academic Plan -> Season -> season_seq.
	Falls back to 1 if player has no plan/season assigned.

	Cached via frappe.cache() with 24h TTL. Invalidated by
	plan_change_sync.on_player_profile_plan_changed when admin
	changes a player's plan.
	"""
	cache_key = f"player_season_seq:{player_id}"
	cached = frappe.cache().get_value(cache_key, expires=True)
	if cached is not None:
		return int(cached)

	result = frappe.db.sql(
		"""
		SELECT s.season_seq
		FROM `tabMemora Player Profile` pp
		INNER JOIN `tabMemora Academic Plan` ap ON ap.name = pp.plan
		INNER JOIN `tabMemora Season` s ON s.name = ap.season
		WHERE pp.name = %(player)s
		LIMIT 1
		""",
		{"player": player_id},
	)
	value = int(result[0][0]) if result else 1

	frappe.cache().set_value(cache_key, value, expires_in_sec=_SEASON_SEQ_CACHE_TTL)
	return value


def invalidate_player_season_seq(player_id: str) -> None:
	"""Clear cached season_seq for a player. Call on plan change."""
	frappe.cache().delete_value(f"player_season_seq:{player_id}")


# ---------------------------------------------------------------------------
# Mastery counter helpers (Redis HASH-based, sub-millisecond)
# ---------------------------------------------------------------------------


def _classify_stability(stability: float | None) -> str:
	"""Classify FSRS stability into a mastery bucket.

	Returns "mature", "learning", or "new".
	"""
	if stability is not None and stability >= MASTERY_MATURE_THRESHOLD:
		return "mature"
	elif stability is not None and stability > 0:
		return "learning"
	return "new"


def _mastery_keys(player: str, subject: str, season_seq: int) -> tuple[str, str]:
	"""Return (subject_key, all_key) for mastery counter hashes."""
	subject_key = mastery_key(player, subject, season_seq)
	all_key = mastery_key(player, None, season_seq)
	return subject_key, all_key


# Apply a single-bucket delta ONLY if the hash already exists, then refresh its
# TTL. Guarding on EXISTS prevents an incremental HINCRBY from resurrecting a key
# that expired out from under us: a resurrected key would start from 0 and
# accumulate detached/negative counts (the root cause of mastery drift). When the
# key is absent, we skip the delta — the next profile read rehydrates absolute
# counts from SQL (get_memory_mastery), so no information is lost.
# KEYS[1]=hash key, ARGV[1]=field, ARGV[2]=delta, ARGV[3]=ttl
_MASTERY_DELTA_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
	redis.call('HINCRBY', KEYS[1], ARGV[1], ARGV[2])
	redis.call('EXPIRE', KEYS[1], ARGV[3])
	return 1
end
return 0
"""

# KEYS[1]=hash key, ARGV[1]=dec field, ARGV[2]=inc field, ARGV[3]=ttl
_MASTERY_MOVE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
	redis.call('HINCRBY', KEYS[1], ARGV[1], -1)
	redis.call('HINCRBY', KEYS[1], ARGV[2], 1)
	redis.call('EXPIRE', KEYS[1], ARGV[3])
	return 1
end
return 0
"""


def update_mastery_counters(
	r: _redis.Redis,
	player: str,
	subject: str,
	season_seq: int,
	old_stability: float | None,
	new_stability: float | None,
) -> None:
	"""Increment/decrement mastery buckets when stability changes.

	No-op if the bucket classification is unchanged. Deltas are applied via a
	Lua script that only mutates a counter hash that already exists, so an
	expired key is never resurrected with detached counts (see _MASTERY_MOVE_LUA).
	"""
	old_bucket = _classify_stability(old_stability)
	new_bucket = _classify_stability(new_stability)
	if old_bucket == new_bucket:
		return

	subj_key, all_key = _mastery_keys(player, subject, season_seq)
	for key in (subj_key, all_key):
		r.eval(_MASTERY_MOVE_LUA, 1, key, old_bucket, new_bucket, _MASTERY_COUNTER_TTL)


def init_mastery_counter(
	r: _redis.Redis,
	player: str,
	subject: str,
	season_seq: int,
	stability: float | None,
) -> None:
	"""Increment mastery bucket for a newly created Memory State row.

	Updates both subject-specific and "all" aggregate keys. Like
	update_mastery_counters, the +1 is only applied to a hash that already
	exists; if the counter expired, the next read rehydrates it from SQL
	(which counts the new row), so the increment is not lost.
	"""
	bucket = _classify_stability(stability)
	subj_key, all_key = _mastery_keys(player, subject, season_seq)
	for key in (subj_key, all_key):
		r.eval(_MASTERY_DELTA_LUA, 1, key, bucket, 1, _MASTERY_COUNTER_TTL)
