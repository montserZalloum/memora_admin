"""Authentication-related Pydantic models."""

from pydantic import BaseModel, Field


# --- Token models (used by all auth flows) ---


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

	sub: str  # User ID (PLAYER-##### for players, email for admins)
	fid: str  # Family ID
	type: str  # "access" or "refresh"
	exp: int  # Expiration timestamp
	jti: str  # JWT ID (unique identifier)

	# Access token specific fields (None for refresh tokens)
	email: str | None = None
	mobile: str | None = None
	plan: str | None = None  # Player's plan document name (e.g., 'PLAN-00001')
	name: str | None = None  # Display name

	# Optional fields
	iat: int | None = None  # Issued at timestamp
	role: str | None = None  # User role (e.g., "System Manager" for admins)


class FrappeUser(BaseModel):
	"""User data retrieved from Frappe API."""

	user_id: str
	email: str
	full_name: str
	user_type: str
	time_zone: str | None = None


# --- Player login ---


class PlayerLoginRequest(BaseModel):
	"""Request body for player login (mobile + password)."""

	mobile: str
	password: str


class LoginProfile(BaseModel):
	"""Player profile data returned with login response."""

	display_name: str
	avatar: str
	xp: int


class PlayerLoginResponse(BaseModel):
	"""Login response with tokens and profile data for players."""

	access_token: str
	refresh_token: str
	token_type: str = "bearer"
	profile: LoginProfile


# --- Admin login ---


class AdminLoginRequest(BaseModel):
	"""Request body for admin login (email + password)."""

	email: str
	password: str


# --- Registration ---


class RegisterRequest(BaseModel):
	"""Request body for player registration (step 1: submit details)."""

	mobile: str
	password: str = Field(..., min_length=8)
	display_name: str
	gender: str
	grade: str
	plan: str
	major: str | None = None


class RegisterResponse(BaseModel):
	"""Response for registration step 1 (OTP sent)."""

	pending_id: str
	message: str


class RegisterVerifyRequest(BaseModel):
	"""Request body for registration step 2 (verify OTP)."""

	pending_id: str
	otp: str


class RegisterResendRequest(BaseModel):
	"""Request body for resending registration OTP."""

	pending_id: str


# --- Password reset ---


class PasswordResetRequest(BaseModel):
	"""Request body for password reset step 1 (send OTP to mobile)."""

	mobile: str


class PasswordResetVerifyRequest(BaseModel):
	"""Request body for password reset step 2 (verify OTP)."""

	mobile: str
	otp: str


class PasswordResetVerifyResponse(BaseModel):
	"""Response for password reset step 2 (temporary reset token)."""

	reset_token: str


class PasswordResetConfirmRequest(BaseModel):
	"""Request body for password reset step 3 (set new password)."""

	reset_token: str
	new_password: str = Field(..., min_length=8)
