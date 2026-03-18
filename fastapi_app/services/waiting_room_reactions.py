"""ReactionEngine — Redis-global reaction aggregation and burst broadcasting.

Aggregates reaction taps (heart/fire/clap) per waiting room into time-based
windows using Redis counters, then broadcasts anonymous burst messages to all
room participants via Redis pub/sub. All counters and room-level caps live in
Redis so behavior is correct under multi-worker (multi-uvicorn) deployment.

Rate limiting uses a Redis Lua token bucket script (per-user, per-event) to
enforce sustained rate and burst allowance without disconnecting clients.

Cross-worker fan-out uses Redis pub/sub: the flush loop PUBLISHes burst JSON
to ``memora:lc_burst:{event_id}``, and every worker's subscriber loop receives
the message and calls ``_broadcast`` to its own local WebSocket connections.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import structlog

from fastapi_app.core.redis_keys import (
	REACTION_ROOM_SEC_TTL,
	REACTION_WIN_TTL,
	lc_reaction_burst_channel,
	lc_reaction_flush_lock_key,
	lc_reaction_rl_key,
	lc_reaction_room_sec_key,
	lc_reaction_win_key,
)

logger = structlog.get_logger()

VALID_REACTIONS = frozenset({"heart", "fire", "clap"})

# ---------------------------------------------------------------------------
# Token bucket rate limiting — Redis Lua script (T009)
# ---------------------------------------------------------------------------
# KEYS[1] = lc_reaction_rl:{event_id}:{player_id}
# ARGV[1] = max_tokens (burst allowance, e.g. 6)
# ARGV[2] = refill_rate_per_sec (sustained rate, e.g. 3)
# ARGV[3] = now_ms (server timestamp in milliseconds)
# ARGV[4] = ttl_sec (key expiry, e.g. 5)
# Returns: 1 if allowed, 0 if rejected
_RATE_LIMIT_LUA = """\
local key = KEYS[1]
local max_tokens = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_ms')
local tokens = tonumber(data[1])
local last_ms = tonumber(data[2])

if tokens == nil then
    tokens = max_tokens - 1
    redis.call('HMSET', key, 'tokens', tokens, 'last_ms', now_ms)
    redis.call('EXPIRE', key, ttl)
    return 1
end

local elapsed_ms = now_ms - last_ms
local refill = elapsed_ms * refill_rate / 1000
tokens = math.min(max_tokens, tokens + refill)

if tokens < 1 then
    redis.call('HSET', key, 'last_ms', now_ms)
    redis.call('EXPIRE', key, ttl)
    return 0
end

tokens = tokens - 1
redis.call('HMSET', key, 'tokens', tokens, 'last_ms', now_ms)
redis.call('EXPIRE', key, ttl)
return 1
"""

# ---------------------------------------------------------------------------
# Atomic tap acceptance — Redis Lua script (T050)
# ---------------------------------------------------------------------------
# KEYS[1] = room_sec key (lc:reaction_room_sec:{event_id}:{second})
# KEYS[2] = window counter hash (lc:reaction_win:{event_id}:{window_id})
# ARGV[1] = reaction type (heart/fire/clap)
# ARGV[2] = room_cap (max taps per second per room)
# ARGV[3] = room_sec_ttl (seconds)
# ARGV[4] = win_ttl (seconds)
# Returns: 1 if accepted, -1 if room cap exceeded (degraded)
_ACCEPT_TAP_LUA = """\
local room_sec_key = KEYS[1]
local win_key = KEYS[2]
local reaction = ARGV[1]
local room_cap = tonumber(ARGV[2])
local room_sec_ttl = tonumber(ARGV[3])
local win_ttl = tonumber(ARGV[4])

local count = redis.call('INCR', room_sec_key)
if count == 1 then
    redis.call('EXPIRE', room_sec_key, room_sec_ttl)
end
if count > room_cap then
    redis.call('HSET', win_key, '_degraded', '1')
    redis.call('EXPIRE', win_key, win_ttl)
    return -1
end

local already_exists = redis.call('EXISTS', win_key)
redis.call('HINCRBY', win_key, reaction, 1)
if already_exists == 0 then
    redis.call('EXPIRE', win_key, win_ttl)
end
return 1
"""


class ReactionEngine:
	"""Aggregates reaction taps in Redis and broadcasts windowed bursts.

	A single global flush loop runs per worker, attempting a flush for
	every active event each tick. A Redis SET NX lock ensures only one
	worker flushes each window globally.
	"""

	def __init__(
		self,
		settings: Any,
		broadcast: Callable[[str, dict], Awaitable[int]],
		redis: Any = None,
	) -> None:
		self._settings = settings
		self._broadcast = broadcast
		self._redis = redis
		self._stopped_rooms: set[str] = set()
		self._active_events: set[str] = set()
		self._rl_script: Any = None  # Lazily registered Lua script
		self._tap_script: Any = None  # Lazily registered Lua script
		self._subscriber_task: asyncio.Task | None = None
		self._flush_task: asyncio.Task | None = None
		self._subscribed_channels: set[str] = set()
		self._pubsub: Any = None  # redis.asyncio pubsub object
		self._worker_id: str = f"w-{os.getpid()}-{id(self)}"

	async def check_rate_limit(self, event_id: str, player_id: str) -> bool:
		"""Check per-user rate limit via Redis Lua token bucket.

		Returns True if allowed, False if rejected.
		Returns True (fail-open) if Redis is unavailable or not configured.
		"""
		if self._redis is None:
			return True

		try:
			if self._rl_script is None:
				self._rl_script = self._redis.register_script(_RATE_LIMIT_LUA)

			key = lc_reaction_rl_key(event_id, player_id)
			now_ms = int(time.time() * 1000)

			result = await self._rl_script(
				keys=[key],
				args=[
					self._settings.reaction_burst_allowance,
					self._settings.reaction_sustained_rate,
					now_ms,
					self._settings.reaction_rl_ttl_sec,
				],
			)
			allowed = int(result) == 1
			if not allowed:
				logger.debug("reaction_rate_limit_hit", event_id=event_id, player_id=player_id)
			return allowed
		except Exception:
			logger.warning("reaction_rate_limit_error", event_id=event_id, player_id=player_id)
			return True  # fail-open

	async def accept_tap(self, event_id: str, player_id: str, reaction: str) -> bool:
		"""Accept a reaction tap. Returns True if accepted, False if dropped."""
		if not self._settings.reaction_enabled:
			return False
		if reaction not in VALID_REACTIONS:
			return False
		if event_id in self._stopped_rooms:
			return False

		try:
			# Per-user rate limit check (T010)
			if not await self.check_rate_limit(event_id, player_id):
				return False

			# Redis counter increment via Lua
			if self._redis is None:
				return False

			if self._tap_script is None:
				self._tap_script = self._redis.register_script(_ACCEPT_TAP_LUA)

			now = time.time()
			window_id = int(now * 1000) // self._settings.reaction_flush_interval_ms
			current_second = int(now)

			room_sec_key = lc_reaction_room_sec_key(event_id, current_second)
			win_key = lc_reaction_win_key(event_id, window_id)

			result = await self._tap_script(
				keys=[room_sec_key, win_key],
				args=[
					reaction,
					self._settings.reaction_room_cap_per_sec,
					REACTION_ROOM_SEC_TTL,
					REACTION_WIN_TTL,
				],
			)

			# Register event for flush loop tracking
			self._active_events.add(event_id)

			accepted = int(result) == 1
			if accepted:
				logger.debug("reaction_tap_accepted", event_id=event_id, reaction=reaction)
			else:
				logger.info("reaction_room_cap_hit", event_id=event_id, cap=self._settings.reaction_room_cap_per_sec)
			return accepted
		except Exception:
			logger.warning("reaction_accept_tap_error", event_id=event_id, player_id=player_id)
			return False

	# -----------------------------------------------------------------------
	# Global flush loop — single loop per worker
	# -----------------------------------------------------------------------

	async def _flush_loop(self) -> None:
		"""Single global loop — attempts flush for every active event each tick."""
		interval = self._settings.reaction_flush_interval_ms / 1000.0
		try:
			while True:
				await asyncio.sleep(interval)
				now_ms = int(time.time() * 1000)
				prev_window = (now_ms // self._settings.reaction_flush_interval_ms) - 1
				for event_id in list(self._active_events):
					if event_id in self._stopped_rooms:
						self._active_events.discard(event_id)
						continue
					try:
						await self._try_flush_window(event_id, prev_window)
					except Exception:
						logger.warning("reaction_flush_error", event_id=event_id)
						continue
		except asyncio.CancelledError:
			return

	async def _try_flush_window(self, event_id: str, window_id: int) -> None:
		"""Attempt to acquire lock and flush a window's counters."""
		if self._redis is None:
			return

		lock_key = lc_reaction_flush_lock_key(event_id, window_id)
		win_key = lc_reaction_win_key(event_id, window_id)

		# Try to acquire lock (only one worker flushes per window)
		acquired = await self._redis.set(
			lock_key,
			self._worker_id,
			nx=True,
			px=self._settings.reaction_flush_interval_ms,
		)
		if not acquired:
			return  # Another worker handles this window

		# Read and delete counter hash (not atomic, but safe: SET NX lock
		# ensures single-writer, and we flush prev-window so no new writes).
		counters_raw = await self._redis.hgetall(win_key)
		await self._redis.delete(win_key)

		if not counters_raw:
			return  # Empty window suppression

		# Extract degraded flag
		degraded = counters_raw.pop("_degraded", None) is not None

		# Build burst message
		reactions = {}
		for rtype, count_str in counters_raw.items():
			count = int(count_str)
			if count > 0:
				reactions[rtype] = {
					"count": count,
					"intensity": _compute_intensity(count),
				}

		if not reactions:
			return  # Only had _degraded flag, no actual taps

		now = datetime.now(timezone.utc)
		msg = {
			"type": "waiting_room_reaction_burst",
			"room_id": event_id,
			"reactions": reactions,
			"degraded": degraded,
			"window_duration_ms": self._settings.reaction_flush_interval_ms,
			"server_ts": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
		}

		logger.info("reaction_burst_emit", event_id=event_id, reactions=reactions, degraded=degraded)

		# Publish to Redis pub/sub for cross-worker fan-out
		published = await self._publish_burst(event_id, msg)
		if not published:
			sent = await self._broadcast(event_id, msg)
			logger.info("reaction_burst_sent_local", event_id=event_id, clients_reached=sent)

	async def _publish_burst(self, event_id: str, msg: dict) -> bool:
		"""Publish burst message to Redis pub/sub channel.

		Returns True if published successfully, False if Redis unavailable
		(caller should fall back to local broadcast).
		"""
		if self._redis is None:
			return False
		try:
			channel = lc_reaction_burst_channel(event_id)
			payload = json.dumps(msg)
			await self._redis.publish(channel, payload)
			return True
		except Exception:
			logger.warning("reaction_burst_publish_error", event_id=event_id)
			return False

	# -----------------------------------------------------------------------
	# Redis pub/sub subscriber — cross-worker burst fan-out
	# -----------------------------------------------------------------------

	async def start_subscriber(self) -> None:
		"""Start subscriber loop and global flush loop.

		Creates pubsub object eagerly but with no initial subscriptions.
		Events are subscribed/unsubscribed dynamically via subscribe_event()
		and unsubscribe_event(). Safe to call multiple times (idempotent).
		"""
		if self._subscriber_task is not None:
			return
		if self._redis is None:
			return
		self._subscriber_task = asyncio.create_task(self._subscriber_loop())
		self._flush_task = asyncio.create_task(self._flush_loop())

	async def _subscriber_loop(self) -> None:
		"""Poll for burst messages on Redis pub/sub and broadcast locally.

		Uses get_message() polling instead of listen() to support dynamic
		subscriptions — listen() exits immediately when no subscriptions
		exist, but get_message() gracefully handles the zero-subscription
		case by sleeping for the timeout duration.
		"""
		import redis.asyncio as aioredis

		pool = self._redis.connection_pool
		client = aioredis.Redis(connection_pool=pool)
		try:
			self._pubsub = client.pubsub()
			logger.info("reaction_subscriber_started")

			while True:
				try:
					message = await self._pubsub.get_message(
						ignore_subscribe_messages=True, timeout=0.1
					)
				except asyncio.CancelledError:
					raise
				except Exception:
					await asyncio.sleep(0.1)
					continue

				if message is None:
					await asyncio.sleep(0.01)
					continue

				try:
					data = message["data"]
					if isinstance(data, bytes):
						data = data.decode("utf-8")
					msg = json.loads(data)
					event_id = msg.get("room_id", "")
					if not event_id:
						continue
					sent = await self._broadcast(event_id, msg)
					logger.info("reaction_burst_relayed", event_id=event_id, clients_reached=sent)
				except Exception:
					logger.warning("reaction_subscriber_message_error", exc_info=True)
					continue
		except asyncio.CancelledError:
			logger.info("reaction_subscriber_cancelled")
			raise
		except Exception:
			logger.error("reaction_subscriber_error", exc_info=True)
			raise
		finally:
			try:
				if self._pubsub is not None:
					for ch in list(self._subscribed_channels):
						await self._pubsub.unsubscribe(ch)
					self._subscribed_channels.clear()
				await client.aclose()
			except Exception:
				logger.debug("reaction_subscriber_cleanup_error", exc_info=True)

	async def stop_subscriber(self) -> None:
		"""Cancel the subscriber loop, flush loop, and clean up."""
		if self._flush_task is not None:
			self._flush_task.cancel()
			try:
				await self._flush_task
			except asyncio.CancelledError:
				pass
			self._flush_task = None

		if self._subscriber_task is not None:
			self._subscriber_task.cancel()
			try:
				await self._subscriber_task
			except asyncio.CancelledError:
				pass
			self._subscriber_task = None

	# -----------------------------------------------------------------------
	# Dynamic subscription — per-event channels
	# -----------------------------------------------------------------------

	async def subscribe_event(self, event_id: str) -> None:
		"""Subscribe to burst channel for an event."""
		if self._pubsub is None:
			return
		channel = lc_reaction_burst_channel(event_id)
		if channel not in self._subscribed_channels:
			try:
				await self._pubsub.subscribe(channel)
				self._subscribed_channels.add(channel)
			except Exception:
				logger.warning("reaction_subscribe_error", event_id=event_id)

	async def unsubscribe_event(self, event_id: str) -> None:
		"""Unsubscribe from burst channel for an event."""
		if self._pubsub is None:
			return
		channel = lc_reaction_burst_channel(event_id)
		if channel in self._subscribed_channels:
			try:
				await self._pubsub.unsubscribe(channel)
				self._subscribed_channels.discard(channel)
			except Exception:
				logger.warning("reaction_unsubscribe_error", event_id=event_id)

	# -----------------------------------------------------------------------
	# Room lifecycle
	# -----------------------------------------------------------------------

	def stop_room(self, event_id: str) -> None:
		"""Stop reaction processing for a room. Redis keys auto-expire via TTL."""
		self._stopped_rooms.add(event_id)
		self._active_events.discard(event_id)
		logger.info("reaction_room_stopped", event_id=event_id)


def _compute_intensity(count: int) -> str:
	"""Compute intensity tier from tap count per reaction per window."""
	if count <= 10:
		return "low"
	if count <= 50:
		return "medium"
	return "high"
