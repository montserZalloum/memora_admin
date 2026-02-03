"""Leaderboard service for Redis ZSET-backed XP rankings.

Per CONTEXT.md (Phase 10):
- Three leaderboard types: daily, weekly, all-time
- Tie-breaking: earlier achiever wins (composite score)
- Dense ranking: tied players share same rank number
- Optional subject filtering for class-specific competitions

Key patterns:
- memora:lb:alltime[:subject:{id}]
- memora:lb:daily:{YYYY-MM-DD}[:subject:{id}]
- memora:lb:weekly:{YYYY-Www}[:subject:{id}]
"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

logger = structlog.get_logger()

# Asia/Amman timezone for consistent daily/weekly boundaries
# Per CONTEXT.md: Daily resets at midnight, weekly resets Friday midnight
AMMAN_TZ = ZoneInfo("Asia/Amman")

# Key prefix for all leaderboard keys
LB_PREFIX = "memora:lb"


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
		- Weekly: resets Friday midnight Asia/Amman (uses ISO week)
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
			base = f"{LB_PREFIX}:alltime"
		elif lb_type == "daily":
			date_str = now.strftime("%Y-%m-%d")
			base = f"{LB_PREFIX}:daily:{date_str}"
		elif lb_type == "weekly":
			# ISO week format: 2026-W06
			# %G = ISO year, %V = ISO week number (01-53)
			week_str = now.strftime("%G-W%V")
			base = f"{LB_PREFIX}:weekly:{week_str}"
		else:
			raise ValueError(f"Invalid leaderboard type: {lb_type}")

		if subject_id:
			return f"{base}:subject:{subject_id}"
		return base

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
		- Include distance to next tier (XP to pass player above)
		- Unranked users (0 XP) treated as tied for last place
		- Dense ranking: count distinct higher scores for true rank

		Args:
			player_id: Player's user ID
			lb_type: One of "daily", "weekly", "alltime"
			subject_id: Optional subject filter
			neighbor_count: Players above/below to include (default 2)

		Returns:
			Dict with rank, xp, xp_to_next, neighbors, or None if error
		"""
		key = self._get_key(lb_type, subject_id)

		# Get player's position and score (None if not in leaderboard)
		result = await self.redis.zrevrank(key, player_id, withscore=True)

		# Get total players for context
		total = await self.redis.zcard(key)

		# Handle unranked users (never earned XP in this period)
		if result is None:
			logger.debug(
				"leaderboard_unranked_user",
				player_id=player_id,
				lb_type=lb_type,
				total_players=total,
			)
			return {
				"rank": total + 1,  # Last place
				"xp": 0,
				"xp_to_next": None,  # No meaningful target
				"neighbors": [],
				"total_players": total,
			}

		position, score = result
		xp = int(score)

		# Calculate dense rank: count scores strictly greater than mine
		# ZCOUNT with exclusive lower bound "(score" counts scores > mine
		higher_count = await self.redis.zcount(key, f"({score}", "+inf")
		dense_rank = higher_count + 1

		# Get neighbors around position
		start = max(0, position - neighbor_count)
		stop = position + neighbor_count

		neighbors_raw = await self.redis.zrange(
			key,
			start,
			stop,
			desc=True,
			withscores=True,
		)

		# Build neighbor entries with dense ranks
		neighbors = []
		for neighbor_id, neighbor_score in neighbors_raw:
			neighbor_xp = int(neighbor_score)

			# Calculate dense rank for neighbor
			neighbor_higher = await self.redis.zcount(key, f"({neighbor_score}", "+inf")
			neighbor_rank = neighbor_higher + 1

			# Handle bytes response
			nid = neighbor_id.decode() if isinstance(neighbor_id, bytes) else neighbor_id

			neighbors.append({
				"rank": neighbor_rank,
				"player_id": nid,
				"xp": neighbor_xp,
				"is_me": nid == player_id,
			})

		# Calculate XP to next tier (pass player above)
		xp_to_next = None
		if position > 0:
			# Get player immediately above
			above = await self.redis.zrange(
				key,
				position - 1,
				position - 1,
				desc=True,
				withscores=True,
			)
			if above:
				above_xp = int(above[0][1])
				# +1 because we need to exceed, not just match
				xp_to_next = above_xp - xp + 1

		logger.debug(
			"leaderboard_rank_fetched",
			player_id=player_id,
			lb_type=lb_type,
			dense_rank=dense_rank,
			xp=xp,
			xp_to_next=xp_to_next,
			neighbor_count=len(neighbors),
		)

		return {
			"rank": dense_rank,
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

		# All-time: Use composite score (total XP with tie-breaking)
		alltime_key = self._get_key("alltime")
		await self.redis.zadd(alltime_key, {player_id: composite_score})

		# Daily: Increment by amount (tracks today's earnings)
		daily_key = self._get_key("daily")
		await self.redis.zincrby(daily_key, xp_amount, player_id)

		# Weekly: Increment by amount (tracks this week's earnings)
		weekly_key = self._get_key("weekly")
		await self.redis.zincrby(weekly_key, xp_amount, player_id)

		# Subject-specific leaderboards (if context available)
		if subject_id:
			# All-time for subject
			await self.redis.zadd(
				self._get_key("alltime", subject_id),
				{player_id: composite_score},
			)
			# Daily for subject
			await self.redis.zincrby(
				self._get_key("daily", subject_id),
				xp_amount,
				player_id,
			)
			# Weekly for subject
			await self.redis.zincrby(
				self._get_key("weekly", subject_id),
				xp_amount,
				player_id,
			)

		logger.debug(
			"leaderboards_updated",
			player_id=player_id,
			xp_amount=xp_amount,
			new_total_xp=new_total_xp,
			subject_id=subject_id,
			composite_score=composite_score,
		)
