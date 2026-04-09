"""Product catalog caching service with per-player filtering."""

import asyncio
import json

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import access_key as _access_key_fn
from fastapi_app.core.redis_keys import catalog_key as _catalog_key_fn
from fastapi_app.core.redis_keys import pending_key as _pending_key_fn
from fastapi_app.models.catalog import CatalogProduct
from fastapi_app.services.access import AccessService
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger(__name__)

class CatalogService:
	"""Cache product catalog per-plan and apply per-player exclusions.

	Per 21-CONTEXT.md:
	- Per-plan cache with NO TTL (infinite, event-driven invalidation only)
	- Post-cache filtering for purchased/pending products
	- Redis failure is fatal (503, no fallback)
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self._access_service = AccessService(redis_client, frappe_client)

	def _cache_key(self, plan_id: str) -> str:
		"""Generate Redis key for plan catalog cache."""
		return _catalog_key_fn(plan_id)

	async def get_catalog(self, plan_id: str) -> list[CatalogProduct]:
		"""Get plan catalog from Redis or Frappe. No TTL -- infinite cache.

		Args:
			plan_id: Memora Plan document name

		Returns:
			List of CatalogProduct for the plan (empty if none found)
		"""
		key = self._cache_key(plan_id)

		# 1. Redis cache
		cached = await self.redis.get(key)
		if cached is not None:
			data = cached.decode() if isinstance(cached, bytes) else cached
			logger.debug("catalog_cache_hit", plan_id=plan_id)
			return [CatalogProduct.model_validate(p) for p in json.loads(data)]

		logger.debug("catalog_cache_miss", plan_id=plan_id)

		# 2. Cache miss: fetch from Frappe whitelisted API
		result = await self.frappe.call(
			"memora_admin.memora_admin.api.catalog.get_plan_catalog",
			{"plan_id": plan_id},
		)

		if not result:
			logger.info("catalog_empty", plan_id=plan_id)
			await self.redis.set(key, "[]")
			return []

		products = [CatalogProduct.model_validate(p) for p in result]

		# Cache with NO TTL in Redis (infinite -- invalidated by events only)
		await self.redis.set(key, json.dumps([p.model_dump() for p in products]))

		logger.info("catalog_cached", plan_id=plan_id, product_count=len(products))
		return products

	async def get_player_catalog(
		self,
		plan_id: str,
		player_id: str,
	) -> list[CatalogProduct]:
		"""Get catalog filtered for a specific player.

		Excludes:
		- Products where player has access to ALL component subjects (purchased)
		- Products with pending transactions (grant_id in pending set)

		Args:
			plan_id: Memora Plan document name
			player_id: Player profile ID (user.sub from JWT)

		Returns:
			Filtered list of CatalogProduct
		"""

		# Hydrate access set from MariaDB if evicted from Redis (self-healing cache miss).
		# ensure_hydrated() is a fast no-op (~1 Redis RTT) when the key exists.
		await self._access_service.ensure_hydrated(player_id)

		# Parallel: catalog fetch + player access/pending sets are independent (2 RTT → 1)
		async def _get_player_sets():
			pipe = self.redis.pipeline()
			pipe.smembers(_access_key_fn(player_id))
			pipe.smembers(_pending_key_fn(player_id))
			return await pipe.execute()

		products, (access_raw, pending_raw) = await asyncio.gather(
			self.get_catalog(plan_id),
			_get_player_sets(),
		)
		if not products:
			return []

		# Decode bytes to strings
		access_set = {m.decode() if isinstance(m, bytes) else m for m in access_raw}
		pending_set = {m.decode() if isinstance(m, bytes) else m for m in pending_raw}

		result = []
		for product in products:
			# Check pending: hide products with pending transactions
			if product.product_grant_id in pending_set:
				continue

			# Check purchased: hide if player has access to ALL subjects and tracks in grant
			access_keys = {f"SUB-{s.subject_id}" for s in product.subjects} | {
				f"TRK-{t.track_id}" for t in product.tracks
			}
			if access_keys and access_keys.issubset(access_set):
				continue  # All components accessible = already purchased

			result.append(product)

		logger.debug(
			"catalog_filtered",
			plan_id=plan_id,
			player_id=player_id,
			total=len(products),
			visible=len(result),
		)
		return result

	async def invalidate(self, plan_id: str) -> None:
		"""Delete cached catalog for a plan.

		Called by pubsub handler when Product Grant or related data changes.

		Args:
			plan_id: Memora Plan document name
		"""
		key = self._cache_key(plan_id)
		await self.redis.delete(key)
		logger.info("catalog_cache_invalidated", plan_id=plan_id)
