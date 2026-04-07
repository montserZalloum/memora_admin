"""Web Push subscription and VAPID key endpoints."""

import time

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from fastapi_app.api.deps import CurrentUser, RedisClient, get_frappe_client
from fastapi_app.core.redis_keys import devices_key
from fastapi_app.models.push import (
	PushStatusResponse,
	PushSubscribeRequest,
	VapidKeyResponse,
)
from fastapi_app.services.device import DeviceService

logger = structlog.get_logger()

router = APIRouter(prefix="/push", tags=["push"])

# Module-level cache for VAPID public key with TTL (refreshes hourly).
_vapid_cache: tuple[str, float] | None = None
_VAPID_CACHE_TTL = 3600  # 1 hour


@router.get("/vapid-key", response_model=VapidKeyResponse)
async def get_vapid_key() -> VapidKeyResponse:
	"""Get VAPID public key for browser push subscription.

	Public endpoint — no authentication required.
	The VAPID public key is not secret; it is shared with all clients.
	"""
	global _vapid_cache

	now = time.monotonic()
	if _vapid_cache is None or (now - _vapid_cache[1]) > _VAPID_CACHE_TTL:
		frappe_client = await get_frappe_client()
		try:
			result = await frappe_client.call("memora_admin.api.settings.get_vapid_public_key")
			key = result.get("public_key", "") if result else ""
		except Exception:
			logger.exception("vapid_key_fetch_failed")
			raise HTTPException(
				status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
				detail="Unable to fetch VAPID key",
			)
		if key:
			_vapid_cache = (key, now)

	if _vapid_cache is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="VAPID keys not configured",
		)

	return VapidKeyResponse(public_key=_vapid_cache[0])


@router.post("/subscribe", response_model=PushStatusResponse)
async def subscribe_push(
	body: PushSubscribeRequest,
	request: Request,
	user: CurrentUser,
	redis_client: RedisClient,
) -> PushStatusResponse:
	"""Register a Web Push subscription for the current device.

	Requires X-Device-ID header (same device that was registered at login).
	Stores the push subscription JSON in the device hash for later use
	by the push notification service.
	"""
	device_id = request.headers.get("X-Device-ID")
	if not device_id:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail={"code": "DEVICE_ID_REQUIRED", "message": "X-Device-ID header required"},
		)

	user_id = user.sub
	device_service = DeviceService(redis_client)

	if not await device_service.validate_device(user_id, device_id):
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "DEVICE_NOT_FOUND", "message": "Device not registered"},
		)

	sub_json = body.subscription.model_dump_json()
	key = devices_key(user_id)
	await redis_client.hset(key, f"device:{device_id}:push_sub", sub_json)

	logger.info("push_subscribed", user_id=user_id, device_id=device_id)
	return PushStatusResponse(status="subscribed")


@router.delete("/subscribe", response_model=PushStatusResponse)
async def unsubscribe_push(
	request: Request,
	user: CurrentUser,
	redis_client: RedisClient,
) -> PushStatusResponse:
	"""Remove the Web Push subscription for the current device.

	Requires X-Device-ID header.
	"""
	device_id = request.headers.get("X-Device-ID")
	if not device_id:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail={"code": "DEVICE_ID_REQUIRED", "message": "X-Device-ID header required"},
		)

	user_id = user.sub
	key = devices_key(user_id)
	await redis_client.hdel(key, f"device:{device_id}:push_sub")

	logger.info("push_unsubscribed", user_id=user_id, device_id=device_id)
	return PushStatusResponse(status="unsubscribed")
