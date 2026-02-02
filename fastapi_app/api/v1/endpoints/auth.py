"""Authentication endpoints."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from fastapi_app.api.deps import RedisClient, SettingsDep
from fastapi_app.core.security import create_access_token, create_refresh_token
from fastapi_app.models.auth import LoginRequest, TokenResponse
from fastapi_app.services.frappe import FrappeAuthService
from fastapi_app.services.rate_limit import RateLimiter
from fastapi_app.services.session import SessionService

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

    Rate limited: 10 attempts/min per IP, 5 attempts/min per account.
    New login invalidates any previous session.
    """
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
