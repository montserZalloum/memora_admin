"""Announcement response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AnnouncementItem(BaseModel):
	"""A single active announcement in the player's preferred language."""

	id: str = Field(..., description="Announcement document ID (e.g., ANN-00001)")
	title: str = Field(..., description="Announcement title in the requested language")
	body: str = Field(..., description="Announcement body text in the requested language")
	display_frequency: Literal["always", "once", "once_per_day", "once_per_session"] = Field(
		..., description="How often the client should display this announcement"
	)
	created_at: datetime = Field(..., description="When the announcement was created")


class AnnouncementsResponse(BaseModel):
	"""Announcements endpoint response."""

	announcements: list[AnnouncementItem] = Field(default_factory=list)
