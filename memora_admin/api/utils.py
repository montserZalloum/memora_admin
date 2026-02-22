"""Shared utilities for Frappe-side API modules."""

from __future__ import annotations

import frappe
import redis as _redis

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
	subject_key = f"memora:mastery:{player}:{subject}:s{season_seq}"
	all_key = f"memora:mastery:{player}:all:s{season_seq}"
	return subject_key, all_key


def update_mastery_counters(
	r: _redis.Redis,
	player: str,
	subject: str,
	season_seq: int,
	old_stability: float | None,
	new_stability: float | None,
) -> None:
	"""Increment/decrement mastery buckets when stability changes.

	No-op if the bucket classification is unchanged.
	Uses a pipeline (2 or 4 HINCRBY ops) — sub-millisecond.
	"""
	old_bucket = _classify_stability(old_stability)
	new_bucket = _classify_stability(new_stability)
	if old_bucket == new_bucket:
		return

	subj_key, all_key = _mastery_keys(player, subject, season_seq)
	pipe = r.pipeline(transaction=False)
	pipe.hincrby(subj_key, old_bucket, -1)
	pipe.hincrby(subj_key, new_bucket, 1)
	pipe.expire(subj_key, _MASTERY_COUNTER_TTL)
	pipe.hincrby(all_key, old_bucket, -1)
	pipe.hincrby(all_key, new_bucket, 1)
	pipe.expire(all_key, _MASTERY_COUNTER_TTL)
	pipe.execute()


def init_mastery_counter(
	r: _redis.Redis,
	player: str,
	subject: str,
	season_seq: int,
	stability: float | None,
) -> None:
	"""Increment mastery bucket for a newly created Memory State row.

	Updates both subject-specific and "all" aggregate keys.
	"""
	bucket = _classify_stability(stability)
	subj_key, all_key = _mastery_keys(player, subject, season_seq)
	pipe = r.pipeline(transaction=False)
	pipe.hincrby(subj_key, bucket, 1)
	pipe.expire(subj_key, _MASTERY_COUNTER_TTL)
	pipe.hincrby(all_key, bucket, 1)
	pipe.expire(all_key, _MASTERY_COUNTER_TTL)
	pipe.execute()
