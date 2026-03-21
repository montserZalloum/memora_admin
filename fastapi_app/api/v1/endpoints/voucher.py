"""Voucher preview and redemption endpoints.

POST /voucher/preview  -- see available grants for a PIN (no rate limit)
POST /voucher/redeem   -- redeem a PIN for a chosen grant (rate limited on failure)
"""

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from fastapi_app.api.deps import CurrentUser, VoucherServiceDep
from fastapi_app.core.request_meta import get_client_ip
from fastapi_app.models.voucher import VoucherPreviewRequest, VoucherRedeemRequest
from fastapi_app.services.voucher import FAILURE_ERRORS

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/voucher", tags=["voucher"])

# ---------------------------------------------------------------------------
# Error-code to HTTP-status mapping (Claude's discretion per CONTEXT.md)
# ---------------------------------------------------------------------------

ERROR_STATUS_MAP: dict[str, int] = {
	"INVALID_PIN": 404,
	"NOT_ALLOCATED": 422,
	"ALREADY_REDEEMED": 409,
	"EXPIRED": 410,
	"VOID": 410,
	"BATCH_INACTIVE": 422,
	"SEASON_INACTIVE": 422,
	"ALL_GRANTS_OWNED": 409,
	"GRANT_NOT_IN_BATCH": 422,
	"ALREADY_OWNED": 409,
	"RATE_LIMITED": 429,
	"PLAN_NOT_ELIGIBLE": 422,
	"ALREADY_HAS_PREMIUM": 409,
	"REDEMPTION_FAILED": 500,
}


# ---------------------------------------------------------------------------
# POST /voucher/preview
# ---------------------------------------------------------------------------


@router.post("/preview")
async def preview_voucher(
	request: Request,
	body: VoucherPreviewRequest,
	user: CurrentUser,
	voucher_service: VoucherServiceDep,
):
	"""Preview what a voucher card unlocks (read-only, no state change).

	NOT rate limited -- students are young and not tech-savvy, preview is forgiving.

	Returns:
		200: face_value + list of available grants
		4xx: Machine-readable error code with appropriate HTTP status
		503: Redis unavailable
	"""
	try:
		result = await voucher_service.preview(body.pin, user.sub)

		if "error" in result:
			error_code = result["error"]
			http_status = ERROR_STATUS_MAP.get(error_code, 400)
			return JSONResponse(status_code=http_status, content=result)

		return result

	except redis.RedisError:
		logger.error("voucher_preview_redis_error", player_id=user.sub)
		return JSONResponse(
			status_code=503,
			content={"error": "SERVICE_UNAVAILABLE"},
		)
	except Exception:
		logger.exception("voucher_preview_unexpected_error", player_id=user.sub)
		return JSONResponse(
			status_code=500,
			content={"error": "INTERNAL_ERROR"},
		)


# ---------------------------------------------------------------------------
# POST /voucher/redeem
# ---------------------------------------------------------------------------


@router.post("/redeem")
async def redeem_voucher(
	request: Request,
	body: VoucherRedeemRequest,
	user: CurrentUser,
	voucher_service: VoucherServiceDep,
):
	"""Redeem a voucher card for a specific product grant.

	Rate limited on FAILED attempts only (5/player/hr, 20/IP/hr).
	Successful redeems do not count toward the limit.

	Returns:
		200: status + transaction_id
		429: RATE_LIMITED with retry_after seconds
		4xx: Machine-readable error code with appropriate HTTP status
		503: Redis unavailable
	"""
	client_ip = get_client_ip(request)

	try:
		# 1. Check rate limit BEFORE operation
		retry_after = await voucher_service.check_rate_limit(user.sub, client_ip)
		if retry_after is not None and retry_after > 0:
			return JSONResponse(
				status_code=429,
				content={"error": "RATE_LIMITED", "retry_after": retry_after},
			)

		# 2. Call Frappe via service
		result = await voucher_service.redeem(body.pin, user.sub, body.grant_id, client_ip)

		# 3. On failure, record for rate limiting (only for known failure errors)
		if "error" in result:
			error_code = result["error"]
			if error_code in FAILURE_ERRORS:
				await voucher_service.record_failure(user.sub, client_ip)
			http_status = ERROR_STATUS_MAP.get(error_code, 400)
			return JSONResponse(status_code=http_status, content=result)

		# 4. Success -- return directly (200 OK)
		return result

	except redis.RedisError:
		logger.error("voucher_redeem_redis_error", player_id=user.sub)
		return JSONResponse(
			status_code=503,
			content={"error": "SERVICE_UNAVAILABLE"},
		)
	except Exception:
		logger.exception("voucher_redeem_unexpected_error", player_id=user.sub)
		return JSONResponse(
			status_code=500,
			content={"error": "INTERNAL_ERROR"},
		)
