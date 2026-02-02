"""Wallet models for XP and streak tracking."""

from pydantic import BaseModel


class WalletResponse(BaseModel):
	"""Response for GET /wallet endpoint.

	Per CONTEXT.md:
	- Returns XP total + current streak only (minimal)
	- No streak_date in response (client doesn't need it)
	"""

	xp: int
	streak: int


class CompletionReward(BaseModel):
	"""Reward data returned with lesson completion.

	Returned as part of completion response to show
	immediate feedback on XP earned and streak status.
	"""

	xp_awarded: int  # XP awarded for this completion
	is_replay: bool  # Whether this was a replay
	streak: int  # Current streak after update
