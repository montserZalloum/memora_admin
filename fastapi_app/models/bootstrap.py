"""Pydantic models for the bootstrap endpoint (combined init data)."""

from pydantic import BaseModel

from fastapi_app.models.review import SubjectReviewCount
from fastapi_app.models.settings import GamificationSettings
from fastapi_app.models.wallet import WalletResponse


class BootstrapResponse(BaseModel):
	"""Combined response for app init — gamification + wallet + reviews + challenge XP in one call."""

	gamification: GamificationSettings
	wallet: WalletResponse
	reviews: list[SubjectReviewCount]
	challenge_xp: int = 0
