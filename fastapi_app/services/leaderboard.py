"""Leaderboard service for Redis ZSET-backed XP rankings.

- Two leaderboard types: daily, weekly
- Dense ranking: tied players share same rank number
- Optional subject filtering for class-specific competitions

Key patterns:
- memora:lb:daily:{YYYY-MM-DD}[:subject:{id}]
- memora:lb:weekly:{YYYY-MM-DD}[:subject:{id}]  (date = Friday that starts the Islamic week)
"""

import asyncio
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import (
	LB_PREFIX,
	LBMETA_LOCK_TTL,
	PLAN_DAILY_KEY_TTL,
	PLAN_WEEKLY_KEY_TTL,
	lb_daily_key,
	lb_daily_plan_key,
	lb_weekly_key,
	lb_weekly_plan_key,
	lbmeta_keys_from_lb_key,
	lbmeta_lock_key,
)
from fastapi_app.core.redis_keys import (
	daily_xp_key as _daily_xp_key_fn,
)

logger = structlog.get_logger()

# Lua script: count distinct XP tiers above a player's score.
# Uses iterative ZRANGEBYSCORE LIMIT 1 to step through tiers — O(T * log N)
# where T = distinct tiers above (bounded by XP range, typically ≤300 for daily)
# and N = total ZSET size. Returns only [count, min_above] instead of
# transferring potentially 100k (member, score) pairs over the network.
_LEGACY_RANK_LUA = """
local key = KEYS[1]
local player_xp = tonumber(ARGV[1])
local count = 0
local min_above = -1
local current_min = player_xp + 1

while true do
    local entries = redis.call('ZRANGEBYSCORE', key, current_min, '+inf', 'WITHSCORES', 'LIMIT', 0, 1)
    if #entries == 0 then break end
    local score = math.floor(tonumber(entries[2]))
    count = count + 1
    if min_above == -1 then min_above = score end
    current_min = score + 1
end

return {count, min_above}
"""

# Atomic tier-aware ZINCRBY: updates leaderboard score AND maintains tier metadata
# in a single atomic operation. Prevents race conditions between concurrent XP awards.
#
# KEYS[1] = leaderboard ZSET (memora:lb:{period}:{date}[:{scope}])
# KEYS[2] = tier index ZSET  (memora:lbmeta:{period}:{date}[:{scope}]:tieridx)
# KEYS[3] = tier counts HASH (memora:lbmeta:{period}:{date}[:{scope}]:tiercnt)
# KEYS[4] = leaderboard mutation counter (memora:lbmeta:{...}:tiercnt:ver)
# ARGV[1] = player_id (string)
# ARGV[2] = xp_amount (integer, always > 0)
#
# Returns: {old_score, new_score} where old_score = -1 if player was new
#
# Contract: contracts/redis-operations.md — TIER_AWARE_ZINCRBY
# TTL: NOT set inside Lua — application layer handles EXPIRE after eval (FR-008)
_TIER_AWARE_ZINCRBY_LUA = """
local lb_key = KEYS[1]
local tieridx_key = KEYS[2]
local tiercnt_key = KEYS[3]
local version_key = KEYS[4]
local player_id = ARGV[1]
local xp_amount = tonumber(ARGV[2])

-- Step 1: Read old score (nil if new player)
local old_score_raw = redis.call('ZSCORE', lb_key, player_id)

-- Step 2: Increment leaderboard score
redis.call('ZINCRBY', lb_key, xp_amount, player_id)
redis.call('INCR', version_key)

-- Step 3: Decide whether to maintain tier metadata.
-- Only maintain if:
--   (a) BOTH tieridx and tiercnt already exist (healthy metadata), OR
--   (b) This is a brand-new leaderboard (first player, ZCARD=1) — safe to bootstrap.
-- Skipping prevents trusting or mutating partial metadata when one of the pair
-- is missing (e.g. rollout before backfill, or single-key loss).
local tieridx_exists = redis.call('EXISTS', tieridx_key) == 1
local tiercnt_exists = redis.call('EXISTS', tiercnt_key) == 1
local should_maintain = tieridx_exists and tiercnt_exists
if not tieridx_exists and not tiercnt_exists and not old_score_raw then
    -- New player on potentially new board — bootstrap only if we're the sole member
    if redis.call('ZCARD', lb_key) == 1 then
        should_maintain = true
    end
end

-- Step 4: Compute scores
local old_score = -1
local new_score
if old_score_raw then
    old_score = tonumber(old_score_raw)
    new_score = old_score + xp_amount
else
    new_score = xp_amount
end

-- Step 5: Update tier metadata (only if should_maintain)
if should_maintain then
    local old_tier = nil
    if old_score_raw then
        old_tier = math.floor(old_score)
    end
    local new_tier = math.floor(new_score)

    -- If player changed tiers, decrement old tier count
    if old_tier ~= nil and old_tier ~= new_tier then
        local remaining = redis.call('HINCRBY', tiercnt_key, tostring(old_tier), -1)
        if remaining <= 0 then
            -- Last player left this tier — remove it entirely
            redis.call('ZREM', tieridx_key, tostring(old_tier))
            redis.call('HDEL', tiercnt_key, tostring(old_tier))
        end
    end

    -- Increment new tier count and ensure tier exists in index
    redis.call('HINCRBY', tiercnt_key, tostring(new_tier), 1)
    redis.call('ZADD', tieridx_key, new_tier, tostring(new_tier))
end

return {old_score, new_score}
"""

# Asia/Amman timezone for consistent daily/weekly boundaries
# Per CONTEXT.md: Daily resets at midnight, weekly resets Friday midnight
AMMAN_TZ = ZoneInfo("Asia/Amman")

# TTLs for periodic leaderboard keys — only the current period is ever queried,
# so keep a small buffer beyond the period length for safety.
DAILY_KEY_TTL = 48 * 3600  # 48 hours (matches plan-scoped daily)
WEEKLY_KEY_TTL = 8 * 86400  # 8 days (matches plan-scoped weekly)


class LeaderboardService:
	"""Manages XP leaderboards via Redis sorted sets (ZSET).

	Two types: daily, weekly — with optional subject filtering.
	Dense ranking: tied players share rank.

	Operations:
	- get_top: ZRANGE with desc=True for top N players
	- get_my_rank: ZREVRANK + ZCOUNT for user position with dense rank
	- update_leaderboards: ZINCRBY for daily/weekly
	"""

	def __init__(self, redis_client: redis.Redis):
		"""Initialize LeaderboardService.

		Args:
			redis_client: Redis async client for ZSET operations
		"""
		self.redis = redis_client
		# Register scripts for EVALSHA auto-caching — after first call, only the
		# SHA1 hash is sent over the wire instead of the full ~1KB script text.
		self._tier_script = redis_client.register_script(_TIER_AWARE_ZINCRBY_LUA)
		self._legacy_script = redis_client.register_script(_LEGACY_RANK_LUA)

	def _get_key(self, lb_type: str, subject_id: str | None = None) -> str:
		"""Generate Redis key for a global leaderboard (daily/weekly only).

		Args:
			lb_type: One of "daily", "weekly"
			subject_id: Optional subject for filtered leaderboards

		Returns:
			Redis key string

		Raises:
			ValueError: If lb_type is invalid
		"""
		now = datetime.now(AMMAN_TZ)

		if lb_type == "daily":
			date_str = now.strftime("%Y-%m-%d")
			return lb_daily_key(date_str, subject_id)
		elif lb_type == "weekly":
			# Islamic week: Friday through Thursday
			# Key = the Friday date that starts the current week
			weekday = now.isoweekday()  # 1=Mon … 5=Fri … 7=Sun
			days_since_friday = (weekday - 5) % 7
			friday = (now - timedelta(days=days_since_friday)).strftime("%Y-%m-%d")
			return lb_weekly_key(friday, subject_id)
		else:
			raise ValueError(f"Invalid leaderboard type: {lb_type}")

	def _get_plan_key(self, lb_type: str, plan_id: str, subject_id: str | None = None) -> str:
		"""Generate Redis key for a plan-scoped leaderboard.

		Args:
			lb_type: One of "daily", "weekly"
			plan_id: Plan document name
			subject_id: Optional subject for filtered leaderboards

		Returns:
			Redis key string

		Raises:
			ValueError: If lb_type is invalid
		"""
		now = datetime.now(AMMAN_TZ)

		if lb_type == "daily":
			date_str = now.strftime("%Y-%m-%d")
			return lb_daily_plan_key(date_str, plan_id, subject_id)
		elif lb_type == "weekly":
			weekday = now.isoweekday()
			days_since_friday = (weekday - 5) % 7
			friday = (now - timedelta(days=days_since_friday)).strftime("%Y-%m-%d")
			return lb_weekly_plan_key(friday, plan_id, subject_id)
		else:
			raise ValueError(f"Invalid leaderboard type: {lb_type}")

	@staticmethod
	def _tiermeta_version_key(tiercnt_key: str) -> str:
		"""Mutation counter key for one leaderboard's authoritative ZSET."""
		return f"{tiercnt_key}:ver"

	async def _repair_tier_metadata_for_key(self, lb_key: str) -> bool:
		"""Rebuild missing tier metadata for one leaderboard under a short-lived lock."""
		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(lb_key)
		suffix = lb_key.replace(f"{LB_PREFIX}:", "", 1)
		lock_key = lbmeta_lock_key(suffix)
		lock_value = f"repair:{datetime.utcnow().timestamp()}"
		acquired = await self.redis.set(lock_key, lock_value, nx=True, ex=LBMETA_LOCK_TTL)
		if not acquired:
			return False

		try:
			for attempt in range(3):
				rebuilt = await self._rebuild_tier_metadata_locked(lb_key, tieridx_key, tiercnt_key)
				if rebuilt:
					return True
				logger.debug(
					"leaderboard_repair_retry",
					lb_key=lb_key,
					attempt=attempt + 1,
				)
			logger.warning("leaderboard_repair_failed", lb_key=lb_key)
			return False
		finally:
			if await self.redis.get(lock_key) == lock_value:
				await self.redis.delete(lock_key)

	async def _rebuild_tier_metadata_locked(
		self,
		lb_key: str,
		tieridx_key: str,
		tiercnt_key: str,
	) -> bool:
		"""Rebuild tier metadata from a stable leaderboard snapshot.

		Returns False if the authoritative ZSET changed while we were rebuilding.
		The caller may retry under the same repair lock.
		"""
		version_key = self._tiermeta_version_key(tiercnt_key)
		raw_expected_version = await self.redis.get(version_key)
		expected_version = int(raw_expected_version or 0)

		tier_counts: dict[int, int] = {}
		scan_cursor = 0
		while True:
			scan_cursor, entries = await self.redis.zscan(lb_key, scan_cursor, count=1000)
			for _member, score in entries:
				tier = math.floor(score)
				tier_counts[tier] = tier_counts.get(tier, 0) + 1
			if scan_cursor == 0:
				break

		lb_ttl = await self.redis.ttl(lb_key)

		pipe = self.redis.pipeline(transaction=True)
		try:
			await pipe.watch(version_key)
			raw_current_version = await pipe.get(version_key)
			current_version = int(raw_current_version or 0)
			if current_version != expected_version:
				return False

			pipe.multi()
			pipe.delete(tieridx_key, tiercnt_key)

			if tier_counts:
				zadd_mapping = {str(tier): tier for tier in tier_counts}
				hset_mapping = {str(tier): count for tier, count in tier_counts.items()}
				pipe.set(version_key, current_version)
				pipe.zadd(tieridx_key, zadd_mapping)
				pipe.hset(tiercnt_key, mapping=hset_mapping)

				if lb_ttl > 0:
					pipe.expire(version_key, lb_ttl)
					pipe.expire(tieridx_key, lb_ttl)
					pipe.expire(tiercnt_key, lb_ttl)

			await pipe.execute()
			return True
		except redis.WatchError:
			return False
		finally:
			await pipe.reset()

	async def _wait_for_tier_metadata(
		self,
		tieridx_key: str,
		tiercnt_key: str,
	) -> bool:
		"""Poll briefly for another request's repair to complete."""
		for _ in range(5):
			await asyncio.sleep(0.02)
			pipe = self.redis.pipeline()
			pipe.exists(tieridx_key)
			pipe.exists(tiercnt_key)
			tieridx_exists, tiercnt_exists = await pipe.execute()
			if tieridx_exists and tiercnt_exists:
				return True
		return False

	async def get_top(
		self,
		lb_type: str,
		limit: int = 10,
		offset: int = 0,
		subject_id: str | None = None,
		plan_id: str | None = None,
	) -> list[dict]:
		"""Get top N players from a leaderboard with optional offset.

		Uses ZRANGE with desc=True for O(log N + M) where M is returned elements.

		Dense ranking: tied players share same rank number, next rank increments
		by 1 (e.g., two #2s → next is #3, not #4).

		Args:
			lb_type: One of "daily", "weekly"
			limit: Maximum entries to return (default 10)
			offset: Number of entries to skip (default 0)
			subject_id: Optional subject filter
			plan_id: Optional plan for plan-scoped leaderboard

		Returns:
			List of dicts with rank, player_id, xp
		"""
		if plan_id:
			key = self._get_plan_key(lb_type, plan_id, subject_id)
		else:
			key = self._get_key(lb_type, subject_id)

		# Fetch from position 0 to offset+limit-1 so dense ranks are computed
		# accurately from the top of the leaderboard
		results = await self.redis.zrange(
			key,
			0,
			offset + limit - 1,
			desc=True,
			withscores=True,
		)

		all_entries = []
		current_rank = 1
		prev_xp = None

		for player_id, score in results:
			# Extract XP from composite score (integer part)
			xp = int(score)

			# Dense ranking: same XP = same rank
			# Increment rank only when XP changes (no gap)
			if prev_xp is not None and xp != prev_xp:
				current_rank += 1

			all_entries.append(
				{
					"rank": current_rank,
					"player_id": player_id,
					"xp": xp,
				}
			)
			prev_xp = xp

		# Return only the requested window
		entries = all_entries[offset:]

		logger.debug(
			"leaderboard_top_fetched",
			lb_type=lb_type,
			subject_id=subject_id,
			limit=limit,
			offset=offset,
			returned=len(entries),
		)

		return entries

	async def get_my_rank(
		self,
		player_id: str,
		lb_type: str,
		subject_id: str | None = None,
		neighbor_count: int = 2,
		plan_id: str | None = None,
	) -> dict | None:
		"""Get player's rank with surrounding neighbors.

		Uses dense ranking consistent with get_top(): tied players share rank,
		next rank increments by 1 (e.g., two #2s → next is #3).

		Args:
			player_id: Player's user ID
			lb_type: One of "daily", "weekly"
			subject_id: Optional subject filter
			neighbor_count: Players above/below to include (default 2)
			plan_id: Optional plan for plan-scoped leaderboard

		Returns:
			Dict with rank, xp, xp_to_next, neighbors, or None if error
		"""
		if plan_id:
			key = self._get_plan_key(lb_type, plan_id, subject_id)
		else:
			key = self._get_key(lb_type, subject_id)

		# Stage 1: Get position, total, score in one pipeline (1 RTT)
		pipe = self.redis.pipeline()
		pipe.zrevrank(key, player_id)
		pipe.zcard(key)
		pipe.zscore(key, player_id)
		position, total, score = await pipe.execute()

		# Handle unranked users (never earned XP in this period)
		if position is None:
			# Plan-scoped: rank is None per contract; global: total + 1
			unranked_rank = None if plan_id else total + 1
			xp_to_next = None
			if total > 0:
				bottom = await self.redis.zrange(key, 0, 0, withscores=True)
				if bottom:
					xp_to_next = int(bottom[0][1])
					if xp_to_next == 0:
						xp_to_next = 1

			logger.debug(
				"leaderboard_unranked_user",
				player_id=player_id,
				lb_type=lb_type,
				total_players=total,
			)
			return {
				"rank": unranked_rank,
				"xp": 0,
				"xp_to_next": xp_to_next,
				"neighbors": [],
				"total_players": total,
			}

		xp = int(score) if score is not None else 0

		# Stage 2: Dense rank + neighbor window
		#
		# Optimistic pipeline: fetch neighbors + tier index probes in 1 RTT.
		# If metadata is missing, attempt a per-key self-heal before using the
		# legacy dense-rank fallback.
		start = max(0, position - neighbor_count)
		stop = position + neighbor_count

		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)
		version_key = self._tiermeta_version_key(tiercnt_key)

		pipe = self.redis.pipeline()
		pipe.zrange(key, start, stop, desc=True, withscores=True)
		pipe.exists(tieridx_key)
		pipe.exists(tiercnt_key)
		pipe.exists(version_key)
		pipe.hvals(tiercnt_key)
		pipe.zcount(tieridx_key, f"({xp}", "+inf")
		pipe.zrangebyscore(tieridx_key, f"({xp}", "+inf", withscores=True, start=0, num=1)
		(
			neighbors_raw,
			tieridx_exists,
			tiercnt_exists,
			version_exists,
			tier_counts_raw,
			idx_distinct_above,
			min_above_entries,
		) = await pipe.execute()

		try:
			tier_count_sum = sum(int(v) for v in (tier_counts_raw or []))
		except (TypeError, ValueError):
			tier_count_sum = -1
		metadata_healthy = tieridx_exists and tiercnt_exists and version_exists and tier_count_sum == total

		if metadata_healthy:
			# Indexed path: O(log T) via tier index ZSET.
			distinct_above = idx_distinct_above
			min_above = int(min_above_entries[0][1]) if min_above_entries else -1
			fallback_used = False
			repair_used = False
		else:
			repair_used = await self._repair_tier_metadata_for_key(key)
			if not repair_used:
				repair_used = await self._wait_for_tier_metadata(tieridx_key, tiercnt_key)

			if repair_used:
				pipe = self.redis.pipeline()
				pipe.zcount(tieridx_key, f"({xp}", "+inf")
				pipe.zrangebyscore(tieridx_key, f"({xp}", "+inf", withscores=True, start=0, num=1)
				distinct_above, min_above_entries = await pipe.execute()
				min_above = int(min_above_entries[0][1]) if min_above_entries else -1
				fallback_used = False
			else:
				# Emergency-only fallback when repair could not complete in time.
				rank_result = await self._legacy_script(keys=[key], args=[str(xp)])
				distinct_above = rank_result[0]
				min_above = rank_result[1]
				fallback_used = True

		my_rank = distinct_above + 1

		# xp_to_next: XP needed to reach the closest higher tier
		xp_to_next = None
		if min_above > 0:
			xp_to_next = min_above - xp

		# Compute neighbor dense ranks relative to my_rank using window tiers.
		# The neighbor window (ZRANGE) returns contiguous positions, so ALL
		# distinct tiers between the player and any neighbor in the window are
		# present — no missing tiers. This lets us derive neighbor ranks from
		# my_rank without any additional queries.
		window_tiers = {int(s) for _, s in neighbors_raw}

		neighbors = []
		for neighbor_id, neighbor_score in neighbors_raw:
			neighbor_xp = int(neighbor_score)

			if neighbor_xp > xp:
				# Tiers strictly between player and neighbor (inclusive of neighbor)
				tiers_between = len({t for t in window_tiers if xp < t <= neighbor_xp})
				neighbor_rank = my_rank - tiers_between
			elif neighbor_xp < xp:
				# Tiers strictly between neighbor and player (inclusive of player)
				tiers_between = len({t for t in window_tiers if neighbor_xp < t <= xp})
				neighbor_rank = my_rank + tiers_between
			else:
				neighbor_rank = my_rank

			neighbors.append(
				{
					"rank": neighbor_rank,
					"player_id": neighbor_id,
					"xp": neighbor_xp,
					"is_me": neighbor_id == player_id,
				}
			)

		logger.debug(
			"leaderboard_rank_fetched",
			player_id=player_id,
			lb_type=lb_type,
			rank=my_rank,
			xp=xp,
			xp_to_next=xp_to_next,
			neighbor_count=len(neighbors),
			repair_used=repair_used,
			fallback_used=fallback_used,
		)

		return {
			"rank": my_rank,
			"xp": xp,
			"xp_to_next": xp_to_next,
			"neighbors": neighbors,
			"total_players": total,
		}

	async def update_leaderboards(
		self,
		player_id: str,
		xp_amount: int,
		subject_id: str | None = None,
		plan_id: str | None = None,
	) -> None:
		"""Update daily/weekly leaderboards after XP award.

		Called after wallet.award_xp() to maintain leaderboard consistency.
		Writes to both global ZSETs (backup) and plan-scoped ZSETs (primary read source).

		Skips the update entirely when xp_amount <= 0 to avoid creating
		ghost 0-score ZSET members via ZINCRBY.

		Args:
			player_id: Player's user ID
			xp_amount: XP just awarded (must be > 0 to write)
			subject_id: Optional subject for filtered leaderboards
			plan_id: Player's plan ID for plan-scoped leaderboards
		"""
		if xp_amount <= 0:
			return

		# Snapshot time once for all keys to avoid midnight-boundary splits
		now = datetime.now(AMMAN_TZ)
		date_str = now.strftime("%Y-%m-%d")
		weekday = now.isoweekday()
		days_since_friday = (weekday - 5) % 7
		friday = (now - timedelta(days=days_since_friday)).strftime("%Y-%m-%d")

		daily_key = lb_daily_key(date_str)
		weekly_key = lb_weekly_key(friday)

		# Single pipeline for all leaderboard updates (1 RTT)
		pipe = self.redis.pipeline()

		# Helper: atomic XP award + tier maintenance + TTL for one leaderboard variant.
		# Replaces plain ZINCRBY with _TIER_AWARE_ZINCRBY_LUA to atomically maintain
		# tier index and tier counts alongside the leaderboard score update.
		lua_eval_count = 0

		async def _tier_eval(lb_key: str, ttl: int) -> None:
			nonlocal lua_eval_count
			tidx, tcnt = lbmeta_keys_from_lb_key(lb_key)
			version_key = self._tiermeta_version_key(tcnt)
			await self._tier_script(
				keys=[lb_key, tidx, tcnt, version_key],
				args=[player_id, str(xp_amount)],
				client=pipe,
			)
			pipe.expire(lb_key, ttl)
			pipe.expire(tidx, ttl)
			pipe.expire(tcnt, ttl)
			pipe.expire(version_key, ttl)
			lua_eval_count += 1

		# Global daily + weekly
		await _tier_eval(daily_key, DAILY_KEY_TTL)
		await _tier_eval(weekly_key, WEEKLY_KEY_TTL)

		# Per-player daily XP summary hash (MariaDB-backed durability for activity chart)
		daily_xp_key = _daily_xp_key_fn(player_id)
		pipe.hincrby(daily_xp_key, date_str, xp_amount)
		pipe.expire(daily_xp_key, 8 * 86400)  # 8 days — covers the 7-day window + 1 buffer

		# Subject-specific leaderboards (if context available)
		if subject_id:
			await _tier_eval(lb_daily_key(date_str, subject_id), DAILY_KEY_TTL)
			await _tier_eval(lb_weekly_key(friday, subject_id), WEEKLY_KEY_TTL)

		# Plan-scoped leaderboards (dual-write for plan-scoped rankings)
		if plan_id:
			await _tier_eval(lb_daily_plan_key(date_str, plan_id), PLAN_DAILY_KEY_TTL)
			await _tier_eval(lb_weekly_plan_key(friday, plan_id), PLAN_WEEKLY_KEY_TTL)

			# Plan-scoped subject variants
			if subject_id:
				await _tier_eval(lb_daily_plan_key(date_str, plan_id, subject_id), PLAN_DAILY_KEY_TTL)
				await _tier_eval(lb_weekly_plan_key(friday, plan_id, subject_id), PLAN_WEEKLY_KEY_TTL)

		await pipe.execute()

		logger.debug(
			"leaderboards_updated",
			player_id=player_id,
			xp_amount=xp_amount,
			subject_id=subject_id,
			plan_id=plan_id,
			lua_eval_count=lua_eval_count,
		)
