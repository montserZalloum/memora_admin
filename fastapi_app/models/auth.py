"""Authentication-related Pydantic models."""

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    """Request body for user login."""

    email: EmailStr
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
    For refresh tokens: email, role, tz, name are None.
    """

    sub: str  # User ID
    fid: str  # Family ID
    type: str  # "access" or "refresh"
    exp: int  # Expiration timestamp
    jti: str  # JWT ID (unique identifier)

    # Access token specific fields (None for refresh tokens)
    email: str | None = None
    role: str | None = None
    tz: str | None = None  # Timezone
    name: str | None = None  # Display name

    # Optional fields
    iat: int | None = None  # Issued at timestamp


class FrappeUser(BaseModel):
    """User data retrieved from Frappe API."""

    user_id: str
    email: str
    full_name: str
    user_type: str
    time_zone: str | None = None
