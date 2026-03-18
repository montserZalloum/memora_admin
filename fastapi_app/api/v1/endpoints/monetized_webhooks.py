"""Monetized payment webhook endpoint (T017).

Single endpoint for both plan_premium and live_event purchase types.
Gateway-agnostic — any payment provider can call this endpoint.
"""

import hmac as hmac_module

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from redis.exceptions import RedisError

from fastapi_app.api.deps import RedisClient, get_frappe_client
from fastapi_app.core.config import Settings, get_settings
from fastapi_app.core.redis_keys import (
	MONETIZED_WEBHOOK_IDEM_TTL,
	event_access_key,
	monetized_webhook_idempotency_key,
	premium_key,
)
from fastapi_app.services.frappe_client import FrappeAPIError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# --- Webhook Auth ---


async def verify_webhook_secret(
	x_webhook_secret: str = Header(...),
	settings: Settings = Depends(get_settings),
):
	"""Verify the webhook caller provides a valid shared secret."""
	if not settings.webhook_secret:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Webhook authentication not configured.",
		)
	if not hmac_module.compare_digest(x_webhook_secret, settings.webhook_secret):
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid webhook secret.",
		)


# --- Request / Response Models ---


class MonetizedPaymentPayload(BaseModel):
	idempotency_key: str
	purchase_type: str  # plan_premium | live_event
	purchase_id: str
	transaction_id: str
	status: str  # success | failed
	payment_gateway: str = ""
	metadata: dict | None = None


class WebhookResponse(BaseModel):
	status: str  # processed | already_processed
	entitlement_id: str | None = None


# --- Endpoint ---


@router.post(
	"/monetized-payment",
	response_model=WebhookResponse,
	dependencies=[Depends(verify_webhook_secret)],
)
async def handle_monetized_payment(
	payload: MonetizedPaymentPayload,
	redis_client: RedisClient,
):
	"""Process monetized payment webhook (FR-016 idempotency).

	Flow:
	1. Verify webhook secret (via dependency)
	2. Check Redis SET NX idempotency — fail closed on Redis error
	3. Route to handler based on purchase_type
	4. Mark idempotency as completed after success
	"""
	# Idempotency check (FR-016) — fail closed on Redis error
	idem_key = monetized_webhook_idempotency_key(payload.idempotency_key)
	try:
		is_new = await redis_client.set(idem_key, "processing", nx=True, ex=MONETIZED_WEBHOOK_IDEM_TTL)
		if not is_new:
			logger.info("duplicate_webhook", idempotency_key=payload.idempotency_key)
			return WebhookResponse(status="already_processed")
	except RedisError:
		logger.error("idempotency_check_failed", idempotency_key=payload.idempotency_key)
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Service temporarily unavailable. Please retry.",
		)

	# Only process successful payments
	if payload.status != "success":
		logger.info("payment_not_success", status=payload.status, purchase_id=payload.purchase_id)
		return WebhookResponse(status="processed")

	frappe_client = await get_frappe_client()

	try:
		if payload.purchase_type == "plan_premium":
			result = await _handle_plan_premium_payment(payload, redis_client, frappe_client)
		elif payload.purchase_type == "live_event":
			result = await _handle_live_event_payment(payload, redis_client, frappe_client)
		else:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail=f"Unknown purchase_type: {payload.purchase_type}",
			)
	except HTTPException:
		# Processing failed — remove idempotency marker so retries can succeed
		try:
			await redis_client.delete(idem_key)
		except Exception:
			pass
		raise

	# Mark as completed after successful processing
	try:
		await redis_client.set(idem_key, "completed", ex=MONETIZED_WEBHOOK_IDEM_TTL)
	except Exception:
		pass  # Processing succeeded — marker update is best-effort

	return result


async def _handle_plan_premium_payment(
	payload: MonetizedPaymentPayload,
	redis_client,
	frappe_client,
) -> WebhookResponse:
	"""Confirm plan premium purchase: mark paid, create entitlement, invoice, invalidate cache."""
	try:
		result = await frappe_client._call_method(
			"memora_admin.memora_admin.services.premium.purchase.confirm_plan_premium_purchase",
			purchase_id=payload.purchase_id,
			transaction_id=payload.transaction_id,
			payment_gateway=payload.payment_gateway,
		)

		premium_id = result.get("premium_id", "")
		player_id = result.get("player_id", "")
		plan_id = result.get("plan_id", "")

		logger.info(
			"plan_premium_confirmed",
			purchase_id=payload.purchase_id,
			premium_id=premium_id,
		)

		# Invalidate Redis cache so player sees fresh state immediately
		if player_id and plan_id:
			try:
				await redis_client.delete(premium_key(player_id, plan_id))
			except Exception:
				logger.warning("webhook_cache_invalidation_failed", player=player_id, plan=plan_id)

		return WebhookResponse(status="processed", entitlement_id=premium_id)

	except FrappeAPIError as e:
		logger.error(
			"plan_premium_webhook_failed",
			purchase_id=payload.purchase_id,
			error=str(e),
		)
		if e.status_code == 417:
			raise HTTPException(
				status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
				detail="Payment processing validation failed.",
			)
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Failed to process payment.",
		)


async def _handle_live_event_payment(
	payload: MonetizedPaymentPayload,
	redis_client,
	frappe_client,
) -> WebhookResponse:
	"""Confirm live event purchase: mark paid, create access, invoice, invalidate cache (T022)."""
	try:
		result = await frappe_client._call_method(
			"memora_admin.memora_admin.services.premium.event_purchase.confirm_event_purchase",
			purchase_id=payload.purchase_id,
			transaction_id=payload.transaction_id,
			payment_gateway=payload.payment_gateway,
		)

		access_id = result.get("access_id", "")
		player_id = result.get("player_id", "")
		event_id = result.get("event_id", "")

		logger.info(
			"live_event_access_confirmed",
			purchase_id=payload.purchase_id,
			access_id=access_id,
		)

		# Invalidate Redis cache so player sees fresh state immediately
		if player_id and event_id:
			try:
				await redis_client.delete(event_access_key(player_id, event_id))
			except Exception:
				logger.warning("webhook_cache_invalidation_failed", player=player_id, event=event_id)

		return WebhookResponse(status="processed", entitlement_id=access_id)

	except FrappeAPIError as e:
		logger.error(
			"live_event_webhook_failed",
			purchase_id=payload.purchase_id,
			error=str(e),
		)
		if e.status_code == 417:
			raise HTTPException(
				status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
				detail="Payment processing validation failed.",
			)
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Failed to process payment.",
		)
