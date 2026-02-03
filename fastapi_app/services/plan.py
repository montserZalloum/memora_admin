"""Plan manifest caching service for fast mobile app serving."""

from typing import Optional

import redis.asyncio as redis
import structlog

from fastapi_app.models.plan import PlanManifest
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger(__name__)


class PlanService:
	"""Cache plan manifests for fast mobile app serving.

	Per v1.2-ROADMAP.md:
	- Cache with 1 hour TTL
	- Invalidation via Redis pub/sub
	- Read from CDN files or fallback to Frappe API
	"""

	CACHE_TTL = 3600  # 1 hour

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
		key_prefix: str = "memora:",
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self.prefix = key_prefix

	def _cache_key(self, plan_id: str) -> str:
		"""Generate Redis key for plan manifest cache."""
		return f"{self.prefix}plan:{plan_id}:manifest"

	async def get_manifest(self, plan_id: str) -> Optional[PlanManifest]:
		"""
		Get plan manifest from cache or Frappe.

		1. Check Redis cache
		2. If miss, fetch from Frappe API (which reads from CDN files)
		3. Cache result with TTL

		Returns:
			PlanManifest or None if plan not found
		"""
		key = self._cache_key(plan_id)

		# Try cache first
		cached = await self.redis.get(key)
		if cached:
			data = cached.decode() if isinstance(cached, bytes) else cached
			logger.debug("plan_manifest_cache_hit", plan_id=plan_id)
			return PlanManifest.model_validate_json(data)

		logger.debug("plan_manifest_cache_miss", plan_id=plan_id)

		# Cache miss - fetch from Frappe API
		result = await self.frappe.call(
			"memora_admin.api.plan.get_plan_manifest",
			{"plan_id": plan_id},
		)

		if not result:
			logger.warning("plan_manifest_not_found", plan_id=plan_id)
			return None

		# Parse into model
		try:
			manifest = PlanManifest.model_validate(result)
		except Exception as e:
			logger.error("plan_manifest_parse_error", plan_id=plan_id, error=str(e))
			return None

		# Cache with TTL
		await self.redis.set(
			key,
			manifest.model_dump_json(),
			ex=self.CACHE_TTL,
		)

		logger.info("plan_manifest_cached", plan_id=plan_id)
		return manifest

	async def invalidate(self, plan_id: str) -> None:
		"""
		Invalidate plan manifest cache.

		Called when:
		- Plan JSON is regenerated (Phase 6 build worker)
		- Manual cache clear
		"""
		key = self._cache_key(plan_id)
		await self.redis.delete(key)
		logger.info("plan_manifest_invalidated", plan_id=plan_id)

	async def invalidate_all(self) -> None:
		"""
		Invalidate all plan manifest caches.

		Uses SCAN to find keys matching pattern.
		Use sparingly - for major updates.
		"""
		pattern = f"{self.prefix}plan:*:manifest"
		cursor = 0
		deleted = 0
		while True:
			cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
			if keys:
				await self.redis.delete(*keys)
				deleted += len(keys)
			if cursor == 0:
				break
		logger.info("plan_manifests_invalidated_all", count=deleted)
