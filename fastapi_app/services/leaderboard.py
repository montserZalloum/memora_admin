"""Leaderboard service for Redis ZSET-backed XP rankings.

- Two leaderboard types: daily, weekly
- Dense ranking: tied players share same rank number
- Optional subject filtering for class-specific competitions

Key patterns:
- memora:lb:daily:{YYYY-MM-DD}[:subject:{id}]
- memora:lb:weekly:{YYYY-MM-DD}[:subject:{id}]  (date = Friday that starts the Islamic week)
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import (
	PLAN_DAILY_KEY_TTL,
	PLAN_WEEKLY_KEY_TTL,
	daily_xp_key as _daily_xp_key_fn,
	lb_daily_key,
	lb_daily_plan_key,
	lb_weekly_key,
	lb_weekly_plan_key,
)

logger = structlog.get_logger()

# Lua script: count distinct XP tiers above a player's score.
# Uses iterative ZRANGEBYSCORE LIMIT 1 to step through tiers — O(T * log N)
# where T = distinct tiers above (bounded by XP range, typically ≤300 for daily)
# and N = total ZSET size. Returns only [count, min_above] instead of
# transferring potentially 100k (member, score) pairs over the network.
_RANK_LUA = """
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

# Asia/Amman timezone for consistent daily/weekly boundaries
# Per CONTEXT.md: Daily resets at midnight, weekly resets Friday midnight
AMMAN_TZ = ZoneInfo("Asia/Amman")

# TTLs for periodic leaderboard keys (prevents unbounded accumulation after Redis data loss)
DAILY_KEY_TTL = 30 * 86400  # 30 days
WEEKLY_KEY_TTL = 90 * 86400  # 90 days


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

			# Handle bytes response from Redis (unless decode_responses=True)
			pid = player_id.decode() if isinstance(player_id, bytes) else player_id

			all_entries.append({
				"rank": current_rank,
				"player_id": pid,
				"xp": xp,
			})
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

		# Stage 2: Bounded dense rank via Lua + neighbor window (1 pipeline RTT)
		#
		# The Lua script counts distinct XP tiers above the player server-side,
		# returning only [count, min_above] — avoids transferring potentially
		# 100k (member, score) pairs that the old zrangebyscore approach fetched.
		start = max(0, position - neighbor_count)
		stop = position + neighbor_count

		pipe = self.redis.pipeline()
		pipe.zrange(key, start, stop, desc=True, withscores=True)
		pipe.eval(_RANK_LUA, 1, key, str(xp))
		neighbors_raw, rank_result = await pipe.execute()

		distinct_above = rank_result[0]
		min_above = rank_result[1]

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

			nid = neighbor_id.decode() if isinstance(neighbor_id, bytes) else neighbor_id

			neighbors.append({
				"rank": neighbor_rank,
				"player_id": nid,
				"xp": neighbor_xp,
				"is_me": nid == player_id,
			})

		logger.debug(
			"leaderboard_rank_fetched",
			player_id=player_id,
			lb_type=lb_type,
			rank=my_rank,
			xp=xp,
			xp_to_next=xp_to_next,
			neighbor_count=len(neighbors),
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

		# Daily: increment + TTL
		pipe.zincrby(daily_key, xp_amount, player_id)
		pipe.expire(daily_key, DAILY_KEY_TTL)

		# Weekly: increment + TTL
		pipe.zincrby(weekly_key, xp_amount, player_id)
		pipe.expire(weekly_key, WEEKLY_KEY_TTL)

		# Per-player daily XP summary hash (MariaDB-backed durability for activity chart)
		daily_xp_key = _daily_xp_key_fn(player_id)
		pipe.hincrby(daily_xp_key, date_str, xp_amount)
		pipe.expire(daily_xp_key, 8 * 86400)  # 8 days — covers the 7-day window + 1 buffer

		# Subject-specific leaderboards (if context available)
		if subject_id:
			daily_subj_key = lb_daily_key(date_str, subject_id)
			weekly_subj_key = lb_weekly_key(friday, subject_id)

			pipe.zincrby(daily_subj_key, xp_amount, player_id)
			pipe.expire(daily_subj_key, DAILY_KEY_TTL)

			pipe.zincrby(weekly_subj_key, xp_amount, player_id)
			pipe.expire(weekly_subj_key, WEEKLY_KEY_TTL)

		# Plan-scoped leaderboards (dual-write for plan-scoped rankings)
		if plan_id:
			# Plan-scoped daily
			plan_daily = lb_daily_plan_key(date_str, plan_id)
			pipe.zincrby(plan_daily, xp_amount, player_id)
			pipe.expire(plan_daily, PLAN_DAILY_KEY_TTL)

			# Plan-scoped weekly
			plan_weekly = lb_weekly_plan_key(friday, plan_id)
			pipe.zincrby(plan_weekly, xp_amount, player_id)
			pipe.expire(plan_weekly, PLAN_WEEKLY_KEY_TTL)

			# Plan-scoped subject variants
			if subject_id:
				plan_daily_subj = lb_daily_plan_key(date_str, plan_id, subject_id)
				pipe.zincrby(plan_daily_subj, xp_amount, player_id)
				pipe.expire(plan_daily_subj, PLAN_DAILY_KEY_TTL)

				plan_weekly_subj = lb_weekly_plan_key(friday, plan_id, subject_id)
				pipe.zincrby(plan_weekly_subj, xp_amount, player_id)
				pipe.expire(plan_weekly_subj, PLAN_WEEKLY_KEY_TTL)

		await pipe.execute()

		logger.debug(
			"leaderboards_updated",
			player_id=player_id,
			xp_amount=xp_amount,
			subject_id=subject_id,
			plan_id=plan_id,
		)
