"""Leaderboard models for competitive XP rankings.

Per CONTEXT.md (Phase 10):
- Three leaderboard types: daily, weekly, all-time
- Tie-breaking: earlier achiever wins (composite score)
- Dense ranking: tied players share same rank number
- Optional subject filtering for class-specific competitions
"""

from typing import Literal

from pydantic import BaseModel

# Leaderboard type as Literal for type safety and validation
LeaderboardType = Literal["daily", "weekly", "alltime"]


class LeaderboardEntry(BaseModel):
	"""Single entry in a leaderboard.

	Per CONTEXT.md:
	- rank: Dense rank (tied players share same number, e.g., two #5s then #7)
	- display_name: Player's display name
	- xp: XP value for the leaderboard period
	- avatar: File identifier for avatar (client constructs full URL)
	- is_me: True when this entry is the requesting user (for neighbors list)
	"""

	rank: int
	player_id: str
	display_name: str
	xp: int
	avatar: str | None = None
	is_me: bool = False


class LeaderboardResponse(BaseModel):
	"""Response for GET /leaderboard/{type}.

	Returns top N players for the specified leaderboard type,
	optionally filtered by subject.

	Per CONTEXT.md:
	- Number of players returned is configurable via limit parameter
	- All players visible (no anonymization)
	- View-only list (entries don't link to profiles)
	"""

	leaderboard_type: LeaderboardType
	subject_id: str | None = None
	entries: list[LeaderboardEntry]
	total_players: int


class MyRankResponse(BaseModel):
	"""Response for GET /leaderboard/{type}/me.

	Per CONTEXT.md:
	- Separate endpoint from main leaderboard
	- Include +/-2 neighbors for context around user's position
	- Include distance to next tier (XP needed to match player in rank above)
	- Unranked users (0 XP) treated as tied for last place
	"""

	rank: int
	xp: int
	xp_to_next: int | None  # XP needed to match next higher tier, None if #1 or unranked on empty board
	neighbors: list[LeaderboardEntry]  # Includes is_me=True for requesting user
	total_players: int
