"""Authentication-related Pydantic models."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """Request body for user login.

    Accepts either email or mobile number as identifier.
    Per CONTEXT.md: Auto-detect type - email has @, mobile doesn't.
    """

    identifier: str
    password: str


class TokenResponse(BaseModel):
    """Response body containing access and refresh tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Request body for token refresh."""

    refresh_token: str


class TokenPayload(BaseModel):
    """Decoded JWT token payload.

    For access tokens: all fields populated.
    For refresh tokens: email, plan, name are None.
    """

    sub: str  # User ID
    fid: str  # Family ID
    type: str  # "access" or "refresh"
    exp: int  # Expiration timestamp
    jti: str  # JWT ID (unique identifier)

    # Access token specific fields (None for refresh tokens)
    email: str | None = None
    plan: str | None = None  # Player's plan document name (e.g., 'PLAN-00001')
    name: str | None = None  # Display name

    # Optional fields
    iat: int | None = None  # Issued at timestamp


class LoginProfile(BaseModel):
    """Player profile data returned with login response."""

    display_name: str
    avatar: str
    gender: str | None = None  # Optional - may not be set
    xp: int


class EnrichedTokenResponse(BaseModel):
    """Login response with tokens and profile data."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    profile: LoginProfile


class FrappeUser(BaseModel):
    """User data retrieved from Frappe API."""

    user_id: str
    email: str
    full_name: str
    user_type: str
    time_zone: str | None = None
