"""Authentication endpoints for player login, admin login, and registration."""

import json
from datetime import timedelta

import jwt
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from fastapi_app.api.deps import RedisClient, SettingsDep, get_frappe_client
from fastapi_app.core.security import create_access_token, create_refresh_token, decode_token
from fastapi_app.core.ws_manager import ConnectionManager
from fastapi_app.models.auth import (
	AdminLoginRequest,
	LoginProfile,
	PasswordResetConfirmRequest,
	PasswordResetRequest,
	PasswordResetVerifyRequest,
	PasswordResetVerifyResponse,
	PlayerLoginRequest,
	PlayerLoginResponse,
	RefreshRequest,
	RegisterRequest,
	RegisterResendRequest,
	RegisterResponse,
	RegisterVerifyRequest,
	TokenResponse,
)
from fastapi_app.services.device import DeviceService
from fastapi_app.services.frappe import FrappeAuthService
from fastapi_app.services.frappe_client import FrappeAPIError
from fastapi_app.services.otp import OTPService
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


async def _force_kick_old_sessions(request: Request, player_id: str) -> None:
	"""Send session_invalidated event via WebSocket and close old connections.

	Called after creating a new session to immediately notify any connected
	devices that their session has been superseded. This provides instant
	feedback rather than waiting for the next API call to fail with 401.

	Best-effort: failures are logged but do not block the login flow.
	"""
	try:
		ws_manager: ConnectionManager | None = getattr(request.app.state, "ws_manager", None)
		if ws_manager is None:
			return

		# Send session_invalidated event to all connected WebSockets for this user
		event = json.dumps(
			{
				"type": "session_invalidated",
				"message": "Your session has been ended because you logged in on another device.",
			}
		)
		sent = await ws_manager.send_to_user(player_id, event)
		if sent > 0:
			logger.info("session_kick_sent", player_id=player_id, ws_count=sent)

		# Close all WebSocket connections for the user (they'll get the message first)
		connections = ws_manager._connections.get(player_id, set()).copy()
		for ws in connections:
			try:
				await ws.close(code=4001, reason="Session superseded by new login")
			except Exception:
				pass

	except Exception as e:
		# Best-effort: don't block login if WS kick fails
		logger.warning("session_kick_failed", player_id=player_id, error=str(e))


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

	# 8. Force-kick old WebSocket connections BEFORE creating new session
	# This sends "session_invalidated" to any connected devices and closes their WS
	await _force_kick_old_sessions(request, player_id)

	# 9. Create session (invalidates any previous session in Redis)
	session_service = SessionService(redis, key_prefix=f"{settings.redis_key_prefix}session:")
	family_id = await session_service.create_session(
		player_id,
		plan_id=profile["plan"],
		ttl_days=session_ttl_days,
	)

	# 10. Create tokens (player: mobile claim, no email)
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

	# 11. Return enriched response (no gender per CONTEXT.md)
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
	user, _profile_data = await frappe_service.verify_credentials(credentials.email, credentials.password)

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


# =============================================================================
# Registration endpoints
# =============================================================================

REGISTRATION_OPTIONS_CACHE_KEY = "memora:registration_options"
REGISTRATION_OPTIONS_CACHE_TTL = 300  # 5 minutes


async def _get_registration_options(redis: "RedisClient", frappe_client=None) -> dict:
	"""Fetch registration options from cache or Frappe API.

	Used by both GET /registration-options and the verify endpoint
	(for auto-populating season and major).
	"""
	cached = await redis.get(REGISTRATION_OPTIONS_CACHE_KEY)
	if cached:
		return json.loads(cached if isinstance(cached, str) else cached.decode())

	if frappe_client is None:
		frappe_client = await get_frappe_client()

	result = await frappe_client.call("memora_admin.api.auth.get_registration_options")

	if result:
		await redis.set(REGISTRATION_OPTIONS_CACHE_KEY, json.dumps(result), ex=REGISTRATION_OPTIONS_CACHE_TTL)

	return result or {}


@router.get("/registration-options")
async def get_registration_options(
	redis: RedisClient,
) -> dict:
	"""Return available options for registration form pickers.

	Returns grades (with nested majors), plans, and seasons.
	Avatars and genders are hardcoded client-side.
	Cached in Redis for 5 minutes (changes infrequently).
	"""
	return await _get_registration_options(redis)


@router.post("/player/register", response_model=RegisterResponse)
async def player_register(
	request: Request,
	body: RegisterRequest,
	redis: RedisClient,
) -> RegisterResponse:
	"""
	Player registration step 1: submit details and receive OTP.

	Checks upfront that the phone number is not already registered (409 error).
	Stores pending registration in Redis and sends OTP via configured provider.
	Returns an opaque pending_id for verification.

	Rate limited: 3 OTP/phone/10min, 10 OTP/IP/10min, 60s resend cooldown.
	"""
	client_ip = _get_client_ip(request)

	# Check if phone already registered in MariaDB (upfront for better UX)
	frappe_client = await get_frappe_client()
	try:
		phone_check = await frappe_client.call(
			"memora_admin.api.auth.check_phone_exists",
			{"mobile": body.mobile},
		)
		if phone_check and phone_check.get("exists"):
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="Phone number already registered",
			)
	except FrappeAPIError:
		# If Frappe is unreachable, let the registration proceed --
		# register_player will catch duplicates at verify time
		logger.warning("check_phone_exists_failed", mobile_suffix=body.mobile[-4:])

	# Create pending registration with OTP
	otp_service = OTPService(redis)
	pending_id = await otp_service.create_pending_registration(
		mobile=body.mobile,
		password=body.password,
		display_name=body.display_name,
		gender=body.gender,
		grade=body.grade,
		plan=body.plan,
		major=body.major,
		ip_address=client_ip,
	)

	return RegisterResponse(pending_id=pending_id, message="OTP sent")


@router.post("/player/register/verify", response_model=PlayerLoginResponse)
async def player_register_verify(
	request: Request,
	body: RegisterVerifyRequest,
	redis: RedisClient,
	settings: SettingsDep,
) -> PlayerLoginResponse | JSONResponse:
	"""
	Player registration step 2: verify OTP and create account.

	On valid OTP, creates the Player Profile via Frappe register_player API,
	then auto-logs the player in (returns tokens + profile immediately).

	Requires X-Device-ID header for auto-login device registration.
	Season is auto-populated from the latest published season.
	Major is auto-derived from the selected plan when not provided.
	"""
	# Require X-Device-ID for auto-login
	device_id = request.headers.get("X-Device-ID")
	if not device_id:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail={"code": "DEVICE_ID_REQUIRED", "message": "X-Device-ID header required"},
		)

	# Verify OTP -- returns registration data on success
	otp_service = OTPService(redis)
	reg_data = await otp_service.verify_registration_otp(body.pending_id, body.otp)

	# Get registration options for season and major auto-population
	frappe_client = await get_frappe_client()
	options = await _get_registration_options(redis, frappe_client)

	# Auto-populate season from latest published season
	seasons = options.get("seasons", [])
	if not seasons:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="No active season available",
		)
	season = seasons[0]["name"]

	# Auto-derive major from plan when not provided
	major = reg_data.get("major")
	if not major:
		plan_name = reg_data["plan"]
		plans = options.get("plans", [])
		for p in plans:
			if p["name"] == plan_name:
				major = p.get("major")
				break

		# If plan has no major, use first major of the selected grade
		if not major:
			grade_name = reg_data["grade"]
			grades = options.get("grades", [])
			for g in grades:
				if g["name"] == grade_name and g.get("majors"):
					major = g["majors"][0]["name"]
					break

	# Create player via Frappe API
	try:
		profile = await frappe_client.call(
			"memora_admin.api.auth.register_player",
			{
				"mobile": reg_data["mobile"],
				"password": reg_data["password"],
				"plan": reg_data["plan"],
				"grade": reg_data["grade"],
				"major": major or "",
				"season": season,
				"display_name": reg_data["display_name"],
				"gender": reg_data["gender"],
			},
		)
	except FrappeAPIError as e:
		# Handle "Phone already registered" from Frappe (race condition safety net)
		if "Phone already registered" in str(e):
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="Phone number already registered",
			)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Registration failed. Please try again.",
		)

	player_id = profile["player_id"]

	# Auto-login flow (same pattern as player_login)
	# Fetch session_timeout_days from Memora Settings
	settings_service = SettingsService(redis, frappe_client)
	game_settings = await settings_service.get_gamification_settings()
	session_ttl_days = game_settings.session_timeout_days
	max_devices = game_settings.max_devices_per_player

	# Device registration
	user_agent = request.headers.get("User-Agent", "Unknown")
	platform_hint = request.headers.get("X-Platform")

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

	# Fetch wallet XP (should be 0 for new player)
	wallet_service = WalletService(redis, key_prefix=settings.redis_key_prefix, frappe_client=frappe_client)
	wallet = await wallet_service.get_wallet(player_id)

	# Force-kick old WebSocket connections (unlikely for new registration but safe)
	await _force_kick_old_sessions(request, player_id)

	# Create session
	session_service = SessionService(redis, key_prefix=f"{settings.redis_key_prefix}session:")
	family_id = await session_service.create_session(
		player_id,
		plan_id=profile["plan"],
		ttl_days=session_ttl_days,
	)

	# Create tokens (player: mobile claim, no email)
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

	logger.info("player_registered", player_id=player_id)

	return PlayerLoginResponse(
		access_token=access_token,
		refresh_token=refresh_token,
		profile=LoginProfile(
			display_name=profile.get("display_name", ""),
			avatar=profile.get("avatar") or "default_avatar",
			xp=wallet.get("xp", 0),
		),
	)


@router.post("/player/register/resend")
async def player_register_resend(
	request: Request,
	body: RegisterResendRequest,
	redis: RedisClient,
) -> dict:
	"""
	Resend OTP for a pending registration.

	Generates a new OTP and resets the attempt counter.
	Subject to 60-second cooldown between resends.
	"""
	client_ip = _get_client_ip(request)
	otp_service = OTPService(redis)
	await otp_service.resend_registration_otp(body.pending_id, client_ip)
	return {"message": "OTP resent"}


# =============================================================================
# Password reset endpoints (3-step OWASP-compliant flow)
# =============================================================================


@router.post("/player/password-reset/request")
async def password_reset_request(
	request: Request,
	body: PasswordResetRequest,
	redis: RedisClient,
) -> dict:
	"""
	Password reset step 1: request OTP for password reset.

	Anti-enumeration design: ALWAYS returns the same generic message regardless
	of whether the phone number is registered. Rate limits and cooldown run
	in all cases for timing consistency.
	"""
	client_ip = _get_client_ip(request)
	otp_service = OTPService(redis)

	# Check phone existence (needed to decide whether to store OTP)
	phone_exists = False
	try:
		frappe_client = await get_frappe_client()
		result = await frappe_client.call(
			"memora_admin.api.auth.check_phone_exists",
			{"mobile": body.mobile},
		)
		phone_exists = bool(result and result.get("exists"))
	except Exception:
		# Treat Frappe errors as "not found" for anti-enumeration
		pass

	# OTPService handles rate limit + cooldown always, OTP storage only if phone exists
	await otp_service.create_password_reset(body.mobile, client_ip, phone_exists=phone_exists)

	return {"message": "If this number is registered, you will receive an OTP"}


@router.post("/player/password-reset/verify", response_model=PasswordResetVerifyResponse)
async def password_reset_verify(
	body: PasswordResetVerifyRequest,
	redis: RedisClient,
) -> PasswordResetVerifyResponse:
	"""
	Password reset step 2: verify OTP and receive temporary reset token.

	The reset token is cryptographically random, bound to the phone number,
	has a 15-minute TTL, and is single-use (deleted from Redis after validation).
	"""
	otp_service = OTPService(redis)
	reset_token = await otp_service.verify_password_reset_otp(body.mobile, body.otp)
	return PasswordResetVerifyResponse(reset_token=reset_token)


@router.post("/player/password-reset/confirm")
async def password_reset_confirm(
	body: PasswordResetConfirmRequest,
	redis: RedisClient,
) -> dict:
	"""
	Password reset step 3: set new password using temporary reset token.

	Validates the single-use token, resolves the mobile number to the player
	docname, then calls set_player_password which handles both the password
	hash update and session invalidation (RESET-05: forces all devices to re-login).
	"""
	otp_service = OTPService(redis)
	# validate_reset_token is single-use: deletes token from Redis, returns mobile
	mobile = await otp_service.validate_reset_token(body.reset_token)

	# Resolve mobile to player docname via check_phone_exists
	frappe_client = await get_frappe_client()
	phone_check = await frappe_client.call(
		"memora_admin.api.auth.check_phone_exists",
		{"mobile": mobile},
	)
	if not phone_check or not phone_check.get("exists"):
		raise HTTPException(status_code=401, detail="Account not found")

	player_name = phone_check["player_name"]

	# set_player_password handles hash update AND session invalidation (RESET-05)
	await frappe_client.call(
		"memora_admin.api.auth.set_player_password",
		{"player_name": player_name, "new_password": body.new_password},
	)

	return {"message": "Password reset successful. Please log in again."}
