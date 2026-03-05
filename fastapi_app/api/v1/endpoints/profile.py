"""Profile page endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status

from fastapi_app.api.deps import (
	CurrentUser,
	ProfilePageServiceDep,
	evict_session_cache,
)
from fastapi_app.models.profile import (
	AvatarUpdateRequest,
	AvatarUpdateResponse,
	HeroResponse,
	LogoutResponse,
	MemoryMasteryResponse,
	StatsResponse,
	WeeklyActivityResponse,
)
from fastapi_app.services.frappe_client import FrappeAPIError

logger = structlog.get_logger()

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=HeroResponse)
async def get_hero(
	user: CurrentUser,
	profile_page_service: ProfilePageServiceDep,
):
	"""Get profile hero section: avatar, display name, level, XP progress."""
	result = await profile_page_service.get_hero(user.sub)
	return HeroResponse(**result)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
	user: CurrentUser,
	profile_page_service: ProfilePageServiceDep,
	subject: str | None = Query(None, description="Subject ID filter"),
):
	"""Get stats grid: streak, items learned, total XP.

	Optionally filtered by subject. Streak is always global.
	"""
	result = await profile_page_service.get_stats(user.sub, subject)
	return StatsResponse(**result)


@router.get("/mastery", response_model=MemoryMasteryResponse)
async def get_mastery(
	user: CurrentUser,
	profile_page_service: ProfilePageServiceDep,
	subject: str | None = Query(None, description="Subject ID filter"),
):
	"""Get memory mastery breakdown: mature and learning counts.

	Cached for 5 minutes. Optionally filtered by subject.
	"""
	result = await profile_page_service.get_mastery(user.sub, subject)
	return MemoryMasteryResponse(**result)


@router.get("/activity", response_model=WeeklyActivityResponse)
async def get_weekly_activity(
	user: CurrentUser,
	profile_page_service: ProfilePageServiceDep,
):
	"""Get weekly activity: XP per day for the last 7 days ending today."""
	result = await profile_page_service.get_weekly_activity(user.sub)
	return WeeklyActivityResponse(**result)


@router.put("/avatar", response_model=AvatarUpdateResponse)
async def update_avatar(
	user: CurrentUser,
	profile_page_service: ProfilePageServiceDep,
	body: AvatarUpdateRequest,
):
	"""Update player avatar selection.

	Validates avatar against DocType field options. Invalidates profile cache.
	"""
	try:
		result = await profile_page_service.update_avatar(user.sub, body.avatar)
	except FrappeAPIError as e:
		logger.warning("avatar_update_failed", player=user.sub, avatar=body.avatar, error=str(e))
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Invalid avatar option",
		)
	return AvatarUpdateResponse(**result)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
	user: CurrentUser,
	profile_page_service: ProfilePageServiceDep,
	request: Request,
):
	"""Logout: invalidate session and optionally remove device.

	Reads X-Device-ID header to remove the device (freeing a device slot).
	"""
	device_id = request.headers.get("X-Device-ID")
	result = await profile_page_service.logout(user.sub, device_id)
	evict_session_cache(user.sub)
	return LogoutResponse(**result)
