# Copyright (c) 2026, corex and contributors
"""Game session service with atomic session lifecycle via Lua script."""

import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis
import structlog

from fastapi_app.core.constants import GAME_SESSION_TTL
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
	"""

	def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
		"""Initialize GameSessionService.

		Args:
			redis_client: Async Redis client
			key_prefix: Prefix for Redis keys (default: "memora:")
		"""
		self.redis = redis_client
		self.prefix = key_prefix
		self._start_script: Any | None = None

	def _session_key(self, user_id: str) -> str:
		"""Get Redis key for user's game session.

		Args:
			user_id: Player's user ID

		Returns:
			Redis key string
		"""
		return f"{self.prefix}gamesession:{user_id}"

	async def _get_start_script(self) -> Any:
		"""Get or create the start session Lua script (lazy-loaded and cached).

		Returns:
			Redis Script object
		"""
		if self._start_script is None:
			self._start_script = self.redis.register_script(START_SESSION_SCRIPT)
		return self._start_script

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
