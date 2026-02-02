"""Shared dependencies for API endpoints."""

from typing import Annotated

import jwt
import redis.asyncio as redis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer

from fastapi_app.core.config import Settings, get_settings
from fastapi_app.core.security import decode_token
from fastapi_app.models.auth import TokenPayload

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
