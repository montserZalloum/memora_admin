"""Dual-key rate limiting for login protection."""

from typing import Tuple

import redis.asyncio as redis

from fastapi_app.core.redis_keys import ratelimit_account_key, ratelimit_ip_key

# Lua script for atomic increment with conditional TTL
# Returns current count after increment
RATE_LIMIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""


class RateLimiter:
	"""
	Dual-key rate limiter for login attempts.

	Per CONTEXT.md:
	- 10 attempts/min per IP
	- 5 attempts/min per account
	- Returns Retry-After seconds when blocked
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		ip_limit: int = 10,
		account_limit: int = 5,
		window_seconds: int = 60,
	):
		self.redis = redis_client
		self.ip_limit = ip_limit
		self.account_limit = account_limit
		self.window_seconds = window_seconds
		self._script = None

	async def _get_script(self):
		"""Get or register Lua script (cached)."""
		if self._script is None:
			self._script = self.redis.register_script(RATE_LIMIT_SCRIPT)
		return self._script

	async def check_rate_limit(
		self,
		ip_address: str,
		target_account: str | None = None,
	) -> Tuple[bool, int, str]:
		"""
		Check dual rate limits.

		Args:
		    ip_address: Client IP address
		    target_account: Email being targeted (optional)

		Returns:
		    Tuple of:
		    - allowed: bool - True if request can proceed
		    - retry_after: int - Seconds until limit resets (0 if allowed)
		    - limit_type: str - "ip" or "account" (empty if allowed)
		"""
		script = await self._get_script()

		# Check IP limit first
		ip_key = ratelimit_ip_key(ip_address)
		ip_count = await script(keys=[ip_key], args=[self.window_seconds])

		if ip_count > self.ip_limit:
			ttl = await self.redis.ttl(ip_key)
			return False, max(ttl, 1), "ip"

		# Check account limit if account provided
		if target_account:
			# Normalize email to lowercase for consistent limiting
			account_key = ratelimit_account_key(target_account.lower())
			account_count = await script(keys=[account_key], args=[self.window_seconds])

			if account_count > self.account_limit:
				ttl = await self.redis.ttl(account_key)
				return False, max(ttl, 1), "account"

		return True, 0, ""

	async def get_remaining(
		self,
		ip_address: str,
		target_account: str | None = None,
	) -> Tuple[int, int]:
		"""
		Get remaining attempts (for response headers).

		Returns:
		    Tuple of (ip_remaining, account_remaining)
		"""
		ip_key = ratelimit_ip_key(ip_address)
		ip_count_raw = await self.redis.get(ip_key)
		ip_count = int(ip_count_raw) if ip_count_raw else 0
		ip_remaining = max(0, self.ip_limit - ip_count)

		account_remaining = self.account_limit
		if target_account:
			account_key = ratelimit_account_key(target_account.lower())
			account_count_raw = await self.redis.get(account_key)
			account_count = int(account_count_raw) if account_count_raw else 0
			account_remaining = max(0, self.account_limit - account_count)

		return ip_remaining, account_remaining
