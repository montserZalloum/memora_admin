"""Announcements endpoint — player-facing active announcements."""

from typing import Literal

from fastapi import APIRouter, Query

from fastapi_app.api.deps import AnnouncementServiceDep, CurrentUser
from fastapi_app.models.announcements import AnnouncementsResponse

router = APIRouter(prefix="/announcements", tags=["announcements"])


@router.get("/", response_model=AnnouncementsResponse)
async def get_announcements(
	user: CurrentUser,
	service: AnnouncementServiceDep,
	lang: Literal["ar", "en"] = Query(default="ar", description="Content language"),
) -> AnnouncementsResponse:
	"""Get active announcements for the authenticated player.

	Returns announcements filtered by date range and plan targeting,
	with content in the requested language. Sorted newest first.
	"""
	items = await service.get_for_player(player_plan=user.plan, lang=lang)
	return AnnouncementsResponse(announcements=items)
