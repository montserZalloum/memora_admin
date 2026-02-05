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
