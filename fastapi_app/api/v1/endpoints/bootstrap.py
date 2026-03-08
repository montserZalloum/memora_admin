"""Bootstrap endpoint — combines gamification settings, wallet, and review overview."""

import asyncio

import structlog
from fastapi import APIRouter

from fastapi_app.api.deps import (
	CurrentUser,
	ReviewServiceDep,
	SettingsServiceDep,
	WalletServiceDep,
)
from fastapi_app.models.bootstrap import BootstrapResponse
from fastapi_app.models.review import SubjectReviewCount
from fastapi_app.models.wallet import WalletResponse

logger = structlog.get_logger()

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


@router.get("", response_model=BootstrapResponse)
async def get_bootstrap(
	user: CurrentUser,
	settings_service: SettingsServiceDep,
	wallet_service: WalletServiceDep,
	review_service: ReviewServiceDep,
) -> BootstrapResponse:
	"""Get combined init data in a single call: gamification settings, wallet, and review overview.

	All three are independent Redis reads executed concurrently.
	Replaces separate calls to /settings/gamification, /wallet, and /reviews.
	"""
	gamification, wallet_data, subjects_data = await asyncio.gather(
		settings_service.get_gamification_settings(),
		wallet_service.get_wallet(user.sub),
		review_service.get_overview(user.sub),
	)

	reviews = [
		SubjectReviewCount(
			subject_id=s.get("subject", ""),
			due_count=s.get("due_count", 0),
		)
		for s in subjects_data
		if s.get("due_count", 0) > 0
	]

	logger.debug("bootstrap_returned", user_id=user.sub)

	return BootstrapResponse(
		gamification=gamification,
		wallet=WalletResponse(xp=wallet_data["xp"], streak=wallet_data["streak"]),
		reviews=reviews,
	)
