"""Player-facing plan premium endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from redis.exceptions import RedisError

from fastapi_app.api.deps import CurrentUser, RedisClient, get_frappe_client
from fastapi_app.core.redis_keys import voucher_redeem_lock_key
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


class VoucherRedeemRequest(BaseModel):
	code: str


class VoucherRedeemResponse(BaseModel):
	premium_id: str
	plan_id: str
	season_end: str | None = None


class PlanAccessStateResponse(BaseModel):
	has_usable_premium: bool
	reason: str  # none | plan_mismatch | season_ended | revoked | no_premium
	season_end: str | None = None
	source_type: str | None = None
	premium_id: str | None = None
	has_pending_purchase: bool


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


@router.post("/voucher/redeem", response_model=VoucherRedeemResponse, status_code=status.HTTP_201_CREATED)
async def redeem_plan_premium_voucher(
	body: VoucherRedeemRequest,
	user: CurrentUser,
	redis_client: RedisClient,
):
	"""Redeem a voucher code for plan premium (T028).

	Verifies the code via HMAC-SHA256, creates premium entitlement and
	redemption record atomically. Code is timing-safe compared.
	"""
	player_id = user.sub

	frappe_client = await get_frappe_client()
	svc = PremiumService(redis_client, frappe_client)

	# Acquire per-player lock for voucher redemption (target plan unknown until resolved)
	_lock_key = voucher_redeem_lock_key(player_id)
	_lock_acquired = False
	try:
		_lock_acquired = bool(await redis_client.set(_lock_key, "1", nx=True, ex=10))
		if not _lock_acquired:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail={"error": "CONCURRENT_REQUEST", "detail": "Another operation is in progress."},
			)
	except HTTPException:
		raise
	except RedisError:
		logger.error("voucher_lock_failed", player=player_id)
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Service temporarily unavailable. Please retry.",
		)

	try:
		result = await frappe_client._call_method(
			"memora_admin.memora_admin.services.premium.voucher.redeem_plan_premium_voucher",
			player=player_id,
			code=body.code,
		)

		return VoucherRedeemResponse(
			premium_id=result.get("premium_id", ""),
			plan_id=result.get("plan_id", ""),
			season_end=result.get("season_end"),
		)
	except FrappeAPIError as e:
		logger.error("voucher_redemption_failed", player=player_id, error=str(e))
		error_map = {
			"VOUCHER_INACTIVE": (status.HTTP_400_BAD_REQUEST, "Voucher is inactive."),
			"VOUCHER_EXPIRED": (status.HTTP_400_BAD_REQUEST, "Voucher has expired."),
			"VOUCHER_EXHAUSTED": (status.HTTP_400_BAD_REQUEST, "Voucher has reached maximum redemptions."),
			"ALREADY_REDEEMED": (status.HTTP_409_CONFLICT, "You have already redeemed this voucher."),
			"ALREADY_PREMIUM": (status.HTTP_409_CONFLICT, "You already have usable premium for this plan."),
		}
		for error_key, (http_status, detail) in error_map.items():
			if error_key in str(e.message):
				raise HTTPException(status_code=http_status, detail={"error": error_key, "detail": detail})
		if e.status_code == 417:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail={"error": "REDEMPTION_FAILED", "detail": e.message},
			)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail={"error": "INTERNAL_ERROR", "detail": "Failed to redeem voucher."},
		)
	finally:
		if _lock_acquired:
			try:
				await redis_client.delete(_lock_key)
			except Exception:
				pass


@router.get("/access-state/{plan_id}", response_model=PlanAccessStateResponse)
async def get_plan_access_state(
	plan_id: str,
	user: CurrentUser,
	redis_client: RedisClient,
):
	"""Get player's plan access state for frontend rendering (FR-014, T036).

	Returns complete access state in a single call — no assembly required.
	"""
	player_id = user.sub

	frappe_client = await get_frappe_client()
	svc = PremiumService(redis_client, frappe_client)

	state = await svc.is_plan_premium_usable(player_id, plan_id)
	has_pending = await svc.has_pending_purchase(player_id, plan_id)

	return PlanAccessStateResponse(
		has_usable_premium=state.usable,
		reason=state.reason,
		season_end=state.season_end,
		source_type=state.source_type,
		premium_id=state.premium_id,
		has_pending_purchase=has_pending,
	)
