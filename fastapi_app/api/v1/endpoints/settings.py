# Copyright (c) 2026, corex and contributors
"""Gamification settings endpoint for game client configuration."""

import structlog
from fastapi import APIRouter

from fastapi_app.api.deps import SettingsServiceDep
from fastapi_app.models.settings import GamificationSettings

logger = structlog.get_logger()

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/gamification", response_model=GamificationSettings)
async def get_gamification_settings(
	settings_service: SettingsServiceDep,
) -> GamificationSettings:
	"""
	Get current gamification settings for game client.

	Per Phase 20:
	- Cached in Redis with 5 minute TTL
	- Includes base_xp, replay_xp, max_hearts, xp_per_heart, and streak multiplier cap
	- Manual invalidation via Frappe hook when Memora Settings updated

	Returns:
		GamificationSettings with all gamification configuration
	"""
	settings = await settings_service.get_gamification_settings()
	logger.debug("gamification_settings_returned")
	return settings
