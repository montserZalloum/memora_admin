"""JWT token creation and decoding utilities."""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt

from fastapi_app.core.config import get_settings


def create_access_token(
    user_id: str,
    plan_id: str,
    display_name: str,
    family_id: str,
    *,
    email: str | None = None,
    mobile: str | None = None,
    role: str | None = None,
    season_id: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a JWT access token with user payload.

    Args:
        user_id: Unique user identifier (goes in 'sub' claim).
            For players: PLAYER-##### docname. For admins: email address.
        plan_id: Player's plan document name (e.g., 'PLAN-00001')
        display_name: User's display name
        family_id: Family identifier for session management
        email: User email address (admin tokens)
        mobile: User mobile number (player tokens)
        role: Optional user role (e.g., "System Manager" for admin users)
        season_id: Player's season ID (e.g., 'SEAS-00027'). None for admins.
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string

    Note:
        Timezone is hardcoded to Asia/Amman for all players.
        Role is included only for admin users to keep player tokens lean.
        Email and mobile are included only when truthy (not in payload if None/empty).
    """
    settings = get_settings()

    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)

    now = datetime.now(tz=timezone.utc)
    expire = now + expires_delta

    payload: dict[str, Any] = {
        "sub": user_id,
        "plan": plan_id,
        "name": display_name,
        "fid": family_id,
        "type": "access",
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }

    # Include identity claims only when truthy (keeps tokens lean)
    if email:
        payload["email"] = email
    if mobile:
        payload["mobile"] = mobile

    # Include role only for admin users (keeps player tokens lean)
    if role:
        payload["role"] = role

    # Include season for players (Gate 1 enforcement)
    if season_id:
        payload["season"] = season_id

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
