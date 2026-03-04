# Copyright (c) 2026, corex and contributors
"""Game session service with atomic session lifecycle via Lua script."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis
import structlog

from fastapi_app.core.constants import (
	DIRTY_PROGRESS_KEY,
	DIRTY_WALLETS_KEY,
	GAME_SESSION_TTL,
	INTERACTION_BUFFER_KEY,
)
from fastapi_app.core.redis_keys import game_session_completion_key as _game_session_completion_key_fn
from fastapi_app.core.redis_keys import game_session_key as _game_session_key_fn
from fastapi_app.core.redis_keys import progress_key as _progress_key_fn
from fastapi_app.core.redis_keys import wallet_key as _wallet_key_fn
from fastapi_app.models.game_session import GameSession

logger = structlog.get_logger()

SESSION_END_RESPONSE_TTL = 300

# Lua script for atomic session start that force-closes any existing
# KEYS[1] = memora:gamesession:{user_id}
# ARGV[1..6] = session_id, lesson_id, subject_id, device_id, timestamp, ttl
#
# Returns: {1, session_id}
START_SESSION_SCRIPT = """
local key = KEYS[1]
local session_id = ARGV[1]
local lesson_id = ARGV[2]
local subject_id = ARGV[3]
local device_id = ARGV[4]
local timestamp = ARGV[5]
local ttl = tonumber(ARGV[6])

-- Delete any existing session (force-close, no notification per CONTEXT.md)
redis.call('DEL', key)

-- Create new session hash with all fields
redis.call('HSET', key,
    'session_id', session_id,
    'lesson_id', lesson_id,
    'subject_id', subject_id,
    'device_id', device_id,
    'started_at', timestamp)

-- Set TTL (1 hour)
redis.call('EXPIRE', key, ttl)

return {1, session_id}
"""

# Lua script for atomic session completion
# Deletes session, sets progress bit, marks dirty, and pushes interactions in single call.
#
# KEYS[1] = memora:gamesession:{user_id}          -- session hash
# KEYS[2] = memora:progress:{user_id}:{subject_id}:v{version}  -- progress bitmap
# KEYS[3] = memora:dirty:progress                  -- dirty progress set
# KEYS[4] = memora:buffer:interactions             -- interaction buffer list
# KEYS[5] = memora:wallet:{user_id}                -- wallet hash
# KEYS[6] = memora:dirty:wallets                   -- dirty wallets set
# KEYS[7] = memora:gamesession:complete:{user_id}:{session_id}  -- response cache
#
# ARGV[1] = bit_index (int)
# ARGV[2] = dirty_progress_member string (e.g. "user_id:subject_id:v1")
# ARGV[3] = expected_session_id
# ARGV[4] = base_xp
# ARGV[5] = lesson_xp
# ARGV[6] = replay_xp
# ARGV[7] = max_multiplier_percent
# ARGV[8] = hearts_remaining
# ARGV[9] = xp_per_heart
# ARGV[10] = today (Asia/Amman)
# ARGV[11] = yesterday (Asia/Amman)
# ARGV[12] = dirty_wallet_member (player_id)
# ARGV[13] = completion cache ttl (seconds)
# ARGV[14..N] = JSON interaction strings (one per stage)
#
# Returns: {status, ...}
#   - status=0: no active session and no cached completion response
#   - status=1: success
#   - status=2: duplicate completion (cached response exists)
#   - status=3: stale request (different active session_id)
SESSION_COMPLETE_SCRIPT = """
local cached = redis.call('GET', KEYS[7])
if cached then
    return {2, cached}
end

-- Read session
local session = redis.call('HGETALL', KEYS[1])
if #session == 0 then
    return {0}
end

local current_session_id = redis.call('HGET', KEYS[1], 'session_id')
if not current_session_id then
    return {0}
end

if current_session_id ~= ARGV[3] then
    return {3, current_session_id}
end

-- Delete session atomically
redis.call('DEL', KEYS[1])

-- Set progress bit, get previous value (0=first, 1=replay)
local prev = redis.call('SETBIT', KEYS[2], tonumber(ARGV[1]), 1)

-- Refresh TTL on progress key (literal 172800 = PROGRESS_KEY_TTL, cross-ref redis_keys.py)
redis.call('EXPIRE', KEYS[2], 172800)

-- Mark dirty for background sync
redis.call('SADD', KEYS[3], ARGV[2])

-- Batch RPUSH all interactions (single call, not N calls)
if #ARGV > 13 then
    local interactions = {}
    for i = 14, #ARGV do
        interactions[#interactions + 1] = ARGV[i]
    end
    redis.call('RPUSH', KEYS[4], unpack(interactions))
end

-- Update streak in the same atomic flow
local raw_streak = redis.call('HGET', KEYS[5], 'streak')
local current_streak = (raw_streak and tonumber(raw_streak)) or 0
local raw_date = redis.call('HGET', KEYS[5], 'streak_date')
local streak_date = raw_date or ''

if prev == 0 then
    if streak_date == ARGV[10] then
        -- Same day: no streak change
    elseif streak_date == ARGV[11] then
        current_streak = current_streak + 1
        redis.call('HSET', KEYS[5], 'streak', current_streak)
        redis.call('HSET', KEYS[5], 'streak_date', ARGV[10])
    else
        current_streak = 1
        redis.call('HSET', KEYS[5], 'streak', current_streak)
        redis.call('HSET', KEYS[5], 'streak_date', ARGV[10])
    end
end

local base_xp = tonumber(ARGV[4])
local lesson_xp = tonumber(ARGV[5])
local replay_xp = tonumber(ARGV[6])
local max_multiplier_percent = tonumber(ARGV[7])
local hearts_remaining = tonumber(ARGV[8])
local xp_per_heart = tonumber(ARGV[9])

local base_amount
if prev == 1 then
    base_amount = replay_xp
else
    if lesson_xp > 0 then
        base_amount = lesson_xp
    else
        base_amount = base_xp
    end
    base_amount = base_amount + (hearts_remaining * xp_per_heart)
end

local capped_streak = math.min(current_streak, max_multiplier_percent)
local multiplier = 1.0 + (capped_streak * 0.01)
local xp_awarded = math.floor(base_amount * multiplier)
local new_total_xp = redis.call('HINCRBY', KEYS[5], 'xp', xp_awarded)

-- Refresh TTL on wallet key (literal 172800 = WALLET_KEY_TTL, cross-ref redis_keys.py)
redis.call('EXPIRE', KEYS[5], 172800)
redis.call('SADD', KEYS[6], ARGV[12])

local response = '{"success":true,"xp_awarded":' .. xp_awarded ..
    ',"is_replay":' .. (prev == 1 and 'true' or 'false') ..
    ',"streak":' .. current_streak ..
    ',"is_duplicate":false' ..
    ',"session_id":"' .. current_session_id .. '"' ..
    ',"hearts_remaining":' .. hearts_remaining ..
    ',"new_total_xp":' .. new_total_xp ..
    '}'

redis.call('SET', KEYS[7], response, 'EX', tonumber(ARGV[13]))

return {1, response}
"""


class GameSessionService:
	"""Manages game session lifecycle with Redis.

	Per CONTEXT.md:
	- One session per user (new session force-closes existing)
	- Sessions auto-expire after 1 hour (GAME_SESSION_TTL)
	- Tracks lesson being played for progress attribution

	Key pattern: memora:gamesession:{user_id}
	Fields: session_id, lesson_id, subject_id, device_id, started_at

	Operations:
	- start_session: Lua script O(1) - atomic create with force-close
	- get_active_session: HGETALL O(N) - get current session
	- end_session: GET+DEL O(1) - end session and return data
	- has_active_session: EXISTS O(1) - check if session exists
	- complete_session: Lua script O(1) - atomic completion + wallet update + cached response
	"""

	def __init__(self, redis_client: redis.Redis):
		"""Initialize GameSessionService.

		Args:
			redis_client: Async Redis client
		"""
		self.redis = redis_client
		self._start_script: Any | None = None
		self._complete_script: Any | None = None

	def _session_key(self, user_id: str) -> str:
		"""Get Redis key for user's game session.

		Args:
			user_id: Player's user ID

		Returns:
			Redis key string
		"""
		return _game_session_key_fn(user_id)

	def _completion_key(self, user_id: str, session_id: str) -> str:
		"""Get Redis key for a recently completed session response."""
		return _game_session_completion_key_fn(user_id, session_id)

	def _wallet_key(self, user_id: str) -> str:
		"""Get Redis key for the player's wallet hash."""
		return _wallet_key_fn(user_id)

	async def _get_start_script(self) -> Any:
		"""Get or create the start session Lua script (lazy-loaded and cached).

		Returns:
			Redis Script object
		"""
		if self._start_script is None:
			self._start_script = self.redis.register_script(START_SESSION_SCRIPT)
		return self._start_script

	async def _get_complete_script(self) -> Any:
		"""Get or create the complete session Lua script (lazy-loaded and cached).

		Returns:
			Redis Script object
		"""
		if self._complete_script is None:
			self._complete_script = self.redis.register_script(SESSION_COMPLETE_SCRIPT)
		return self._complete_script

	async def complete_session(
		self,
		user_id: str,
		session_id: str,
		bit_index: int,
		subject_id: str,
		version: int,
		base_xp: int,
		lesson_xp: int,
		replay_xp: int,
		max_multiplier_percent: int,
		hearts_remaining: int,
		xp_per_heart: int,
		today: str,
		yesterday: str,
		interaction_jsons: list[str],
	) -> tuple[str, str | None, str | None]:
		"""Atomically complete session: delete session, set progress bit, push interactions.

		Single Lua script execution = 1 Redis round-trip.

		Args:
			user_id: Player's user ID
			session_id: Expected active session identifier from the client
			bit_index: Lesson's position in progress bitmap
			subject_id: Subject identifier
			version: Bitmap version
			base_xp: Default lesson XP from settings
			lesson_xp: Lesson-specific XP
			replay_xp: Fixed replay XP from settings
			max_multiplier_percent: Streak multiplier cap
			hearts_remaining: Hearts remaining after the completion
			xp_per_heart: Bonus XP per remaining heart
			today: Current Asia/Amman date
			yesterday: Previous Asia/Amman date
			interaction_jsons: List of JSON-encoded interaction strings

		Returns:
			Tuple of (status, payload, extra)
			- status: "completed", "missing", "duplicate", or "mismatch"
			- payload: Cached/finalized response JSON for completed/duplicate cases
			- extra: active session_id for mismatch cases
		"""
		script = await self._get_complete_script()

		session_key = self._session_key(user_id)
		completion_key = self._completion_key(user_id, session_id)
		progress_key = _progress_key_fn(user_id, subject_id, version)
		wallet_key = self._wallet_key(user_id)
		dirty_member = f"{user_id}:{subject_id}:v{version}"

		keys = [
			session_key,
			progress_key,
			DIRTY_PROGRESS_KEY,
			INTERACTION_BUFFER_KEY,
			wallet_key,
			DIRTY_WALLETS_KEY,
			completion_key,
		]
		args = [
			str(bit_index),
			dirty_member,
			session_id,
			str(base_xp),
			str(lesson_xp),
			str(replay_xp),
			str(max_multiplier_percent),
			str(hearts_remaining),
			str(xp_per_heart),
			today,
			yesterday,
			user_id,
			str(SESSION_END_RESPONSE_TTL),
			*interaction_jsons,
		]

		result = await script(keys=keys, args=args)

		if result[0] == 0:
			return ("missing", None, None)
		if result[0] == 2:
			return ("duplicate", self._decode_result_value(result[1]), None)
		if result[0] == 3:
			return ("mismatch", None, self._decode_result_value(result[1]))

		logger.info(
			"session_completed_atomic",
			user_id=user_id,
			session_id=session_id,
			subject_id=subject_id,
			interactions_count=len(interaction_jsons),
		)

		return ("completed", self._decode_result_value(result[1]), None)

	async def start_session(
		self,
		user_id: str,
		lesson_id: str,
		subject_id: str,
		device_id: str | None = None,
	) -> str:
		"""Start new game session, force-closing any existing.

		Per CONTEXT.md:
		- Atomic operation via Lua script
		- Any existing session is silently closed (no notification)
		- TTL of 1 hour auto-expires abandoned sessions

		Args:
			user_id: Player's user ID
			lesson_id: Lesson being started
			subject_id: Subject for hierarchy lookup
			device_id: Optional device that started session

		Returns:
			New session_id (UUID)
		"""
		session_id = str(uuid.uuid4())
		timestamp = datetime.now(timezone.utc).isoformat()

		script = await self._get_start_script()
		key = self._session_key(user_id)

		await script(
			keys=[key],
			args=[
				session_id,
				lesson_id,
				subject_id,
				device_id or "",
				timestamp,
				str(GAME_SESSION_TTL),
			],
		)

		logger.info(
			"session_started",
			user_id=user_id,
			session_id=session_id,
			lesson_id=lesson_id,
			subject_id=subject_id,
			device_id=device_id,
		)

		return session_id

	async def get_active_session(self, user_id: str) -> GameSession | None:
		"""Get active session for user.

		Args:
			user_id: Player's user ID

		Returns:
			GameSession if exists, None otherwise
		"""
		key = self._session_key(user_id)
		data = await self.redis.hgetall(key)

		if not data:
			return None

		return GameSession.from_redis_hash(data)

	async def end_session(self, user_id: str) -> GameSession | None:
		"""End active session and return its data.

		Args:
			user_id: Player's user ID

		Returns:
			GameSession that was ended, or None if no session
		"""
		# Get session data before deleting
		session = await self.get_active_session(user_id)

		if session:
			key = self._session_key(user_id)
			await self.redis.delete(key)

			logger.info(
				"session_ended",
				user_id=user_id,
				session_id=session.session_id,
				lesson_id=session.lesson_id,
			)

		return session

	async def has_active_session(self, user_id: str) -> bool:
		"""Check if user has an active session.

		O(1) operation using EXISTS.

		Args:
			user_id: Player's user ID

		Returns:
			True if session exists, False otherwise
		"""
		key = self._session_key(user_id)
		return await self.redis.exists(key) > 0

	@staticmethod
	def _decode_result_value(value: bytes | str | None) -> str | None:
		"""Normalize Redis script return values to strings."""
		if value is None:
			return None
		if isinstance(value, bytes):
			return value.decode("utf-8")
		return value

	async def get_end_response_state(
		self,
		user_id: str,
		session_id: str,
	) -> tuple[str, dict[str, Any] | None]:
		"""Return whether a cached end-session response is missing or ready."""
		raw = await self.redis.get(self._completion_key(user_id, session_id))
		if raw is None:
			return ("missing", None)

		value = self._decode_result_value(raw)
		if value is None:
			return ("missing", None)

		return ("ready", json.loads(value))
