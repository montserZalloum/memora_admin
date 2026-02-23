"""Leaderboard service for Redis ZSET-backed XP rankings.

Per CONTEXT.md (Phase 10):
- Three leaderboard types: daily, weekly, all-time
- Tie-breaking: earlier achiever wins (composite score)
- Dense ranking: tied players share same rank number
- Optional subject filtering for class-specific competitions

Key patterns:
- memora:lb:alltime[:subject:{id}]
- memora:lb:daily:{YYYY-MM-DD}[:subject:{id}]
- memora:lb:weekly:{YYYY-MM-DD}[:subject:{id}]  (date = Friday that starts the Islamic week)
"""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import LB_PREFIX, daily_xp_key as _daily_xp_key_fn, lb_alltime_key, lb_daily_key, lb_weekly_key

logger = structlog.get_logger()

# Asia/Amman timezone for consistent daily/weekly boundaries
# Per CONTEXT.md: Daily resets at midnight, weekly resets Friday midnight
AMMAN_TZ = ZoneInfo("Asia/Amman")

# TTLs for periodic leaderboard keys (prevents unbounded accumulation after Redis data loss)
DAILY_KEY_TTL = 30 * 86400  # 30 days
WEEKLY_KEY_TTL = 90 * 86400  # 90 days


def compute_composite_score(xp: int, timestamp: float | None = None) -> float:
	"""Compute composite score for leaderboard ranking with tie-breaking.

	Per CONTEXT.md: "Earlier achiever wins - whoever reached that XP first ranks higher"

	The score encodes:
	- Integer part: XP value (for primary ranking)
	- Fractional part: Inverted timestamp (for tie-breaking)

	Since ZREVRANGE/ZRANGE(desc=True) sorts descending, higher scores rank better.
	For same XP, earlier timestamp = smaller fractional part = ranks higher.

	Formula: xp + (1.0 - (timestamp % 1_000_000_000) / 1_000_000_000)

	Args:
		xp: XP value to encode
		timestamp: Unix timestamp (defaults to current time)

	Returns:
		Composite score as float

	Example:
		>>> score = compute_composite_score(100)
		>>> int(score)  # Integer part is the XP
		100
	"""
	if timestamp is None:
		timestamp = time.time()

	# Use modulo to keep timestamp in manageable range (cycles every ~31 years)
	# Invert so earlier timestamps produce smaller fractions
	inverted = 1.0 - (timestamp % 1_000_000_000) / 1_000_000_000

	return float(xp) + inverted


class LeaderboardService:
	"""Manages XP leaderboards via Redis sorted sets (ZSET).

	Per CONTEXT.md:
	- Three types: daily, weekly, all-time
	- Optional subject filtering
	- Tie-breaking: earlier achiever wins (composite score)
	- Dense ranking: tied players share rank

	Operations:
	- get_top: ZRANGE with desc=True for top N players
	- get_my_rank: ZREVRANK + ZCOUNT for user position with dense rank
	- update_leaderboards: ZADD for all-time, ZINCRBY for daily/weekly
	"""

	def __init__(self, redis_client: redis.Redis):
		"""Initialize LeaderboardService.

		Args:
			redis_client: Redis async client for ZSET operations
		"""
		self.redis = redis_client

	def _get_key(self, lb_type: str, subject_id: str | None = None) -> str:
		"""Generate Redis key for a leaderboard.

		Per CONTEXT.md:
		- Daily: resets at midnight Asia/Amman
		- Weekly: resets Friday midnight Asia/Amman (Islamic week: Fri-Thu)
		- All-time: no reset

		Args:
			lb_type: One of "daily", "weekly", "alltime"
			subject_id: Optional subject for filtered leaderboards

		Returns:
			Redis key string

		Raises:
			ValueError: If lb_type is invalid
		"""
		now = datetime.now(AMMAN_TZ)

		if lb_type == "alltime":
			return lb_alltime_key(subject_id)
		elif lb_type == "daily":
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

	async def get_top(
		self,
		lb_type: str,
		limit: int = 10,
		subject_id: str | None = None,
	) -> list[dict]:
		"""Get top N players from a leaderboard.

		Uses ZRANGE with desc=True for O(log N + M) where M is returned elements.

		Per CONTEXT.md:
		- Dense ranking: tied players share same rank number
		- rank, player_id, xp returned (display_name/avatar from profile service)

		Args:
			lb_type: One of "daily", "weekly", "alltime"
			limit: Maximum entries to return (default 10)
			subject_id: Optional subject filter

		Returns:
			List of dicts with rank, player_id, xp
		"""
		key = self._get_key(lb_type, subject_id)

		# ZRANGE with desc=True returns [(member, score), ...]
		# Equivalent to deprecated ZREVRANGE
		results = await self.redis.zrange(
			key,
			0,
			limit - 1,
			desc=True,
			withscores=True,
		)

		entries = []
		current_rank = 1
		prev_xp = None

		for idx, (player_id, score) in enumerate(results):
			# Extract XP from composite score (integer part)
			xp = int(score)

			# Dense ranking: same XP = same rank
			# Only increment rank when XP changes
			if prev_xp is not None and xp != prev_xp:
				current_rank = idx + 1

			# Handle bytes response from Redis (unless decode_responses=True)
			pid = player_id.decode() if isinstance(player_id, bytes) else player_id

			entries.append({
				"rank": current_rank,
				"player_id": pid,
				"xp": xp,
			})
			prev_xp = xp

		logger.debug(
			"leaderboard_top_fetched",
			lb_type=lb_type,
			subject_id=subject_id,
			limit=limit,
			returned=len(entries),
		)

		return entries

	async def get_my_rank(
		self,
		player_id: str,
		lb_type: str,
		subject_id: str | None = None,
		neighbor_count: int = 2,
	) -> dict | None:
		"""Get player's rank with surrounding neighbors.

		Per CONTEXT.md:
		- Include +/-2 neighbors for context around user's position
		- Include distance to next tier (XP to match player in rank above)
		- Unranked users (0 XP) treated as tied for last place
		- Competition ranking: tied players share rank, next rank = count of players above + 1

		Args:
			player_id: Player's user ID
			lb_type: One of "daily", "weekly", "alltime"
			subject_id: Optional subject filter
			neighbor_count: Players above/below to include (default 2)

		Returns:
			Dict with rank, xp, xp_to_next, neighbors, or None if error
		"""
		key = self._get_key(lb_type, subject_id)

		# Stage 1: Get position, total, score in one pipeline (1 RTT)
		pipe = self.redis.pipeline()
		pipe.zrevrank(key, player_id)
		pipe.zcard(key)
		pipe.zscore(key, player_id)
		position, total, score = await pipe.execute()

		# Handle unranked users (never earned XP in this period)
		if position is None:
			# Provide xp_to_next: XP needed to match the last-ranked player
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
				"rank": total + 1,
				"xp": 0,
				"xp_to_next": xp_to_next,
				"neighbors": [],
				"total_players": total,
			}

		xp = int(score) if score is not None else 0

		# Stage 2: Get rank, neighbors, and next-tier data in one pipeline (1 RTT)
		# Competition rank uses XP boundary (not exact composite score) so that
		# alltime composite tie-breaking doesn't split same-XP players into
		# different ranks. ZCOUNT(xp+1, +inf) counts players with strictly higher XP.
		start = max(0, position - neighbor_count)
		stop = position + neighbor_count

		pipe = self.redis.pipeline()
		pipe.zcount(key, xp + 1, "+inf")
		pipe.zrange(key, start, stop, desc=True, withscores=True)
		pipe.zrangebyscore(key, xp + 1, "+inf", start=0, num=1, withscores=True)
		higher_count, neighbors_raw, above_entries = await pipe.execute()

		my_rank = higher_count + 1

		# xp_to_next: XP needed to match the next higher tier
		# Matching is sufficient since ties share rank (competition ranking)
		xp_to_next = None
		if above_entries:
			above_xp = int(above_entries[0][1])
			xp_to_next = above_xp - xp

		# Stage 3: Compute neighbor ranks via pipeline (1 RTT)
		# Use XP boundary for consistency with main rank calculation
		pipe = self.redis.pipeline()
		for _, neighbor_score in neighbors_raw:
			neighbor_xp = int(neighbor_score)
			pipe.zcount(key, neighbor_xp + 1, "+inf")
		rank_results = await pipe.execute()

		neighbors = []
		for i, (neighbor_id, neighbor_score) in enumerate(neighbors_raw):
			neighbor_xp = int(neighbor_score)
			neighbor_rank = rank_results[i] + 1

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
		new_total_xp: int,
		subject_id: str | None = None,
	) -> None:
		"""Update all relevant leaderboards after XP award.

		Called after wallet.award_xp() to maintain leaderboard consistency.

		Per CONTEXT.md:
		- All-time: Use composite score (total XP + timestamp for tie-breaking)
		- Daily/Weekly: Increment by amount earned (not total)

		Args:
			player_id: Player's user ID
			xp_amount: XP just awarded (for daily/weekly increment)
			new_total_xp: Total XP after award (for all-time composite score)
			subject_id: Optional subject for filtered leaderboards
		"""
		timestamp = time.time()
		composite_score = compute_composite_score(new_total_xp, timestamp)

		alltime_key = self._get_key("alltime")
		daily_key = self._get_key("daily")
		weekly_key = self._get_key("weekly")

		# Single pipeline for all leaderboard updates (1 RTT instead of 3-6)
		pipe = self.redis.pipeline()

		# All-time: composite score (no TTL — persistent)
		pipe.zadd(alltime_key, {player_id: composite_score})

		# Daily: increment + TTL
		pipe.zincrby(daily_key, xp_amount, player_id)
		pipe.expire(daily_key, DAILY_KEY_TTL)

		# Weekly: increment + TTL
		pipe.zincrby(weekly_key, xp_amount, player_id)
		pipe.expire(weekly_key, WEEKLY_KEY_TTL)

		# Per-player daily XP summary hash (MariaDB-backed durability for activity chart)
		# Stored separately from the ranked ZSET so it can survive Redis data loss and
		# be recovered from MariaDB (synced by sync_dirty_wallets every minute).
		amman_date_str = datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")
		daily_xp_key = _daily_xp_key_fn(player_id)
		pipe.hincrby(daily_xp_key, amman_date_str, xp_amount)
		pipe.expire(daily_xp_key, 8 * 86400)  # 8 days — covers the 7-day window + 1 buffer

		# Subject-specific leaderboards (if context available)
		if subject_id:
			alltime_subj_key = self._get_key("alltime", subject_id)
			daily_subj_key = self._get_key("daily", subject_id)
			weekly_subj_key = self._get_key("weekly", subject_id)

			pipe.zadd(alltime_subj_key, {player_id: composite_score})

			pipe.zincrby(daily_subj_key, xp_amount, player_id)
			pipe.expire(daily_subj_key, DAILY_KEY_TTL)

			pipe.zincrby(weekly_subj_key, xp_amount, player_id)
			pipe.expire(weekly_subj_key, WEEKLY_KEY_TTL)

		await pipe.execute()

		logger.debug(
			"leaderboards_updated",
			player_id=player_id,
			xp_amount=xp_amount,
			new_total_xp=new_total_xp,
			subject_id=subject_id,
			composite_score=composite_score,
		)
