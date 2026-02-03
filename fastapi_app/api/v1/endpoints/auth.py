"""Authentication endpoints."""

from datetime import timedelta

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from fastapi_app.api.deps import RedisClient, SettingsDep, get_frappe_client
from fastapi_app.core.security import create_access_token, create_refresh_token, decode_token
from fastapi_app.models.auth import LoginRequest, RefreshRequest, TokenResponse
from fastapi_app.services.device import DeviceService
from fastapi_app.services.frappe import FrappeAuthService
from fastapi_app.services.rate_limit import RateLimiter
from fastapi_app.services.session import SessionService
from fastapi_app.services.settings import SettingsService

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For from nginx."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First IP in chain is the original client
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    credentials: LoginRequest,
    redis: RedisClient,
    settings: SettingsDep,
) -> TokenResponse | JSONResponse:
    """
    Login with Frappe credentials, receive JWT tokens.

    Requires X-Device-ID header for device registration.
    Rate limited: 10 attempts/min per IP, 5 attempts/min per account.
    New login invalidates any previous session.
    Device registration enforces max_devices_per_player limit.
    """
    # Require X-Device-ID header
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

    # Check rate limits
    rate_limiter = RateLimiter(redis)
    allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(
        ip_address=client_ip,
        target_account=credentials.email,
    )

    if not allowed:
        # Per CONTEXT.md: include Retry-After header and seconds in body
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "Too many login attempts",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    # Verify credentials with Frappe
    frappe_service = FrappeAuthService(settings.frappe_url)
    user = await frappe_service.verify_credentials(
        credentials.email,
        credentials.password,
    )

    if not user:
        # Per CONTEXT.md: generic error, don't reveal if email exists
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Get device limit from settings
    frappe_client = await get_frappe_client()
    settings_service = SettingsService(redis, frappe_client)
    game_settings = await settings_service.get_gamification_settings()
    max_devices = game_settings.max_devices_per_player

    # Register device (atomic with limit check)
    device_service = DeviceService(redis, key_prefix=settings.redis_key_prefix)
    device_result = await device_service.register_device(
        user_id=user.user_id,
        device_id=device_id,
        user_agent=user_agent,
        max_devices=max_devices,
        platform_hint=platform_hint,
    )

    if not device_result.success:
        # Per CONTEXT.md: HTTP 429 with specific message
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "code": "DEVICE_LIMIT_EXCEEDED",
                "message": f"Device limit reached ({device_result.current_count}/{device_result.max_count}). Contact support to manage your devices.",
            },
        )

    # Create new session (invalidates any existing session)
    session_service = SessionService(redis, key_prefix=f"{settings.redis_key_prefix}session:")
    family_id = await session_service.create_session(
        user.user_id,
        ttl_days=settings.jwt_refresh_token_expire_days,
    )

    # Map Frappe user_type to game role
    role = "admin" if user.user_type == "System User" else "player"

    # Create tokens
    access_token = create_access_token(
        user_id=user.user_id,
        email=user.email,
        role=role,
        timezone_str=user.time_zone or "UTC",
        display_name=user.full_name,
        family_id=family_id,
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )

    refresh_token = create_refresh_token(
        user_id=user.user_id,
        family_id=family_id,
        expires_delta=timedelta(days=settings.jwt_refresh_token_expire_days),
    )

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

    Per CONTEXT.md:
    - Validates session is still active (not invalidated by new login)
    - Returns same refresh token (not rotated)
    - Refresh token is reusable
    """
    try:
        # Decode refresh token (validates signature, expiry, type)
        payload = decode_token(body.refresh_token, verify_type="refresh")

        user_id = payload["sub"]
        family_id = payload["fid"]

        # Validate session is still active
        session_service = SessionService(redis, key_prefix=f"{settings.redis_key_prefix}session:")
        is_valid = await session_service.validate_session(user_id, family_id)

        if not is_valid:
            # Session invalidated by new login on another device
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        # Create new access token with SAME family_id
        # Note: Refresh token has minimal claims, so we use what we have
        # For full user data, we'd need to cache it or fetch from Frappe
        # Using placeholder values that will be refreshed on next full login
        access_token = create_access_token(
            user_id=user_id,
            email=payload.get("email", ""),
            role=payload.get("role", "player"),
            timezone_str=payload.get("tz", "UTC"),
            display_name=payload.get("name", ""),
            family_id=family_id,
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
