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

	- Cached in Redis with no TTL (persistent)
	- Invalidated by Frappe hook when admin saves Memora Settings
	- Cold start hydrates from Frappe with thundering herd protection

	Returns:
		GamificationSettings with all gamification configuration
	"""
	settings = await settings_service.get_gamification_settings()
	logger.debug("gamification_settings_returned")
	return settings
