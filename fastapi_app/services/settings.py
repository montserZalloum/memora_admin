"""Settings service for cached Frappe gamification configuration."""

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import gamification_settings_key
from fastapi_app.models.settings import GamificationSettings
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()


class SettingsService:
	"""Cache gamification settings from Frappe for fast access.

	Per RESEARCH.md:
	- Cache with 5 minute TTL
	- Single cache key for all gamification settings
	- Invalidation via manual call (Phase 6 hook)
	"""

	CACHE_TTL = 300  # 5 minutes

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
	):
		self.redis = redis_client
		self.frappe = frappe_client

	async def get_gamification_settings(self) -> GamificationSettings:
		"""
		Get gamification settings from cache or Frappe.

		1. Check Redis cache
		2. If miss, fetch from Frappe API
		3. Cache result with TTL

		Returns:
			GamificationSettings with XP values and streak multiplier cap
		"""
		# Try cache first
		cached = await self.redis.get(gamification_settings_key())
		if cached:
			data = cached.decode() if isinstance(cached, bytes) else cached
			logger.debug("settings_cache_hit")
			return GamificationSettings.model_validate_json(data)

		# Cache miss - fetch from Frappe
		logger.info("settings_cache_miss", action="fetching_from_frappe")

		result = await self.frappe.call(
			"memora_admin.api.settings.get_gamification_settings"
		)

		if not result:
			# Fallback to defaults if Frappe unavailable
			logger.warning("settings_frappe_unavailable", action="using_defaults")
			return GamificationSettings()

		settings = GamificationSettings.model_validate(result)

		# Cache with TTL
		await self.redis.set(
			gamification_settings_key(),
			settings.model_dump_json(),
			ex=self.CACHE_TTL,
		)

		logger.info(
			"settings_cached",
			base_xp=settings.base_lesson_xp,
			replay_xp=settings.replay_xp,
			max_streak=settings.max_streak_multiplier_percent,
		)

		return settings

	async def invalidate(self) -> None:
		"""
		Invalidate settings cache for manual refresh.

		Called when:
		- Admin updates Memora Settings (Phase 6 hook)
		- Manual cache clear
		"""
		await self.redis.delete(gamification_settings_key())
		logger.info("settings_cache_invalidated")
