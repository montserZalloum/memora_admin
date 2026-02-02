"""JWT token creation and decoding utilities."""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt

from fastapi_app.core.config import get_settings


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    timezone_str: str,
    display_name: str,
    family_id: str,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a JWT access token with rich user payload.

    Args:
        user_id: Unique user identifier (goes in 'sub' claim)
        email: User email address
        role: User role (e.g., 'player', 'parent', 'admin')
        timezone_str: User's timezone (e.g., 'Asia/Riyadh')
        display_name: User's display name
        family_id: Family identifier for session management
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string
    """
    settings = get_settings()

    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)

    now = datetime.now(tz=timezone.utc)
    expire = now + expires_delta

    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "tz": timezone_str,
        "name": display_name,
        "fid": family_id,
        "type": "access",
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    user_id: str,
    family_id: str,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a JWT refresh token with minimal payload.

    Args:
        user_id: Unique user identifier (goes in 'sub' claim)
        family_id: Family identifier for session management
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string
    """
    settings = get_settings()

    if expires_delta is None:
        expires_delta = timedelta(days=settings.jwt_refresh_token_expire_days)

    now = datetime.now(tz=timezone.utc)
    expire = now + expires_delta

    payload: dict[str, Any] = {
        "sub": user_id,
        "fid": family_id,
        "type": "refresh",
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(
    token: str,
    verify_type: str | None = None,
) -> dict[str, Any]:
    """
    Decode and validate a JWT token.

    Args:
        token: The JWT token string to decode
        verify_type: Optional token type to verify ('access' or 'refresh')

    Returns:
        Decoded payload as dictionary

    Raises:
        jwt.ExpiredSignatureError: If token has expired
        jwt.InvalidTokenError: If token is invalid or type mismatch
    """
    settings = get_settings()

    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "exp", "type", "fid"]},
    )

    if verify_type is not None and payload.get("type") != verify_type:
        raise jwt.InvalidTokenError(
            f"Token type mismatch: expected '{verify_type}', got '{payload.get('type')}'"
        )

    return payload
