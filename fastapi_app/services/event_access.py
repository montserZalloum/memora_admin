"""EventAccessService: Redis-cached event access check (R-004 pattern).

Architecture mirrors PremiumService:
  1. Process-local cache (60s TTL) → hit? return
  2. Redis hash memora:event_access:{player}:{event} → hit? return
  3. Frappe API hydration → compute, cache in Redis, return
"""

import time
from dataclasses import dataclass

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import NEGATIVE_CACHE_TTL, event_access_key, event_access_lock_key
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger(__name__)

LOCAL_CACHE_TTL = 60  # seconds
LOCK_TTL = 10  # seconds
MAX_LOCAL_CACHE_SIZE = 10_000


@dataclass
class EventAccessState:
	"""Cached event access state."""

	has_access: bool
	access_type: str | None = None  # purchase | voucher | admin | None
	access_id: str | None = None


class EventAccessService:
	"""Redis-cached event access check: process-local → Redis → Frappe API."""

	def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient):
		self.redis = redis_client
		self.frappe = frappe_client
		# Process-local cache: {f"{player}:{event}": (EventAccessState, expiry_ts)}
		self._local_cache: dict[str, tuple[EventAccessState, float]] = {}

	async def has_active_access(self, player_id: str, event_id: str) -> EventAccessState:
		"""Check if player has active access to event. 3-tier cached."""
		cache_key = f"{player_id}:{event_id}"

		# Tier 1: Process-local cache
		entry = self._local_cache.get(cache_key)
		if entry:
			state, expiry = entry
			if time.monotonic() < expiry:
				return state
			del self._local_cache[cache_key]

		# Tier 2: Redis hash
		redis_key = event_access_key(player_id, event_id)
		try:
			raw = await self.redis.hgetall(redis_key)
			if raw:
				state = _parse_redis_hash(raw)
				self._local_cache[cache_key] = (state, time.monotonic() + LOCAL_CACHE_TTL)
				return state
		except Exception:
			logger.warning("event_access_redis_read_failed", player=player_id, event=event_id)

		# Tier 3: Hydrate from Frappe API
		state = await self._hydrate_from_frappe(player_id, event_id)
		# Cache in Redis — negative results get TTL to prevent permanent false denials
		try:
			mapping = {
				"has_access": "1" if state.has_access else "0",
				"access_type": state.access_type or "",
				"access_id": state.access_id or "",
			}
			await self.redis.hset(redis_key, mapping=mapping)
			if not state.has_access:
				await self.redis.expire(redis_key, NEGATIVE_CACHE_TTL)
		except Exception:
			logger.warning("event_access_redis_write_failed", player=player_id, event=event_id)

		if len(self._local_cache) >= MAX_LOCAL_CACHE_SIZE:
			self._local_cache.clear()
		self._local_cache[cache_key] = (state, time.monotonic() + LOCAL_CACHE_TTL)
		return state

	async def has_pending_purchase(self, player_id: str, event_id: str) -> bool:
		"""Check if player has a pending event ticket purchase."""
		try:
			result = await self.frappe._call_method(
				"frappe.client.get_count",
				doctype="Memora Live Event Purchase",
				filters={"player": player_id, "event": event_id, "status": "pending"},
			)
			return int(result or 0) > 0
		except Exception:
			logger.warning("event_pending_purchase_check_failed", player=player_id, event=event_id)
			return False

	async def acquire_lock(self, player_id: str, event_id: str) -> bool:
		"""Acquire Redis lock for event access mutations. Returns True if acquired."""
		lock_key = event_access_lock_key(player_id, event_id)
		try:
			return bool(await self.redis.set(lock_key, "1", nx=True, ex=LOCK_TTL))
		except Exception:
			logger.warning("event_access_lock_failed", player=player_id, event=event_id)
			return False

	async def release_lock(self, player_id: str, event_id: str):
		"""Release Redis lock for event access mutations."""
		lock_key = event_access_lock_key(player_id, event_id)
		try:
			await self.redis.delete(lock_key)
		except Exception:
			pass

	async def _hydrate_from_frappe(self, player_id: str, event_id: str) -> EventAccessState:
		"""Call Frappe API to check event access from DB."""
		try:
			result = await self.frappe._call_method(
				"frappe.client.get_list",
				doctype="Memora Live Event Access",
				filters={"player": player_id, "event": event_id, "status": "active"},
				fields=["name", "access_type"],
				limit_page_length=1,
			)
			if result and len(result) > 0:
				row = result[0]
				return EventAccessState(
					has_access=True,
					access_type=row.get("access_type"),
					access_id=row.get("name"),
				)
			return EventAccessState(has_access=False)
		except Exception:
			logger.warning("event_access_frappe_hydration_failed", player=player_id, event=event_id)
			return EventAccessState(has_access=False)


def _parse_redis_hash(raw: dict) -> EventAccessState:
	"""Parse Redis hash bytes/strings into EventAccessState."""
	def _s(v):
		return v.decode() if isinstance(v, bytes) else str(v) if v else ""

	has_access = _s(raw.get(b"has_access", raw.get("has_access", "0"))) == "1"
	access_type = _s(raw.get(b"access_type", raw.get("access_type", ""))) or None
	access_id = _s(raw.get(b"access_id", raw.get("access_id", ""))) or None

	return EventAccessState(
		has_access=has_access,
		access_type=access_type,
		access_id=access_id,
	)
