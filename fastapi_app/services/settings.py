"""Settings service for cached Frappe gamification configuration."""

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import gamification_settings_key
from fastapi_app.models.settings import GamificationSettings
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.hydration import guarded_hydrate

logger = structlog.get_logger()


class SettingsService:
	"""Cache gamification settings from Frappe for fast access.

	- Cached indefinitely (no TTL) — invalidated only on Memora Settings save
	- Frappe hook writes eagerly on save; cache miss = cold start fallback
	- Hydration guard prevents thundering herd on cache miss
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
	):
		self.redis = redis_client
		self.frappe = frappe_client

	async def get_gamification_settings(self) -> GamificationSettings:
		"""
		Get gamification settings from cache, or hydrate from Frappe on miss.

		Returns:
			GamificationSettings with XP values and streak multiplier cap.
			Falls back to defaults if Frappe is unreachable.
		"""
		# Try cache first
		cached = await self.redis.get(gamification_settings_key())
		if cached:
			data = cached.decode() if isinstance(cached, bytes) else cached
			try:
				logger.debug("settings_cache_hit")
				return GamificationSettings.model_validate_json(data)
			except Exception:
				logger.warning("settings_cache_corrupt", action="deleting_and_rehydrating")
				await self.redis.delete(gamification_settings_key())

		# Cache miss — hydrate with guard (prevents thundering herd)
		logger.info("settings_cache_miss", action="hydrating_from_frappe")

		try:
			await guarded_hydrate(
				self.redis,
				gamification_settings_key(),
				self._hydrate_from_frappe,
				lock_ttl=10,
				wait_timeout=3.0,
			)
		except Exception:
			logger.exception("settings_hydration_failed", action="using_defaults")
			return GamificationSettings()

		# Re-read after hydration
		cached = await self.redis.get(gamification_settings_key())
		if cached:
			data = cached.decode() if isinstance(cached, bytes) else cached
			return GamificationSettings.model_validate_json(data)

		# Hydration wrote nothing (Frappe returned empty) — use defaults
		logger.warning("settings_frappe_empty", action="using_defaults")
		return GamificationSettings()

	async def _hydrate_from_frappe(self) -> bool:
		"""Fetch settings from Frappe and write to Redis (no TTL)."""
		try:
			result = await self.frappe.call("memora_admin.api.settings.get_gamification_settings")
		except Exception:
			logger.exception("settings_frappe_call_failed")
			return False

		if not result:
			return False

		settings = GamificationSettings.model_validate(result)

		# Cache indefinitely — invalidated by Frappe hook on save
		await self.redis.set(
			gamification_settings_key(),
			settings.model_dump_json(),
		)

		logger.info(
			"settings_cached",
			base_xp=settings.base_lesson_xp,
			replay_xp=settings.replay_xp,
			max_streak=settings.max_streak_multiplier_percent,
		)
		return True

	async def invalidate(self) -> None:
		"""
		Invalidate settings cache.

		Called via pubsub when admin saves Memora Settings.
		"""
		await self.redis.delete(gamification_settings_key())
		logger.info("settings_cache_invalidated")
