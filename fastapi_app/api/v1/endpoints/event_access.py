"""Player-facing live event access endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from fastapi_app.api.deps import CurrentUser, RedisClient, get_frappe_client
from fastapi_app.services.event_access import EventAccessService
from fastapi_app.services.frappe_client import FrappeAPIError
from fastapi_app.services.premium import PremiumService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/events", tags=["event-access"])


# --- Request / Response Models ---


class EventPurchaseResponse(BaseModel):
	purchase_id: str
	payment_url: str
	amount: float
	currency: str


class EventVoucherRedeemRequest(BaseModel):
	code: str


class EventVoucherRedeemResponse(BaseModel):
	access_id: str
	event_id: str


class EventAccessStateResponse(BaseModel):
	has_access: bool
	access_type: str | None = None  # premium | purchase | voucher | admin | free | None
	is_covered_by_premium: bool
	is_paid: bool
	price: float | None = None
	currency: str | None = None
	has_pending_purchase: bool


# --- Endpoints ---


@router.post("/{event_id}/purchase", response_model=EventPurchaseResponse, status_code=status.HTTP_201_CREATED)
async def create_event_ticket_purchase(
	event_id: str,
	user: CurrentUser,
	redis_client: RedisClient,
):
	"""Purchase a ticket for a paid live event (T021).

	Rejects if player has usable premium (FR-007: prevent double-charging),
	already has active access, or has a pending purchase.
	"""
	player_id = user.sub
	plan_id = user.plan

	frappe_client = await get_frappe_client()
	premium_svc = PremiumService(redis_client, frappe_client)
	event_svc = EventAccessService(redis_client, frappe_client)

	# FR-007: Reject if player has usable premium (prevents double-charging)
	if plan_id:
		premium_state = await premium_svc.is_plan_premium_usable(player_id, plan_id)
		if premium_state.usable:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail={"error": "COVERED_BY_PREMIUM", "detail": "Player has usable plan premium. No ticket purchase needed."},
			)

	# Check for existing active access
	access_state = await event_svc.has_active_access(player_id, event_id)
	if access_state.has_access:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"error": "ALREADY_HAS_ACCESS", "detail": "Player already has active access to this event."},
		)

	# Check for pending purchase
	has_pending = await event_svc.has_pending_purchase(player_id, event_id)
	if has_pending:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"error": "PENDING_PURCHASE", "detail": "A pending purchase already exists for this event."},
		)

	# Acquire lock to prevent concurrent purchase creation
	if not await event_svc.acquire_lock(player_id, event_id):
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"error": "CONCURRENT_REQUEST", "detail": "Another operation is in progress."},
		)

	try:
		# Create purchase via Frappe
		result = await frappe_client._call_method(
			"memora_admin.memora_admin.services.premium.event_purchase.create_event_purchase",
			player=player_id,
			event=event_id,
		)

		return EventPurchaseResponse(
			purchase_id=result.get("purchase_id", ""),
			payment_url=result.get("payment_url", ""),
			amount=float(result.get("amount", 0)),
			currency=result.get("currency", "JOD"),
		)
	except FrappeAPIError as e:
		logger.error("event_purchase_creation_failed", player=player_id, event=event_id, error=str(e))
		if e.status_code == 417:  # ValidationError from Frappe
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail={"error": "PURCHASE_FAILED", "detail": e.message},
			)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail={"error": "INTERNAL_ERROR", "detail": "Failed to create event purchase."},
		)
	finally:
		await event_svc.release_lock(player_id, event_id)


@router.post("/{event_id}/voucher/redeem", response_model=EventVoucherRedeemResponse, status_code=status.HTTP_201_CREATED)
async def redeem_event_voucher(
	event_id: str,
	body: EventVoucherRedeemRequest,
	user: CurrentUser,
	redis_client: RedisClient,
):
	"""Redeem a voucher code for live event access (T029).

	Verifies the code via HMAC-SHA256, creates event access entitlement
	and redemption record atomically.
	"""
	player_id = user.sub

	frappe_client = await get_frappe_client()
	event_svc = EventAccessService(redis_client, frappe_client)

	# Acquire lock to prevent concurrent redemption
	if not await event_svc.acquire_lock(player_id, event_id):
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"error": "CONCURRENT_REQUEST", "detail": "Another operation is in progress."},
		)

	try:
		result = await frappe_client._call_method(
			"memora_admin.memora_admin.services.premium.voucher.redeem_event_access_voucher",
			player=player_id,
			event=event_id,
			code=body.code,
		)

		return EventVoucherRedeemResponse(
			access_id=result.get("access_id", ""),
			event_id=result.get("event_id", event_id),
		)
	except FrappeAPIError as e:
		logger.error("event_voucher_redemption_failed", player=player_id, event=event_id, error=str(e))
		error_map = {
			"VOUCHER_INACTIVE": (status.HTTP_400_BAD_REQUEST, "Voucher is inactive."),
			"VOUCHER_EXPIRED": (status.HTTP_400_BAD_REQUEST, "Voucher has expired."),
			"VOUCHER_EXHAUSTED": (status.HTTP_400_BAD_REQUEST, "Voucher has reached maximum redemptions."),
			"ALREADY_REDEEMED": (status.HTTP_409_CONFLICT, "You have already redeemed this voucher."),
			"ALREADY_HAS_ACCESS": (status.HTTP_409_CONFLICT, "You already have access to this event."),
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
		await event_svc.release_lock(player_id, event_id)


@router.get("/{event_id}/access-state", response_model=EventAccessStateResponse)
async def get_event_access_state(
	event_id: str,
	user: CurrentUser,
	redis_client: RedisClient,
):
	"""Get player's event access state for frontend rendering (FR-014, T037).

	Returns complete access state including premium bypass status in one call.
	"""
	player_id = user.sub
	plan_id = user.plan

	frappe_client = await get_frappe_client()
	premium_svc = PremiumService(redis_client, frappe_client)
	event_svc = EventAccessService(redis_client, frappe_client)

	# Get event info (is_paid, price, currency)
	try:
		event_info = await frappe_client._call_method(
			"frappe.client.get_value",
			doctype="Memora Live Challenge Event",
			fieldname=["is_paid", "price", "currency"],
			filters={"name": event_id},
		)
	except Exception:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"error": "EVENT_NOT_FOUND", "detail": "Event not found."},
		)

	is_paid = bool(event_info.get("is_paid")) if event_info else False
	price = float(event_info.get("price") or 0) if event_info else None
	currency = event_info.get("currency") if event_info else None

	# Check premium bypass
	is_covered_by_premium = False
	if plan_id and is_paid:
		premium_state = await premium_svc.is_plan_premium_usable(player_id, plan_id)
		is_covered_by_premium = premium_state.usable

	# Determine access
	has_access = False
	access_type = None

	if not is_paid:
		has_access = True
		access_type = "free"
	elif is_covered_by_premium:
		has_access = True
		access_type = "premium"
	else:
		access_state = await event_svc.has_active_access(player_id, event_id)
		if access_state.has_access:
			has_access = True
			access_type = access_state.access_type

	# Check pending purchase
	has_pending = await event_svc.has_pending_purchase(player_id, event_id) if is_paid else False

	return EventAccessStateResponse(
		has_access=has_access,
		access_type=access_type,
		is_covered_by_premium=is_covered_by_premium,
		is_paid=is_paid,
		price=price if is_paid else None,
		currency=currency if is_paid else None,
		has_pending_purchase=has_pending,
	)
