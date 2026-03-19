"""Player-facing plan premium purchase endpoint."""

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from fastapi_app.api.deps import CurrentUser, RedisClient, get_frappe_client
from fastapi_app.services.frappe_client import FrappeAPIError
from fastapi_app.services.premium import PremiumService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/premium", tags=["premium"])


# --- Request / Response Models ---


class PurchaseRequest(BaseModel):
	plan_id: str


class PurchaseResponse(BaseModel):
	purchase_id: str
	payment_url: str
	amount: float
	currency: str


# --- Endpoints ---


@router.post("/purchase", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED)
async def create_plan_premium_purchase(
	body: PurchaseRequest,
	user: CurrentUser,  # T038: player auth ensures own-records only
	redis_client: RedisClient,
):
	"""Initiate a plan premium purchase (T016).

	Creates a pending purchase record. Returns payment session info.
	Rejects if player already has usable premium or pending purchase.
	"""
	player_id = user.sub
	plan_id = body.plan_id

	frappe_client = await get_frappe_client()
	svc = PremiumService(redis_client, frappe_client)

	# Check for existing usable premium
	state = await svc.is_plan_premium_usable(player_id, plan_id)
	if state.usable:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"error": "ALREADY_PREMIUM", "detail": "Player already has usable premium for this plan."},
		)

	# Check for pending purchase
	has_pending = await svc.has_pending_purchase(player_id, plan_id)
	if has_pending:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"error": "PENDING_PURCHASE", "detail": "A pending purchase already exists for this plan."},
		)

	# Acquire lock to prevent concurrent purchase creation
	if not await svc.acquire_lock(player_id, plan_id):
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"error": "CONCURRENT_REQUEST", "detail": "Another operation is in progress."},
		)

	try:
		# Create purchase via Frappe
		result = await frappe_client._call_method(
			"memora_admin.memora_admin.services.premium.purchase.create_plan_premium_purchase",
			player=player_id,
			plan=plan_id,
		)

		return PurchaseResponse(
			purchase_id=result.get("purchase_id", ""),
			payment_url=result.get("payment_url", ""),
			amount=float(result.get("amount", 0)),
			currency=result.get("currency", "JOD"),
		)
	except FrappeAPIError as e:
		logger.error("purchase_creation_failed", player=player_id, plan=plan_id, error=str(e))
		if e.status_code == 417:  # ValidationError from Frappe
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail={"error": "PURCHASE_FAILED", "detail": e.message},
			)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail={"error": "INTERNAL_ERROR", "detail": "Failed to create purchase."},
		)
	finally:
		await svc.release_lock(player_id, plan_id)
