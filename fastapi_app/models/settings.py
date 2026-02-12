"""Pydantic models for gamification settings."""

from pydantic import BaseModel


class GamificationSettings(BaseModel):
	"""Admin-configurable gamification values from Memora Settings.

	Per CONTEXT.md:
	- base_lesson_xp: Default XP for first completion
	- replay_xp: Fixed XP for replaying completed lessons
	- max_streak_multiplier_percent: Cap for streak bonus (e.g., 50 = 50% max)
	"""

	base_lesson_xp: int = 100
	replay_xp: int = 25
	max_streak_multiplier_percent: int = 50
	max_devices_per_player: int = 3
	default_max_hearts: int = 5
	xp_per_heart: int = 0
	session_timeout_days: int = 30
