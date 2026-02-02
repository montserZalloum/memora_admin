"""Wallet endpoints for XP and streak display."""

import structlog
from fastapi import APIRouter, HTTPException, status

from fastapi_app.api.deps import CurrentUser, WalletServiceDep
from fastapi_app.models.wallet import WalletResponse

logger = structlog.get_logger()

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/", response_model=WalletResponse)
async def get_my_wallet(
	user: CurrentUser,
	wallet_service: WalletServiceDep,
) -> WalletResponse:
	"""
	Get authenticated player's wallet.

	Returns XP total and current streak.
	Per CONTEXT.md: No streak_date in response.
	"""
	wallet = await wallet_service.get_wallet(user.sub)

	logger.info(
		"wallet_fetched",
		user_id=user.sub,
		xp=wallet["xp"],
		streak=wallet["streak"],
	)

	return WalletResponse(
		xp=wallet["xp"],
		streak=wallet["streak"],
	)


@router.get("/{player_id}", response_model=WalletResponse)
async def get_player_wallet(
	player_id: str,
	user: CurrentUser,
	wallet_service: WalletServiceDep,
) -> WalletResponse:
	"""
	Get specified player's wallet (admin only).

	Per CONTEXT.md: Admin can view any player's wallet.
	Returns 403 for non-admin users.
	"""
	# Admin check per CONTEXT.md
	if user.role != "System Manager":
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "ADMIN_REQUIRED", "message": "Admin access required"},
		)

	wallet = await wallet_service.get_wallet(player_id)

	logger.info(
		"admin_wallet_fetched",
		admin_id=user.sub,
		player_id=player_id,
		xp=wallet["xp"],
		streak=wallet["streak"],
	)

	return WalletResponse(
		xp=wallet["xp"],
		streak=wallet["streak"],
	)
