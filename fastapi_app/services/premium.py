"""PremiumService: 3-tier cached premium usability check (R-004).

Architecture:
  1. Process-local cache (60s TTL) → hit? return
  2. Redis hash memora:premium:{player}:{plan} → hit? return
  3. Frappe API hydration → compute, cache in Redis, return
"""

import time
from dataclasses import dataclass

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import NEGATIVE_CACHE_TTL, premium_key, premium_lock_key
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger(__name__)

LOCAL_CACHE_TTL = 60  # seconds
LOCK_TTL = 10  # seconds
MAX_LOCAL_CACHE_SIZE = 10_000


@dataclass
class PremiumState:
	"""Cached premium usability state."""

	usable: bool
	reason: str  # none | plan_mismatch | season_ended | revoked | no_premium
	premium_id: str | None = None
	season_end: str | None = None
	source_type: str | None = None


class PremiumService:
	"""3-tier cached premium check: process-local → Redis → Frappe API."""

	def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient):
		self.redis = redis_client
		self.frappe = frappe_client
		# Process-local cache: {f"{player}:{plan}": (PremiumState, expiry_ts)}
		self._local_cache: dict[str, tuple[PremiumState, float]] = {}

	async def is_plan_premium_usable(self, player_id: str, plan_id: str) -> PremiumState:
		"""Check if player has usable premium for plan. 3-tier cached."""
		cache_key = f"{player_id}:{plan_id}"

		# Tier 1: Process-local cache
		entry = self._local_cache.get(cache_key)
		if entry:
			state, expiry = entry
			if time.monotonic() < expiry:
				return state
			del self._local_cache[cache_key]

		# Tier 2: Redis hash
		redis_key = premium_key(player_id, plan_id)
		try:
			raw = await self.redis.hgetall(redis_key)
			if raw:
				state = _parse_redis_hash(raw)
				self._local_cache[cache_key] = (state, time.monotonic() + LOCAL_CACHE_TTL)
				return state
		except Exception:
			logger.warning("premium_redis_read_failed", player=player_id, plan=plan_id)

		# Tier 3: Hydrate from Frappe API
		state = await self._hydrate_from_frappe(player_id, plan_id)
		# Cache in Redis — negative results get TTL to prevent permanent false denials
		try:
			mapping = {
				"usable": "1" if state.usable else "0",
				"reason": state.reason,
				"season_end": state.season_end or "",
				"source_type": state.source_type or "",
				"premium_id": state.premium_id or "",
			}
			await self.redis.hset(redis_key, mapping=mapping)
			if not state.usable:
				await self.redis.expire(redis_key, NEGATIVE_CACHE_TTL)
		except Exception:
			logger.warning("premium_redis_write_failed", player=player_id, plan=plan_id)

		if len(self._local_cache) >= MAX_LOCAL_CACHE_SIZE:
			self._local_cache.clear()
		self._local_cache[cache_key] = (state, time.monotonic() + LOCAL_CACHE_TTL)
		return state

	async def has_pending_purchase(self, player_id: str, plan_id: str) -> bool:
		"""Check if player has a pending purchase for this plan."""
		try:
			result = await self.frappe._call_method(
				"frappe.client.get_count",
				doctype="Memora Plan Premium Purchase",
				filters={"player": player_id, "plan": plan_id, "status": "pending"},
			)
			return int(result or 0) > 0
		except Exception:
			logger.warning("pending_purchase_check_failed", player=player_id, plan=plan_id)
			return False

	async def acquire_lock(self, player_id: str, plan_id: str) -> bool:
		"""Acquire Redis lock for premium mutations. Returns True if acquired."""
		lock_key = premium_lock_key(player_id, plan_id)
		try:
			return bool(await self.redis.set(lock_key, "1", nx=True, ex=LOCK_TTL))
		except Exception:
			logger.warning("premium_lock_failed", player=player_id, plan=plan_id)
			return False

	async def release_lock(self, player_id: str, plan_id: str):
		"""Release Redis lock for premium mutations."""
		lock_key = premium_lock_key(player_id, plan_id)
		try:
			await self.redis.delete(lock_key)
		except Exception:
			pass

	async def _hydrate_from_frappe(self, player_id: str, plan_id: str) -> PremiumState:
		"""Call Frappe API to compute premium usability from DB."""
		try:
			result = await self.frappe._call_method(
				"memora_admin.memora_admin.services.premium.access_check.check_premium_api",
				player=player_id,
				plan=plan_id,
			)
			if not result:
				return PremiumState(usable=False, reason="no_premium")
			return PremiumState(
				usable=bool(result.get("usable")),
				reason=result.get("reason", "no_premium"),
				premium_id=result.get("premium_id"),
				season_end=result.get("season_end"),
				source_type=result.get("source_type"),
			)
		except Exception:
			logger.warning("premium_frappe_hydration_failed", player=player_id, plan=plan_id)
			return PremiumState(usable=False, reason="no_premium")


def _parse_redis_hash(raw: dict) -> PremiumState:
	"""Parse Redis hash bytes/strings into PremiumState."""
	def _s(v):
		return v.decode() if isinstance(v, bytes) else str(v) if v else ""

	usable = _s(raw.get(b"usable", raw.get("usable", "0"))) == "1"
	reason = _s(raw.get(b"reason", raw.get("reason", "no_premium")))
	premium_id = _s(raw.get(b"premium_id", raw.get("premium_id", ""))) or None
	season_end = _s(raw.get(b"season_end", raw.get("season_end", ""))) or None
	source_type = _s(raw.get(b"source_type", raw.get("source_type", ""))) or None

	return PremiumState(
		usable=usable,
		reason=reason,
		premium_id=premium_id,
		season_end=season_end,
		source_type=source_type,
	)
