"""Player access set management via Redis for O(1) access checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog
from cachetools import TTLCache

from fastapi_app.core.redis_keys import ACCESS_KEY_TTL
from fastapi_app.core.redis_keys import access_key as _access_key_fn
from fastapi_app.core.redis_keys import plan_free_subjects_key as _plan_free_subjects_key_fn
from fastapi_app.services.hydration import guarded_hydrate

if TYPE_CHECKING:
	from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()

# Process-local TTL caches — eliminates Redis RTTs for repeat accesses within TTL window.
# Each uvicorn worker gets its own copy. Safe under asyncio (single-threaded per worker).
_grants_cache: TTLCache[str, set[str]] = TTLCache(maxsize=10_000, ttl=10)
_plan_subjects_cache: TTLCache[str, set[str]] = TTLCache(maxsize=500, ttl=60)


class AccessService:
	"""Manages player access grants via Redis sets.

	Key pattern: memora:access:{player_id} -> set of content keys
	Content keys follow pattern: SUB-{subject} or TRK-{track}

	Per CONTEXT.md:
	- Grants are additive (direct OR plan membership)
	- Grants are permanent until explicitly revoked
	- Grant granularity: Subject-level or Track-level

	Hydration: After a Redis flush, access grants are lost. The ensure_hydrated()
	method restores them from MariaDB via the Frappe API, following the same
	pattern as WalletService.ensure_hydrated().
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient | None = None,
	):
		self.redis = redis_client
		self.frappe = frappe_client

	def _access_key(self, player_id: str) -> str:
		"""Generate Redis key for player's access set."""
		return _access_key_fn(player_id)

	async def ensure_hydrated(self, player_id: str) -> None:
		"""Ensure access set exists in Redis, hydrating from MariaDB if missing.

		Uses distributed lock + semaphore to prevent thundering herd after Redis flush.
		Only one request per player hydrates at a time; others wait for the result.

		Args:
		    player_id: Player's user ID
		"""
		key = self._access_key(player_id)

		# Fast path: access set already exists in Redis
		if await self.redis.exists(key):
			return

		# No Frappe client — can't hydrate
		if not self.frappe:
			logger.warning(
				"access_hydration_skipped",
				player_id=player_id,
				reason="no_frappe_client",
			)
			return

		async def _do_hydrate() -> None:
			try:
				result = await self.frappe.call(
					"memora_admin.api.subscriptions.get_player_access_keys",
					{"player_id": player_id},
				)

				if result and isinstance(result, list) and len(result) > 0:
					await self.redis.sadd(key, *result)
					await self.redis.expire(key, ACCESS_KEY_TTL)
					logger.info(
						"access_hydrated_from_mariadb",
						player_id=player_id,
						count=len(result),
					)
				else:
					logger.debug("access_hydration_empty", player_id=player_id)

			except Exception as e:
				logger.error(
					"access_hydration_failed",
					player_id=player_id,
					error=str(e),
				)

		await guarded_hydrate(self.redis, key, _do_hydrate)

	async def check_access(self, player_id: str, content_key: str) -> bool:
		"""
		Check if player has access to content.
		Uses local grants cache for O(1) set membership check.

		Args:
		    player_id: Player's user ID
		    content_key: Access key (e.g., "SUB-MATH", "TRK-MATH-01")

		Returns:
		    True if player has grant for this content
		"""
		grants = await self.get_player_grants(player_id)
		return content_key in grants

	async def grant_access(self, player_id: str, content_keys: list[str]) -> int:
		"""
		Grant access to content.
		Idempotent - re-granting same key is safe (SADD ignores duplicates).

		Returns:
		    Number of NEW grants added (0 if all existed)
		"""
		if not content_keys:
			return 0
		key = self._access_key(player_id)
		result = await self.redis.sadd(key, *content_keys)
		_grants_cache.pop(player_id, None)
		return result

	async def revoke_access(self, player_id: str, content_keys: list[str]) -> int:
		"""
		Revoke access to content.

		Returns:
		    Number of grants removed
		"""
		if not content_keys:
			return 0
		key = self._access_key(player_id)
		result = await self.redis.srem(key, *content_keys)
		_grants_cache.pop(player_id, None)
		return result

	async def get_player_grants(self, player_id: str) -> set[str]:
		"""
		Get all content keys player has access to.
		Uses local TTL cache (10s) to avoid Redis RTTs on repeat calls.
		"""
		cached = _grants_cache.get(player_id)
		if cached is not None:
			return cached

		await self.ensure_hydrated(player_id)

		key = self._access_key(player_id)
		members = await self.redis.smembers(key)
		result = set(members)
		_grants_cache[player_id] = result
		return result

	# =========================================================================
	# Plan-Aware Access Methods (Level 1: Plan membership grants)
	# =========================================================================

	def _plan_free_subjects_key(self, plan_id: str) -> str:
		"""Generate Redis key for plan's free subjects set."""
		return _plan_free_subjects_key_fn(plan_id)

	async def is_subject_free_in_plan(self, plan_id: str, subject_id: str) -> bool:
		"""Check if subject is marked non-premium in player's plan.
		Uses local TTL cache (60s) for O(1) set membership check.
		"""
		if not plan_id:
			return False
		plan_subjects = await self.get_plan_free_subjects(plan_id)
		return subject_id in plan_subjects

	async def get_plan_free_subjects(self, plan_id: str | None) -> set[str]:
		"""Get subjects marked as non-premium in player's plan.
		Uses local TTL cache (60s) — plans change very rarely.
		"""
		if not plan_id:
			return set()
		cached = _plan_subjects_cache.get(plan_id)
		if cached is not None:
			return cached
		key = self._plan_free_subjects_key(plan_id)
		members = await self.redis.smembers(key)
		result = set(members)
		_plan_subjects_cache[plan_id] = result
		return result

	async def check_access_with_plan(self, player_id: str, content_key: str, plan_id: str | None) -> bool:
		"""Check access via explicit grant OR plan membership.
		Both checks use local caches — typically 0 Redis RTTs.
		"""
		grants = await self.get_player_grants(player_id)
		if content_key in grants:
			return True

		if plan_id and content_key.startswith("SUB-"):
			subject_id = content_key.replace("SUB-", "")
			plan_subjects = await self.get_plan_free_subjects(plan_id)
			if subject_id in plan_subjects:
				return True

		return False
