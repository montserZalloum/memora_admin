# Copyright (c) 2026, corex and contributors
"""Game session service with atomic session lifecycle via Lua script."""

import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis
import structlog

from fastapi_app.core.constants import DIRTY_PROGRESS_KEY, GAME_SESSION_TTL, INTERACTION_BUFFER_KEY
from fastapi_app.core.redis_keys import game_session_key as _game_session_key_fn, progress_key as _progress_key_fn
from fastapi_app.models.game_session import GameSession

logger = structlog.get_logger()

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
#
# ARGV[1] = bit_index (int)
# ARGV[2] = dirty_member string (e.g. "user_id:subject_id:v1")
# ARGV[3..N] = JSON interaction strings (one per stage)
#
# Returns: {status, is_replay, session_field1, session_field2, ...}
#   - status=0: no active session (error)
#   - status=1: success
#   - is_replay: 0 if first completion, 1 if replay (from SETBIT return value)
SESSION_COMPLETE_SCRIPT = """
-- Read session
local session = redis.call('HGETALL', KEYS[1])
if #session == 0 then
    return {0}
end

-- Delete session atomically
redis.call('DEL', KEYS[1])

-- Set progress bit, get previous value (0=first, 1=replay)
local prev = redis.call('SETBIT', KEYS[2], tonumber(ARGV[1]), 1)

-- Mark dirty for background sync
redis.call('SADD', KEYS[3], ARGV[2])

-- Batch RPUSH all interactions (single call, not N calls)
if #ARGV > 2 then
    local interactions = {}
    for i = 3, #ARGV do
        interactions[#interactions + 1] = ARGV[i]
    end
    redis.call('RPUSH', KEYS[4], unpack(interactions))
end

-- Return status + is_replay + session fields
return {1, prev, unpack(session)}
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
	- complete_session: Lua script O(1) - atomic DEL + SETBIT + SADD + RPUSH
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
		bit_index: int,
		subject_id: str,
		version: int,
		interaction_jsons: list[str],
	) -> tuple[bool, bool, GameSession | None]:
		"""Atomically complete session: delete session, set progress bit, push interactions.

		Single Lua script execution = 1 Redis round-trip.

		Args:
			user_id: Player's user ID
			bit_index: Lesson's position in progress bitmap
			subject_id: Subject identifier
			version: Bitmap version
			interaction_jsons: List of JSON-encoded interaction strings

		Returns:
			Tuple of (success, is_replay, session_data)
			- success: True if session existed and was completed
			- is_replay: True if lesson was already completed (bit was 1)
			- session_data: GameSession that was ended, or None if no session
		"""
		script = await self._get_complete_script()

		session_key = self._session_key(user_id)
		progress_key = _progress_key_fn(user_id, subject_id, version)
		dirty_member = f"{user_id}:{subject_id}:v{version}"

		keys = [session_key, progress_key, DIRTY_PROGRESS_KEY, INTERACTION_BUFFER_KEY]
		args = [str(bit_index), dirty_member, *interaction_jsons]

		result = await script(keys=keys, args=args)

		# result[0] == 0 means no active session
		if result[0] == 0:
			return (False, False, None)

		# result[0] == 1: success
		# result[1]: is_replay (0=first completion, 1=replay)
		is_replay = bool(result[1])

		# result[2:] is flat HGETALL output: [key1, val1, key2, val2, ...]
		session_fields = result[2:]
		session_dict: dict[str, str] = {}
		for i in range(0, len(session_fields), 2):
			key = session_fields[i].decode() if isinstance(session_fields[i], bytes) else session_fields[i]
			val = (
				session_fields[i + 1].decode()
				if isinstance(session_fields[i + 1], bytes)
				else session_fields[i + 1]
			)
			session_dict[key] = val

		session = GameSession(**session_dict)

		logger.info(
			"session_completed_atomic",
			user_id=user_id,
			session_id=session.session_id,
			lesson_id=session.lesson_id,
			subject_id=subject_id,
			is_replay=is_replay,
			interactions_count=len(interaction_jsons),
		)

		return (True, is_replay, session)

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
