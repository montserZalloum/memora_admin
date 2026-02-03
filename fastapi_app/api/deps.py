"""Shared dependencies for API endpoints."""

from typing import Annotated

import jwt
import redis.asyncio as redis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer

from fastapi_app.core.config import Settings, get_settings
from fastapi_app.core.security import decode_token
from fastapi_app.models.access import ContentAccessRequest, SeasonMeta
from fastapi_app.models.auth import TokenPayload
from fastapi_app.services.access import AccessService
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.hierarchy import HierarchyService
from fastapi_app.services.progress import ProgressService
from fastapi_app.services.season import SeasonService
from fastapi_app.services.settings import SettingsService
from fastapi_app.services.device import DeviceService
from fastapi_app.services.wallet import WalletService

# Common dependencies
SettingsDep = Annotated[Settings, Depends(get_settings)]

# HTTP Bearer security scheme
security = HTTPBearer()


async def get_redis(request: Request) -> redis.Redis:
    """Get Redis client from connection pool stored in app state."""
    return redis.Redis(connection_pool=request.app.state.redis_pool)


# Type alias for dependency injection
RedisClient = Annotated[redis.Redis, Depends(get_redis)]


async def get_current_user(
    credentials: Annotated[str, Depends(security)],
) -> TokenPayload:
    """
    Stateless JWT verification - no database lookup per CONTEXT.md.

    Checks:
    1. Token signature is valid (HS256)
    2. Token is not expired
    3. Token type is "access"
    4. Required claims present (sub, exp, type, fid)

    Returns TokenPayload with user claims.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # HTTPBearer returns HTTPAuthorizationCredentials with .credentials attribute
    token = credentials.credentials

    try:
        payload = decode_token(token, verify_type="access")
        return TokenPayload(**payload)

    except jwt.ExpiredSignatureError:
        raise credentials_exception
    except jwt.InvalidTokenError:
        raise credentials_exception
    except Exception:
        # Catch-all for any validation errors (e.g., missing fields in TokenPayload)
        raise credentials_exception


# Type alias for protected endpoints
CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]


# --- Service Dependencies ---


async def get_season_service(request: Request) -> SeasonService:
    """Get SeasonService with Redis from app state."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    return SeasonService(redis_client)


SeasonServiceDep = Annotated[SeasonService, Depends(get_season_service)]


async def get_access_service(request: Request) -> AccessService:
    """Get AccessService with Redis from app state."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    return AccessService(redis_client)


AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]


async def get_progress_service(request: Request) -> ProgressService:
    """Get ProgressService with Redis from app state."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    return ProgressService(redis_client)


ProgressServiceDep = Annotated[ProgressService, Depends(get_progress_service)]


async def get_wallet_service(request: Request) -> WalletService:
    """Get WalletService with Redis from app state."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    return WalletService(redis_client)


WalletServiceDep = Annotated[WalletService, Depends(get_wallet_service)]


async def get_device_service(request: Request) -> DeviceService:
    """Get DeviceService with Redis from app state."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    return DeviceService(redis_client)


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]


# Singleton for FrappeClient
_frappe_client: FrappeClient | None = None


async def get_frappe_client() -> FrappeClient:
    """Get singleton FrappeClient instance."""
    global _frappe_client
    if _frappe_client is None:
        _frappe_client = FrappeClient()
    return _frappe_client


async def get_settings_service(request: Request) -> SettingsService:
    """Get SettingsService with Redis and FrappeClient."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    frappe_client = await get_frappe_client()
    return SettingsService(redis_client, frappe_client)


SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]


async def get_hierarchy_service(request: Request) -> HierarchyService:
    """Get HierarchyService with Redis and FrappeClient."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    frappe_client = await get_frappe_client()
    return HierarchyService(redis_client, frappe_client)


HierarchyServiceDep = Annotated[HierarchyService, Depends(get_hierarchy_service)]


# --- Double-Gate Dependencies ---


async def require_season_access(
    season_id: str,
    season_service: SeasonServiceDep,
) -> SeasonMeta:
    """
    Gate 1: Validate season is active and not expired.

    Raises:
        HTTPException 403 if season fails validation

    Returns:
        SeasonMeta for the validated season
    """
    season = await season_service.get_season_meta(season_id)

    if not season:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SEASON_NOT_FOUND", "message": "Season not available"},
        )

    if not season.is_published:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SEASON_INACTIVE", "message": "Season is not active"},
        )

    if season.is_expired:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SEASON_EXPIRED", "message": "Season has ended"},
        )

    return season


async def require_content_access(
    content: ContentAccessRequest,
    user: CurrentUser,
    access_service: AccessServiceDep,
) -> bool:
    """
    Gate 2: Validate player has access to content.

    Per CONTEXT.md:
    - Free content (is_free=true) bypasses this check entirely
    - Grants are additive (direct OR plan membership)

    Raises:
        HTTPException 403 if player lacks access

    Returns:
        True if access granted
    """
    # Check free content FIRST (per RESEARCH.md pitfall #3)
    if content.is_free:
        return True

    has_access = await access_service.check_access(
        player_id=user.sub,
        content_key=content.content_key,
    )

    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NO_ACCESS", "message": "Content access required"},
        )

    return True


async def require_double_gate(
    season_id: str,
    content: ContentAccessRequest,
    user: CurrentUser,
    season_service: SeasonServiceDep,
    access_service: AccessServiceDep,
) -> tuple[SeasonMeta, bool]:
    """
    Combined Double-Gate validation.

    1. Gate 1: Validate season
    2. Gate 2: Validate player access (unless content is free)

    Returns:
        Tuple of (SeasonMeta, access_granted: bool)
    """
    # Gate 1
    season = await require_season_access(season_id, season_service)

    # Gate 2
    access_granted = await require_content_access(content, user, access_service)

    return (season, access_granted)


# Type aliases for dependency injection
RequireSeasonAccess = Annotated[SeasonMeta, Depends(require_season_access)]
RequireContentAccess = Annotated[bool, Depends(require_content_access)]
