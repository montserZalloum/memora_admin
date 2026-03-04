"""Global rate limiting service using Redis Lua scripts."""

import time

import redis.asyncio as redis
import structlog

logger = structlog.get_logger()


class RateLimitExceeded(Exception):
	"""Raised when a per-player rate limit is exceeded."""

	def __init__(self, retry_after: int):
		self.retry_after = retry_after


# Lua script: atomic INCR + conditional EXPIRE + TTL retrieval
# Returns {count, ttl} in a single round-trip
GLOBAL_RATE_LIMIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("TTL", KEYS[1])
return {count, ttl}
"""


class GlobalRateLimiter:
	"""
	Single-key rate limiter for global and per-player limits.

	Uses atomic Lua script (INCR + conditional EXPIRE) for single
	Redis round-trip. Fails open on Redis errors.
	"""

	def __init__(self, redis_client: redis.Redis):
		self.redis = redis_client
		self._script = None

	async def _get_script(self):
		"""Get or register Lua script (cached)."""
		if self._script is None:
			self._script = self.redis.register_script(GLOBAL_RATE_LIMIT_SCRIPT)
		return self._script

	async def check(
		self,
		key: str,
		limit: int,
		window: int,
	) -> tuple[bool, int, int]:
		"""
		Check rate limit for a given key.

		Args:
			key: Redis key (e.g., "memora:global_rl:ip:1.2.3.4")
			limit: Maximum allowed requests in the window
			window: Window duration in seconds

		Returns:
			Tuple of (allowed, count, ttl):
			- allowed: True if request should proceed
			- count: Current request count in window
			- ttl: Seconds until window resets
		"""
		try:
			script = await self._get_script()
			result = await script(keys=[key], args=[window])
			count = int(result[0])
			ttl = int(result[1])
			allowed = count <= limit
			return allowed, count, ttl
		except Exception:
			logger.warning("rate_limit_redis_unavailable", key=key)
			return True, 0, 0
