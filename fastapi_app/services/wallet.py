"""Wallet service for Redis-backed XP and streak tracking."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

from fastapi_app.core.constants import DIRTY_WALLETS_KEY
from fastapi_app.core.redis_keys import wallet_key as _wallet_key_fn
from fastapi_app.services.hydration import guarded_hydrate

if TYPE_CHECKING:
	from fastapi_app.services.frappe_client import FrappeClient

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


def calculate_xp_award(
	base_xp: int,
	lesson_xp: int,
	current_streak: int,
	max_multiplier_percent: int,
	is_replay: bool,
	replay_xp: int,
	hearts_remaining: int = 0,
	xp_per_heart: int = 0,
) -> int:
	"""Calculate XP to award for lesson completion.

	Per Phase 20:
	- Fresh completion: (lesson_xp or base_xp) + hearts_bonus
	- Hearts bonus: remaining_hearts * xp_per_heart (added before streak multiplier)
	- Replay: fixed replay_xp amount (no hearts bonus)
	- Streak multiplier: +1% per day, capped at max_multiplier_percent
	- Streak multiplier applies to BOTH fresh and replay
	"""
	if is_replay:
		base = replay_xp
	else:
		base = lesson_xp if lesson_xp > 0 else base_xp
		# Hearts bonus: remaining hearts * xp_per_heart
		hearts_bonus = hearts_remaining * xp_per_heart
		base += hearts_bonus

	# Apply streak multiplier (linear +1% per day, capped)
	capped_streak = min(current_streak, max_multiplier_percent)
	multiplier = 1.0 + (capped_streak * 0.01)

	# Floor the result per RESEARCH.md recommendation
	return int(base * multiplier)


# Lua script for atomic streak update with date comparison
# Per RESEARCH.md: Use Lua for read-check-write atomicity
STREAK_UPDATE_SCRIPT = """
local key = KEYS[1]
local today = ARGV[1]
local yesterday = ARGV[2]
local is_replay = tonumber(ARGV[3])

-- Get current values (safe: HGET returns false when field missing)
local raw_streak = redis.call('HGET', key, 'streak')
local current_streak = (raw_streak and tonumber(raw_streak)) or 0
local raw_date = redis.call('HGET', key, 'streak_date')
local streak_date = raw_date or ''

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

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient | None = None,
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self._streak_script = None

	def _wallet_key(self, player_id: str) -> str:
		"""Generate Redis key for player's wallet hash.

		Args:
			player_id: Player's user ID

		Returns:
			Redis key string
		"""
		return _wallet_key_fn(player_id)

	async def ensure_hydrated(self, player_id: str) -> None:
		"""Ensure wallet hash exists in Redis, hydrating from MariaDB if missing.

		Uses distributed lock + semaphore to prevent thundering herd after Redis flush.
		Only one request per player hydrates at a time; others wait for the result.

		Args:
			player_id: Player's user ID
		"""
		key = self._wallet_key(player_id)

		# Fast path: wallet hash already exists in Redis
		if await self.redis.exists(key):
			return

		# No Frappe client — can't hydrate
		if not self.frappe:
			logger.warning(
				"wallet_hydration_skipped",
				player_id=player_id,
				reason="no_frappe_client",
			)
			return

		async def _do_hydrate() -> None:
			try:
				result = await self.frappe.call(
					"memora_admin.api.wallet.get_player_wallet",
					{"player_id": player_id},
				)

				if result and isinstance(result, dict):
					total_xp = int(result.get("total_xp", 0))
					current_streak = int(result.get("current_streak", 0))

					if total_xp > 0 or current_streak > 0:
						mapping = {"xp": total_xp, "streak": current_streak}
						await self.redis.hset(key, mapping=mapping)
						logger.info(
							"wallet_hydrated_from_mariadb",
							player_id=player_id,
							xp=total_xp,
							streak=current_streak,
						)
					else:
						logger.debug("wallet_hydration_empty", player_id=player_id)

			except Exception as e:
				logger.error(
					"wallet_hydration_failed",
					player_id=player_id,
					error=str(e),
				)

		await guarded_hydrate(self.redis, key, _do_hydrate)

	async def get_wallet(self, player_id: str) -> dict:
		"""Get wallet data (xp, streak).

		Hydrates from MariaDB if wallet is missing from Redis (e.g., after cache flush).
		Returns defaults for new players (no wallet record in either store).

		Args:
			player_id: Player's user ID

		Returns:
			Dict with xp (int) and streak (int)
		"""
		key = self._wallet_key(player_id)
		data = await self.redis.hgetall(key)

		# If wallet is missing from Redis, try to hydrate from MariaDB
		if not data:
			await self.ensure_hydrated(player_id)
			data = await self.redis.hgetall(key)

		# decode_responses=True on the connection pool ensures string keys
		xp_raw = data.get("xp")
		streak_raw = data.get("streak")

		return {
			"xp": int(xp_raw) if xp_raw else 0,
			"streak": int(streak_raw) if streak_raw else 0,
		}

	async def award_xp(self, player_id: str, amount: int) -> int:
		"""Atomically add XP and return new total.

		Ensures wallet is hydrated from MariaDB before incrementing,
		preventing XP reset after Redis cache flush.

		Uses HINCRBY for atomic increment - never GET+add+SET.
		Per RESEARCH.md: Prevents race conditions on concurrent completions.

		Args:
			player_id: Player's user ID
			amount: XP to award (can be negative for corrections)

		Returns:
			New total XP after increment
		"""
		key = self._wallet_key(player_id)

		# Ensure wallet exists in Redis before incrementing.
		# Without this, HINCRBY on a missing key starts from 0, resetting XP.
		await self.ensure_hydrated(player_id)

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

		Ensures wallet is hydrated before streak update to prevent
		losing streak data after Redis cache flush.

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
		# Ensure wallet exists in Redis before streak update.
		# The Lua script reads streak/streak_date from the hash;
		# if missing after cache flush, it would incorrectly reset to 1.
		await self.ensure_hydrated(player_id)

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
