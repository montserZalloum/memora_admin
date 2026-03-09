"""
Backfill and rebuild utilities for leaderboard keys.

1. backfill_tier_metadata() — populates tier index/counts for dense rank reads.
2. rebuild_challenge_leaderboards() — rebuilds Challenge Hub leaderboard ZSETs
   from MariaDB (Memora Challenge Progress) after Redis data loss.

Backfill tier metadata for existing leaderboard keys.

Populates tier index (ZSET) and tier counts (HASH) for every active
leaderboard, enabling O(log T) dense rank reads instead of the
O(T * log N) legacy Lua iterative approach.

Usage:
    bench --site x.conanacademy.com execute \
        memora_admin.tasks.leaderboard_backfill.backfill_tier_metadata

**Recommended**: Run during low-traffic hours (e.g., 03:00 local) to
minimize the race window described below.

Race window during backfill:
    Between ZSCAN (snapshot) and MULTI/EXEC (metadata install), live XP
    writes may change player scores. The Lua script skips metadata
    maintenance during this window (should_maintain=false since metadata
    doesn't exist yet). After installation, metadata reflects the ZSCAN
    snapshot — not the current state — so tier counts may drift by N
    (where N = live writes during the gap).

    Self-correction: Once metadata is installed, subsequent XP writes
    see should_maintain=true and the Lua script atomically adjusts tier
    counts. Any drift from the race window is corrected within the next
    few writes per affected tier. No manual intervention required.

After backfill completes, the indexed read path in get_my_rank() will
automatically detect tier metadata via EXISTS and skip the legacy Lua.
"""

import logging
import math
import time
from collections import defaultdict

from fastapi_app.core.redis_keys import (
	LB_PREFIX,
	LBMETA_LOCK_TTL,
	ch_leaderboard_key,
	ch_leaderboard_subject_key,
	lbmeta_keys_from_lb_key,
	lbmeta_lock_key,
)
from memora_admin.utils.redis_connection import get_memora_redis

logger = logging.getLogger(__name__)


def backfill_tier_metadata() -> dict:
	"""Backfill tier metadata for all active leaderboard keys.

	Scans for ``memora:lb:*`` keys (skipping archive keys), acquires a
	per-key short-lived lock, builds tier counts from ZSCAN, atomically
	installs tieridx ZSET + tiercnt HASH via MULTI/EXEC, releases lock,
	and logs progress every 10 keys.

	Returns:
		Summary dict with counts: processed, skipped, errors, mismatches.
	"""
	r = get_memora_redis()

	stats = {
		"processed": 0,
		"skipped_archive": 0,
		"skipped_locked": 0,
		"skipped_empty": 0,
		"errors": 0,
		"mismatches": 0,
	}

	# Collect all leaderboard keys
	lb_keys = []
	cursor = 0
	while True:
		cursor, keys = r.scan(cursor, match=f"{LB_PREFIX}:*", count=500)
		for key in keys:
			k = key.decode() if isinstance(key, bytes) else key
			# Skip archive keys — they don't need tier metadata
			if ":archive:" in k:
				stats["skipped_archive"] += 1
				continue
			lb_keys.append(k)
		if cursor == 0:
			break

	logger.info(f"leaderboard_backfill: found {len(lb_keys)} active leaderboard keys")

	for i, lb_key in enumerate(lb_keys):
		try:
			_backfill_one_key(r, lb_key, stats)
		except Exception:
			stats["errors"] += 1
			logger.exception(f"leaderboard_backfill: error processing {lb_key}")

		# Progress logging every 10 keys
		if (i + 1) % 10 == 0:
			logger.info(
				f"leaderboard_backfill: progress {i + 1}/{len(lb_keys)} "
				f"(processed={stats['processed']}, errors={stats['errors']})"
			)

	logger.info(
		f"leaderboard_backfill: complete — "
		f"processed={stats['processed']}, "
		f"skipped_archive={stats['skipped_archive']}, "
		f"skipped_locked={stats['skipped_locked']}, "
		f"skipped_empty={stats['skipped_empty']}, "
		f"errors={stats['errors']}, "
		f"mismatches={stats['mismatches']}"
	)

	return stats


def _backfill_one_key(r, lb_key: str, stats: dict) -> None:
	"""Backfill tier metadata for a single leaderboard key.

	Args:
		r: Synchronous Redis client
		lb_key: Full leaderboard key (e.g., 'memora:lb:daily:2026-03-01')
		stats: Mutable stats dict to update
	"""
	tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(lb_key)

	# Derive lock key from the suffix after LB_PREFIX
	suffix = lb_key.replace(f"{LB_PREFIX}:", "", 1)
	lock_key = lbmeta_lock_key(suffix)

	# Acquire per-key lock (SET NX EX 30)
	lock_value = f"backfill:{int(time.time())}"
	acquired = r.set(lock_key, lock_value, nx=True, ex=LBMETA_LOCK_TTL)
	if not acquired:
		stats["skipped_locked"] += 1
		logger.debug(f"leaderboard_backfill: skipped {lb_key} (locked)")
		return

	try:
		# ZSCAN the leaderboard to build tier counts
		tier_counts = defaultdict(int)
		scan_cursor = 0
		while True:
			scan_cursor, entries = r.zscan(lb_key, scan_cursor, count=1000)
			for _member, score in entries:
				tier = math.floor(score)
				tier_counts[tier] += 1
			if scan_cursor == 0:
				break

		if not tier_counts:
			stats["skipped_empty"] += 1
			return

		# Get TTL from the leaderboard key to mirror on metadata
		lb_ttl = r.ttl(lb_key)

		# Atomic install via MULTI/EXEC
		pipe = r.pipeline(transaction=True)
		pipe.delete(tieridx_key, tiercnt_key)  # clean slate

		# Build ZADD mapping and HSET mapping
		zadd_mapping = {}
		hset_mapping = {}
		for tier, count in tier_counts.items():
			zadd_mapping[str(tier)] = tier
			hset_mapping[str(tier)] = count

		pipe.zadd(tieridx_key, zadd_mapping)
		pipe.hset(tiercnt_key, mapping=hset_mapping)

		# Set TTL matching the leaderboard key
		if lb_ttl > 0:
			pipe.expire(tieridx_key, lb_ttl)
			pipe.expire(tiercnt_key, lb_ttl)

		pipe.execute()

		# T010: Integrity check — sum(tier_counts) must equal ZCARD(lb_key)
		lb_card = r.zcard(lb_key)
		tier_sum = sum(tier_counts.values())
		if tier_sum != lb_card:
			stats["mismatches"] += 1
			logger.error(
				f"leaderboard_backfill: INTEGRITY MISMATCH {lb_key} — "
				f"tier_sum={tier_sum}, zcard={lb_card}"
			)
		else:
			logger.debug(
				f"leaderboard_backfill: OK {lb_key} — " f"{len(tier_counts)} tiers, {tier_sum} members"
			)

		stats["processed"] += 1

	finally:
		# Release lock
		r.delete(lock_key)


def rebuild_challenge_leaderboards(season_id: str | None = None) -> dict:
	"""Rebuild Challenge Hub leaderboard ZSETs from MariaDB source of truth.

	Reads Memora Challenge Progress records, groups by player + plan + season,
	and populates both overall and per-subject leaderboard ZSETs.

	This is a recovery tool for when Redis leaderboard data is lost
	(restart without AOF, accidental deletion, etc.).

	Args:
		season_id: Specific season to rebuild. If None, rebuilds all seasons
			found in Memora Challenge Progress.

	Usage:
		bench --site x.conanacademy.com execute \\
			memora_admin.tasks.leaderboard_backfill.rebuild_challenge_leaderboards

		# Single season:
		bench --site x.conanacademy.com execute \\
			memora_admin.tasks.leaderboard_backfill.rebuild_challenge_leaderboards \\
			--kwargs '{"season_id": "SEAS-00635"}'

	Returns:
		Summary dict with counts: seasons, plans, players, keys_written, errors.
	"""
	import frappe

	r = get_memora_redis()

	stats = {
		"seasons": 0,
		"plans": 0,
		"players": 0,
		"keys_written": 0,
		"errors": 0,
	}

	# Query: per-player per-subject XP totals with their plan from profile.
	# Challenge Progress has player, subject, season, total_xp_earned.
	# Player Profile has the player's current plan.
	season_filter = ""
	params = {}
	if season_id:
		season_filter = "AND cp.season = %(season_id)s"
		params["season_id"] = season_id

	rows = frappe.db.sql(
		f"""
		SELECT
			cp.player,
			cp.subject,
			cp.season,
			pp.plan,
			SUM(cp.total_xp_earned) AS total_xp
		FROM `tabMemora Challenge Progress` cp
		JOIN `tabMemora Player Profile` pp ON pp.name = cp.player
		WHERE pp.plan IS NOT NULL
			AND pp.plan != ''
			{season_filter}
		GROUP BY cp.player, cp.subject, cp.season, pp.plan
		HAVING total_xp > 0
		""",
		params,
		as_dict=True,
	)

	if not rows:
		logger.info("rebuild_ch_leaderboards: no challenge progress found")
		return stats

	# Aggregate into: (season, plan) → {player → {overall_xp, per_subject_xp}}
	leaderboard_data: dict[tuple[str, str], dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"overall": 0, "subjects": defaultdict(int)}))

	for row in rows:
		key = (row["season"], row["plan"])
		player_data = leaderboard_data[key][row["player"]]
		xp = int(row["total_xp"])
		player_data["overall"] += xp
		player_data["subjects"][row["subject"]] += xp

	seasons_seen = set()
	plans_seen = set()
	players_seen = set()

	for (season, plan), players in leaderboard_data.items():
		seasons_seen.add(season)
		plans_seen.add((season, plan))

		try:
			# Build overall leaderboard
			lb_key = ch_leaderboard_key(season, plan)
			overall_mapping = {}
			for player_id, data in players.items():
				players_seen.add(player_id)
				overall_mapping[player_id] = data["overall"]

			if overall_mapping:
				pipe = r.pipeline()
				pipe.delete(lb_key)
				pipe.zadd(lb_key, overall_mapping)
				pipe.execute()
				stats["keys_written"] += 1

			# Build per-subject leaderboards
			subject_members: dict[str, dict[str, int]] = defaultdict(dict)
			for player_id, data in players.items():
				for subject_id, xp in data["subjects"].items():
					subject_members[subject_id][player_id] = xp

			for subject_id, members in subject_members.items():
				lb_subj_key = ch_leaderboard_subject_key(season, plan, subject_id)
				pipe = r.pipeline()
				pipe.delete(lb_subj_key)
				pipe.zadd(lb_subj_key, members)
				pipe.execute()
				stats["keys_written"] += 1

		except Exception as e:
			stats["errors"] += 1
			logger.error(f"rebuild_ch_leaderboards: error for season={season} plan={plan}: {e}")

	stats["seasons"] = len(seasons_seen)
	stats["plans"] = len(plans_seen)
	stats["players"] = len(players_seen)

	logger.info(
		f"rebuild_ch_leaderboards: complete — "
		f"seasons={stats['seasons']}, plans={stats['plans']}, "
		f"players={stats['players']}, keys_written={stats['keys_written']}, "
		f"errors={stats['errors']}"
	)

	return stats
