"""Authentication endpoints for player and admin login."""

from datetime import timedelta

import jwt
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from fastapi_app.api.deps import RedisClient, SettingsDep, get_frappe_client
from fastapi_app.core.security import create_access_token, create_refresh_token, decode_token
from fastapi_app.models.auth import (
	AdminLoginRequest,
	LoginProfile,
	PlayerLoginRequest,
	PlayerLoginResponse,
	RefreshRequest,
	TokenResponse,
)
from fastapi_app.services.device import DeviceService
from fastapi_app.services.frappe import FrappeAuthService
from fastapi_app.services.frappe_client import FrappeAPIError
from fastapi_app.services.rate_limit import RateLimiter
from fastapi_app.services.session import SessionService
from fastapi_app.services.settings import SettingsService
from fastapi_app.services.wallet import WalletService

logger = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_client_ip(request: Request) -> str:
	"""Extract client IP, respecting X-Forwarded-For from nginx."""
	forwarded = request.headers.get("X-Forwarded-For")
	if forwarded:
		# First IP in chain is the original client
		return forwarded.split(",")[0].strip()
	return request.client.host if request.client else "unknown"


@router.post("/player/login", response_model=PlayerLoginResponse)
async def player_login(
	request: Request,
	credentials: PlayerLoginRequest,
	redis: RedisClient,
	settings: SettingsDep,
) -> PlayerLoginResponse | JSONResponse:
	"""
	Player login with phone + password.

	Verifies credentials via Frappe verify_player_password API (single call, no Frappe session).
	Returns JWT tokens and profile data (display_name, avatar, xp).

	Requires X-Device-ID header for device registration.
	Rate limited: 10 attempts/min per IP, 5 attempts/min per account.
	New login invalidates any previous session.
	"""
	# 1. Require X-Device-ID header
	device_id = request.headers.get("X-Device-ID")
	if not device_id:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail={"code": "DEVICE_ID_REQUIRED", "message": "X-Device-ID header required"},
		)

	# Extract optional headers for device info
	user_agent = request.headers.get("User-Agent", "Unknown")
	platform_hint = request.headers.get("X-Platform")  # Optional: iOS, Android, Web

	client_ip = _get_client_ip(request)

	# 2. Rate limit check
	rate_limiter = RateLimiter(redis)
	allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(
		ip_address=client_ip,
		target_account=credentials.mobile,
	)

	if not allowed:
		return JSONResponse(
			status_code=status.HTTP_429_TOO_MANY_REQUESTS,
			content={
				"detail": "Too many login attempts",
				"retry_after": retry_after,
			},
			headers={"Retry-After": str(retry_after)},
		)

	# 3. Verify credentials via Frappe API (single call -- no Frappe session)
	frappe_client = await get_frappe_client()
	try:
		profile = await frappe_client.call(
			"memora_admin.api.auth.verify_player_password",
			{"mobile": credentials.mobile, "password": credentials.password},
		)
	except FrappeAPIError:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid credentials",
		)

	# 4. Check profile has plan
	if not profile or not profile.get("plan"):
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid credentials",
		)

	player_id = profile["player_id"]

	# 5. Fetch session_timeout_days from Memora Settings
	settings_service = SettingsService(redis, frappe_client)
	game_settings = await settings_service.get_gamification_settings()
	session_ttl_days = game_settings.session_timeout_days
	max_devices = game_settings.max_devices_per_player

	# 6. Device registration (atomic with limit check)
	device_service = DeviceService(redis, key_prefix=settings.redis_key_prefix)
	device_result = await device_service.register_device(
		user_id=player_id,
		device_id=device_id,
		user_agent=user_agent,
		max_devices=max_devices,
		platform_hint=platform_hint,
	)

	if not device_result.success:
		return JSONResponse(
			status_code=status.HTTP_429_TOO_MANY_REQUESTS,
			content={
				"code": "DEVICE_LIMIT_EXCEEDED",
				"message": f"Device limit reached ({device_result.current_count}/{device_result.max_count}). Contact support to manage your devices.",
			},
		)

	# 7. Fetch wallet for XP (with FrappeClient for hydration after cache flush)
	wallet_service = WalletService(redis, key_prefix=settings.redis_key_prefix, frappe_client=frappe_client)
	wallet = await wallet_service.get_wallet(player_id)

	# 8. Create session (invalidates any previous session)
	session_service = SessionService(redis, key_prefix=f"{settings.redis_key_prefix}session:")
	family_id = await session_service.create_session(
		player_id,
		plan_id=profile["plan"],
		ttl_days=session_ttl_days,
	)

	# 9. Create tokens (player: mobile claim, no email)
	access_token = create_access_token(
		user_id=player_id,
		mobile=profile["mobile"],
		plan_id=profile["plan"],
		display_name=profile.get("display_name", ""),
		family_id=family_id,
		expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
	)

	refresh_token = create_refresh_token(
		user_id=player_id,
		family_id=family_id,
		expires_delta=timedelta(days=session_ttl_days),
	)

	# 10. Return enriched response (no gender per CONTEXT.md)
	return PlayerLoginResponse(
		access_token=access_token,
		refresh_token=refresh_token,
		profile=LoginProfile(
			display_name=profile.get("display_name", ""),
			avatar=profile.get("avatar") or "default_avatar",
			xp=wallet.get("xp", 0),
		),
	)


@router.post("/admin/login", response_model=TokenResponse)
async def admin_login(
	request: Request,
	credentials: AdminLoginRequest,
	redis: RedisClient,
	settings: SettingsDep,
) -> TokenResponse | JSONResponse:
	"""
	Admin login with email + password.

	Uses existing FrappeAuthService to verify credentials via Frappe session.
	Returns JWT tokens only (no profile enrichment).
	No X-Device-ID required for admin.
	"""
	client_ip = _get_client_ip(request)

	# 1. Rate limit check
	rate_limiter = RateLimiter(redis)
	allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(
		ip_address=client_ip,
		target_account=credentials.email,
	)

	if not allowed:
		return JSONResponse(
			status_code=status.HTTP_429_TOO_MANY_REQUESTS,
			content={
				"detail": "Too many login attempts",
				"retry_after": retry_after,
			},
			headers={"Retry-After": str(retry_after)},
		)

	# 2. Verify via FrappeAuthService (admin uses Frappe User auth)
	frappe_service = FrappeAuthService(settings.frappe_url, settings.frappe_site)
	user, _profile_data = await frappe_service.verify_credentials(
		credentials.email, credentials.password
	)

	if not user:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid credentials",
		)

	# 3. Create session (admin uses email as user_id)
	session_service = SessionService(redis, key_prefix=f"{settings.redis_key_prefix}session:")
	family_id = await session_service.create_session(
		user.user_id,
		plan_id="",
		ttl_days=settings.jwt_refresh_token_expire_days,
	)

	# 4. Create tokens (admin: email claim, no mobile, role included)
	access_token = create_access_token(
		user_id=user.user_id,
		email=user.email,
		plan_id="",
		display_name=user.full_name,
		family_id=family_id,
		role="System Manager",
		expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
	)

	refresh_token = create_refresh_token(
		user_id=user.user_id,
		family_id=family_id,
		expires_delta=timedelta(days=settings.jwt_refresh_token_expire_days),
	)

	# 5. Return tokens only (no profile enrichment for admin)
	return TokenResponse(
		access_token=access_token,
		refresh_token=refresh_token,
	)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
	body: RefreshRequest,
	redis: RedisClient,
	settings: SettingsDep,
) -> TokenResponse:
	"""
	Exchange refresh token for new access token.

	Works for both player and admin tokens transparently.
	Validates session is still active (not invalidated by new login).
	Returns same refresh token (not rotated).
	Plan_id sourced from session (not token) to reflect any admin changes.
	"""
	try:
		# Decode refresh token (validates signature, expiry, type)
		payload = decode_token(body.refresh_token, verify_type="refresh")

		user_id = payload["sub"]
		family_id = payload["fid"]

		# Validate session is still active and get plan_id from session
		session_service = SessionService(redis, key_prefix=f"{settings.redis_key_prefix}session:")
		is_valid, plan_id = await session_service.validate_session(user_id, family_id)

		if not is_valid or not plan_id:
			# Session invalidated by new login or plan change
			raise HTTPException(
				status_code=status.HTTP_401_UNAUTHORIZED,
				detail="Invalid credentials",
			)

		# Create new access token with plan_id from session
		# Pass email and mobile from original token payload (one will be None)
		access_token = create_access_token(
			user_id=user_id,
			email=payload.get("email"),
			mobile=payload.get("mobile"),
			plan_id=plan_id,
			display_name=payload.get("name", ""),
			family_id=family_id,
			role=payload.get("role"),
			expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
		)

		# Return same refresh token (per CONTEXT.md: not rotated)
		return TokenResponse(
			access_token=access_token,
			refresh_token=body.refresh_token,
		)

	except jwt.ExpiredSignatureError:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid credentials",
		)
	except jwt.InvalidTokenError:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid credentials",
		)
