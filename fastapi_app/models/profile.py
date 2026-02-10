"""Profile models for player display names and avatars.

Per CONTEXT.md (Phase 14):
- Enriches leaderboard responses with human-readable display names
- Cache-first approach with 1-hour TTL
- Fallback to "Anonymous XXXX" for missing profiles
"""

from pydantic import BaseModel


class PlayerProfile(BaseModel):
	"""Player profile data for leaderboard enrichment.

	Per CONTEXT.md:
	- player_id: User identifier
	- display_name: Human-readable name (or fallback "Anonymous XXXX")
	- avatar: Avatar file identifier (client constructs full URL)
	"""

	player_id: str
	display_name: str
	avatar: str


class HeroResponse(BaseModel):
	"""Profile hero section."""

	display_name: str
	avatar: str
	level: int
	level_title: str
	current_xp: int  # Total XP
	xp_in_level: int  # XP earned within current level
	xp_for_next_level: int  # XP remaining to next level (0 if max)
	xp_level_start: int  # XP threshold for current level
	xp_level_end: int  # XP threshold for next level (0 if max)


class StatsResponse(BaseModel):
	"""Stats grid data."""

	subject: str | None = None  # None = combined across all subjects
	streak: int
	items_learned: int
	total_xp: int


class MemoryMasteryResponse(BaseModel):
	"""Memory mastery breakdown from FSRS."""

	subject: str | None = None
	mature: int
	learning: int
	new_items: int
	total: int


class DailyXP(BaseModel):
	"""Single day's XP data."""

	date: str  # YYYY-MM-DD
	day_name: str  # Mon, Tue, etc.
	xp: int


class WeeklyActivityResponse(BaseModel):
	"""Weekly activity chart data."""

	subject: str | None = None
	week_start: str  # Monday YYYY-MM-DD
	days: list[DailyXP]
	total_xp: int


class AvatarUpdateRequest(BaseModel):
	"""Request to change avatar."""

	avatar: str


class AvatarUpdateResponse(BaseModel):
	"""Response after avatar change."""

	avatar: str
	success: bool


class LogoutResponse(BaseModel):
	"""Response after logout."""

	success: bool
	message: str = "Logged out successfully"
