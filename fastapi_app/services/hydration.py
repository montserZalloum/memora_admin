"""Hydration guard to prevent thundering herd after Redis flush.

After a Redis restart/FLUSHDB, every user's first request triggers a synchronous
Frappe HTTP call to hydrate their data. With 100k concurrent users, this creates
a storm of HTTP calls that exhausts Frappe's worker pool.

This module provides three defenses:
1. Distributed lock (SET NX EX) — only one hydration per cache key at a time;
   other requests for the same key wait for the result.
2. Global asyncio semaphore — limits total concurrent Frappe calls per worker
   process (e.g., 50), preventing worker pool exhaustion.
3. Empty-result sentinel — after hydration finds no data (new player), a short-
   lived sentinel key prevents repeated Frappe calls and 5s waiter timeouts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis
import structlog

from fastapi_app.core.redis_keys import hydration_lock_key

logger = structlog.get_logger()

# Max concurrent Frappe hydration calls per uvicorn worker process.
# With 4 workers x 50 = 200 max concurrent Frappe calls across the service.
MAX_CONCURRENT_HYDRATIONS = 50

# How long to remember that a key hydrated to empty (seconds).
# During this window, requests skip the Frappe call entirely.
# If the player gains data via event hooks (e.g., grant_access()), the real
# cache key will exist and ensure_hydrated() returns on the EXISTS fast path
# before ever reaching the sentinel check.
SENTINEL_TTL = 60

_hydration_semaphore: asyncio.Semaphore | None = None


def get_hydration_semaphore() -> asyncio.Semaphore:
	"""Get or create the per-process hydration semaphore."""
	global _hydration_semaphore
	if _hydration_semaphore is None:
		_hydration_semaphore = asyncio.Semaphore(MAX_CONCURRENT_HYDRATIONS)
	return _hydration_semaphore


async def guarded_hydrate(
	redis_client: aioredis.Redis,
	cache_key: str,
	hydrate_fn: Callable[[], Awaitable[bool | None]],
	*,
	lock_ttl: int = 30,
	wait_timeout: float = 5.0,
	poll_interval: float = 0.1,
	sentinel_ttl: int = SENTINEL_TTL,
) -> None:
	"""Execute hydration with distributed lock and rate limiting.

	Prevents thundering herd after Redis flush:
	- Only one request per cache_key hydrates at a time (distributed lock).
	- Global semaphore caps concurrent Frappe calls per worker process.
	- Concurrent requests for the same key short-poll until data appears.
	- Empty-result sentinel prevents repeated hydration for new/empty players.

	Args:
		redis_client: Redis connection.
		cache_key: The Redis key being hydrated (polled for existence by waiters).
		hydrate_fn: Async callable that performs the Frappe call and returns
			truthy when it wrote the cache key.
		lock_ttl: Lock auto-expiration in seconds (deadlock protection).
		wait_timeout: Max seconds a waiter polls before giving up.
		poll_interval: Seconds between existence checks while waiting.
		sentinel_ttl: TTL for empty-result sentinel (seconds).
	"""
	sentinel_key = f"{cache_key}:_hydrated"
	lock_key = hydration_lock_key(cache_key)

	# Fast path: we recently confirmed this key is empty (no data in MariaDB).
	# Avoids re-hydrating new players every request for sentinel_ttl seconds.
	if await redis_client.exists(sentinel_key):
		return

	# Try to acquire distributed lock for this specific key
	acquired = await redis_client.set(lock_key, "1", nx=True, ex=lock_ttl)

	if not acquired:
		# Another request is already hydrating this key — wait for the result.
		# Poll for either the cache key (data written) or sentinel (empty result).
		elapsed = 0.0
		while elapsed < wait_timeout:
			await asyncio.sleep(poll_interval)
			elapsed += poll_interval
			# exists() with multiple keys returns count of keys that exist
			if await redis_client.exists(cache_key, sentinel_key):
				return
		# Timeout — proceed without data. Next request will retry.
		logger.warning(
			"hydration_wait_timeout",
			cache_key=cache_key,
			waited_s=round(elapsed, 1),
		)
		return

	# We hold the lock — hydrate under the global semaphore
	wrote_data = False
	try:
		sem = get_hydration_semaphore()
		async with sem:
			wrote_data = bool(await hydrate_fn())
	finally:
		# Only set the sentinel when hydration wrote no data. That preserves the
		# empty-player fast path without masking a freshly written cache entry.
		if not wrote_data:
			await redis_client.set(sentinel_key, "1", ex=sentinel_ttl)
		# Release lock. On crash, the TTL auto-expires it anyway.
		await redis_client.delete(lock_key)
