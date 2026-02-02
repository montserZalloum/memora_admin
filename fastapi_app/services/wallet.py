"""Wallet service for Redis-backed XP and streak tracking."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

from fastapi_app.core.constants import DIRTY_WALLETS_KEY

logger = structlog.get_logger()

# Asia/Amman timezone for consistent streak boundary
# Per CONTEXT.md: All players use single timezone (no per-player config)
AMMAN_TZ = ZoneInfo("Asia/Amman")


def get_amman_today() -> str:
	"""Get today's date in Asia/Amman timezone as YYYY-MM-DD."""
	return datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")


def get_amman_yesterday() -> str:
	"""Get yesterday's date in Asia/Amman timezone as YYYY-MM-DD."""
	yesterday = datetime.now(AMMAN_TZ) - timedelta(days=1)
	return yesterday.strftime("%Y-%m-%d")


# Lua script for atomic streak update with date comparison
# Per RESEARCH.md: Use Lua for read-check-write atomicity
STREAK_UPDATE_SCRIPT = """
local key = KEYS[1]
local today = ARGV[1]
local yesterday = ARGV[2]
local is_replay = tonumber(ARGV[3])

-- Get current values
local current_streak = tonumber(redis.call('HGET', key, 'streak') or 0)
local streak_date = redis.call('HGET', key, 'streak_date') or ''

-- Replays don't update streak per CONTEXT.md
if is_replay == 1 then
    return {current_streak, 0}
end

-- Same day - no streak change
if streak_date == today then
    return {current_streak, 0}
end

-- Consecutive day - increment
if streak_date == yesterday then
    current_streak = current_streak + 1
    redis.call('HSET', key, 'streak', current_streak)
    redis.call('HSET', key, 'streak_date', today)
    return {current_streak, 1}
end

-- Missed day(s) or first completion - reset to 1
redis.call('HSET', key, 'streak', 1)
redis.call('HSET', key, 'streak_date', today)
return {1, 1}
"""


class WalletService:
	"""Manages player wallet via Redis hash.

	Key pattern: memora:wallet:{player_id}
	Fields: xp (int), streak (int), streak_date (YYYY-MM-DD string)

	Per CONTEXT.md:
	- XP accumulates atomically via HINCRBY
	- Streaks track consecutive days of activity
	- Replays don't count toward streak maintenance

	Operations:
	- get_wallet: HGETALL O(N) - returns xp and streak
	- award_xp: HINCRBY O(1) - atomic XP increment
	- update_streak: Lua script O(1) - atomic streak update with date check
	"""

	def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
		self.redis = redis_client
		self.prefix = key_prefix
		self._streak_script = None

	def _wallet_key(self, player_id: str) -> str:
		"""Generate Redis key for player's wallet hash.

		Args:
			player_id: Player's user ID

		Returns:
			Redis key string
		"""
		return f"{self.prefix}wallet:{player_id}"

	async def get_wallet(self, player_id: str) -> dict:
		"""Get wallet data (xp, streak).

		Returns defaults for new players (no wallet hash yet).

		Args:
			player_id: Player's user ID

		Returns:
			Dict with xp (int) and streak (int)
		"""
		key = self._wallet_key(player_id)
		data = await self.redis.hgetall(key)

		# Handle bytes/str response from Redis
		# (redis-py returns bytes by default unless decode_responses=True)
		xp_raw = data.get(b"xp") or data.get("xp")
		streak_raw = data.get(b"streak") or data.get("streak")

		return {
			"xp": int(xp_raw) if xp_raw else 0,
			"streak": int(streak_raw) if streak_raw else 0,
		}

	async def award_xp(self, player_id: str, amount: int) -> int:
		"""Atomically add XP and return new total.

		Uses HINCRBY for atomic increment - never GET+add+SET.
		Per RESEARCH.md: Prevents race conditions on concurrent completions.

		Args:
			player_id: Player's user ID
			amount: XP to award (can be negative for corrections)

		Returns:
			New total XP after increment
		"""
		key = self._wallet_key(player_id)
		new_total = await self.redis.hincrby(key, "xp", amount)

		# Mark dirty for background sync to MariaDB
		await self.redis.sadd(DIRTY_WALLETS_KEY, player_id)

		logger.debug(
			"xp_awarded",
			player_id=player_id,
			amount=amount,
			new_total=new_total,
		)

		return new_total

	async def _get_streak_script(self):
		"""Get or register Lua script (cached).

		Follows RateLimiter._get_script pattern.
		"""
		if self._streak_script is None:
			self._streak_script = self.redis.register_script(STREAK_UPDATE_SCRIPT)
		return self._streak_script

	async def update_streak(self, player_id: str, is_replay: bool) -> tuple[int, bool]:
		"""Update streak atomically using Lua script.

		Per CONTEXT.md:
		- Daily requirement: 1 lesson completion maintains streak
		- Missed day: Streak resets to 0 immediately
		- Replay policy: Replays do NOT count toward maintaining streak

		Args:
			player_id: Player's user ID
			is_replay: Whether this is a replay completion

		Returns:
			Tuple of (current_streak, was_updated)
			- current_streak: Streak count after operation
			- was_updated: True if streak_date was changed
		"""
		script = await self._get_streak_script()
		key = self._wallet_key(player_id)

		today = get_amman_today()
		yesterday = get_amman_yesterday()

		# Lua returns [streak, was_updated] as integers
		result = await script(
			keys=[key],
			args=[today, yesterday, 1 if is_replay else 0],
		)

		streak = int(result[0])
		was_updated = bool(result[1])

		# Mark dirty if streak was updated (either incremented or reset)
		if was_updated:
			await self.redis.sadd(DIRTY_WALLETS_KEY, player_id)

		logger.debug(
			"streak_updated",
			player_id=player_id,
			is_replay=is_replay,
			streak=streak,
			was_updated=was_updated,
			today=today,
		)

		return streak, was_updated
