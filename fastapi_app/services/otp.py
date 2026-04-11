"""OTP lifecycle service with pluggable provider and Redis-backed storage.

Handles registration OTP flow (pending → verify → create user)
and password reset OTP flow (reset → verify → temp token → confirm).

All OTP data is stored in Redis with TTL-based expiration.
Rate limiting uses atomic Lua scripts for thread-safety.
"""

import json
import secrets
from typing import Protocol, runtime_checkable

import redis.asyncio as aioredis
import structlog
from fastapi import HTTPException

from fastapi_app.core.redis_keys import (
	pending_reg_key,
	phone_reserved_key,
	ratelimit_otp_cooldown_key,
	ratelimit_otp_ip_key,
	ratelimit_otp_phone_key,
	reset_otp_key,
	reset_token_key,
)

logger = structlog.get_logger()

# Lua script for atomic increment with conditional TTL (same pattern as rate_limit.py)
RATE_LIMIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""


@runtime_checkable
class OTPProvider(Protocol):
	"""Protocol for OTP delivery providers (SMS, WhatsApp, etc.)."""

	async def send_otp(self, mobile: str, otp: str) -> bool: ...


class StaticOTPProvider:
	"""Dev provider -- always uses '1111', logs instead of sending."""

	async def send_otp(self, mobile: str, otp: str) -> bool:
		logger.info("otp_sent_static", mobile_suffix=mobile[-4:], otp=otp)
		return True


class OTPService:
	"""OTP lifecycle management with Redis-backed storage.

	Supports two flows:
	1. Registration: create_pending → verify_registration_otp → user created
	2. Password reset: create_password_reset → verify_password_reset_otp → validate_reset_token

	Rate limiting: 3 OTP requests/phone/10min, 10 OTP requests/IP/10min.
	Cooldown: 60s between resend requests per phone number.
	"""

	# --- Constants ---
	OTP_TTL = 300  # 5 minutes
	RESET_TOKEN_TTL = 900  # 15 minutes
	MAX_ATTEMPTS = 3
	COOLDOWN_TTL = 60  # 60 seconds between resends
	RATE_LIMIT_WINDOW = 600  # 10 minutes
	PHONE_LIMIT = 3  # 3 OTP requests per phone per window
	IP_LIMIT = 10  # 10 OTP requests per IP per window

	def __init__(
		self,
		redis_client: aioredis.Redis,
		provider: OTPProvider | None = None,
	):
		self.redis = redis_client
		self.provider = provider or StaticOTPProvider()
		self._rate_limit_script = None

	async def _get_rate_limit_script(self):
		"""Get or register Lua rate limit script (cached)."""
		if self._rate_limit_script is None:
			self._rate_limit_script = self.redis.register_script(RATE_LIMIT_SCRIPT)
		return self._rate_limit_script

	# --- Rate limiting & cooldown ---

	async def _check_otp_rate_limit(self, mobile: str, ip_address: str) -> None:
		"""Check OTP rate limits for phone and IP.

		Raises HTTPException(429) if either limit is exceeded.
		"""
		script = await self._get_rate_limit_script()

		# Check phone limit
		phone_key = ratelimit_otp_phone_key(mobile)
		phone_count = await script(keys=[phone_key], args=[self.RATE_LIMIT_WINDOW])

		if phone_count > self.PHONE_LIMIT:
			ttl = await self.redis.ttl(phone_key)
			logger.warning("otp_rate_limit_phone", mobile_suffix=mobile[-4:])
			raise HTTPException(
				status_code=429,
				detail="Too many OTP requests for this phone number",
				headers={"Retry-After": str(max(ttl, 1))},
			)

		# Check IP limit
		ip_key = ratelimit_otp_ip_key(ip_address)
		ip_count = await script(keys=[ip_key], args=[self.RATE_LIMIT_WINDOW])

		if ip_count > self.IP_LIMIT:
			ttl = await self.redis.ttl(ip_key)
			logger.warning("otp_rate_limit_ip", ip_address=ip_address)
			raise HTTPException(
				status_code=429,
				detail="Too many OTP requests from this IP address",
				headers={"Retry-After": str(max(ttl, 1))},
			)

	async def _check_cooldown(self, mobile: str) -> None:
		"""Check if resend cooldown is active for this phone number.

		Raises HTTPException(429) if cooldown is still active.
		"""
		cooldown_key = ratelimit_otp_cooldown_key(mobile)
		exists = await self.redis.exists(cooldown_key)

		if exists:
			ttl = await self.redis.ttl(cooldown_key)
			raise HTTPException(
				status_code=429,
				detail="Please wait before requesting another OTP",
				headers={"Retry-After": str(max(ttl, 1))},
			)

	async def _set_cooldown(self, mobile: str) -> None:
		"""Set resend cooldown for this phone number."""
		cooldown_key = ratelimit_otp_cooldown_key(mobile)
		await self.redis.set(cooldown_key, "1", ex=self.COOLDOWN_TTL)

	# --- Registration flow ---

	async def create_pending_registration(
		self,
		mobile: str,
		password: str,
		display_name: str,
		gender: str,
		grade: str,
		plan: str,
		major: str | None,
		avatar: str | None,
		ip_address: str,
		governorate: str | None = None,
	) -> str:
		"""Create a pending registration with OTP verification.

		Steps:
		1. Check rate limits and cooldown
		2. Reserve phone number (atomic SETNX)
		3. Generate OTP and store pending state in Redis
		4. Send OTP via provider

		Returns:
			pending_id: Opaque token to reference this pending registration

		Raises:
			HTTPException(429): Rate limit or cooldown exceeded
			HTTPException(409): Phone number already has a pending registration
		"""
		# Check rate limits and cooldown
		await self._check_otp_rate_limit(mobile, ip_address)
		await self._check_cooldown(mobile)

		# Atomic phone reservation (SETNX)
		reserved_key = phone_reserved_key(mobile)
		was_set = await self.redis.set(reserved_key, "1", ex=self.OTP_TTL, nx=True)

		if not was_set:
			logger.info("pending_registration_exists", mobile_suffix=mobile[-4:])
			raise HTTPException(
				status_code=409,
				detail="Phone number has a pending registration",
			)

		# Generate OTP (static "1111" for dev via StaticOTPProvider)
		otp = "1111"
		pending_id = secrets.token_urlsafe(32)

		# Store pending state
		pending_data = {
			"mobile": mobile,
			"password": password,
			"display_name": display_name,
			"gender": gender,
			"grade": grade,
			"plan": plan,
			"major": major,
			"avatar": avatar,
			"governorate": governorate,
			"otp": otp,
			"attempts": 0,
		}

		pending_key = pending_reg_key(pending_id)
		await self.redis.set(pending_key, json.dumps(pending_data), ex=self.OTP_TTL)

		# Set cooldown and send OTP
		await self._set_cooldown(mobile)
		await self.provider.send_otp(mobile, otp)

		logger.info("pending_registration_created", mobile_suffix=mobile[-4:], pending_id=pending_id[:8])
		return pending_id

	async def verify_registration_otp(self, pending_id: str, otp: str) -> dict:
		"""Verify OTP for a pending registration.

		On success, returns the registration data and cleans up Redis keys.
		On failure, increments attempt counter.

		Returns:
			dict with registration fields (mobile, password, display_name, etc.)

		Raises:
			HTTPException(401): OTP expired, invalid, or too many attempts
		"""
		pending_key = pending_reg_key(pending_id)
		raw = await self.redis.get(pending_key)

		if raw is None:
			raise HTTPException(status_code=401, detail="OTP expired or invalid")

		data = json.loads(raw)

		# Check max attempts
		if data["attempts"] >= self.MAX_ATTEMPTS:
			# Clean up
			await self.redis.delete(pending_key)
			reserved_key = phone_reserved_key(data["mobile"])
			await self.redis.delete(reserved_key)
			logger.warning("otp_max_attempts", mobile_suffix=data["mobile"][-4:])
			raise HTTPException(
				status_code=401,
				detail="Too many attempts. Please request a new OTP.",
			)

		# Verify OTP
		if otp != data["otp"]:
			data["attempts"] += 1
			await self.redis.set(pending_key, json.dumps(data), keepttl=True)
			remaining = self.MAX_ATTEMPTS - data["attempts"]
			logger.info(
				"otp_verification_failed",
				mobile_suffix=data["mobile"][-4:],
				remaining_attempts=remaining,
			)
			raise HTTPException(
				status_code=401,
				detail={"detail": "Invalid OTP", "remaining_attempts": remaining},
			)

		# Success: clean up and return data
		reserved_key = phone_reserved_key(data["mobile"])
		await self.redis.delete(pending_key, reserved_key)

		logger.info("otp_verification_success", mobile_suffix=data["mobile"][-4:])

		# Remove OTP and attempts from returned data
		data.pop("otp", None)
		data.pop("attempts", None)
		return data

	async def resend_registration_otp(self, pending_id: str, ip_address: str) -> None:
		"""Resend OTP for a pending registration.

		Generates a new OTP code and resets the attempt counter.
		Preserves the original TTL of the pending registration.

		Raises:
			HTTPException(401): Registration expired
			HTTPException(429): Rate limit or cooldown exceeded
		"""
		pending_key = pending_reg_key(pending_id)
		raw = await self.redis.get(pending_key)

		if raw is None:
			raise HTTPException(status_code=401, detail="Registration expired")

		data = json.loads(raw)
		mobile = data["mobile"]

		# Check cooldown and rate limit
		await self._check_cooldown(mobile)
		await self._check_otp_rate_limit(mobile, ip_address)

		# Generate new OTP and reset attempts
		new_otp = "1111"
		data["otp"] = new_otp
		data["attempts"] = 0

		# Update with preserved TTL
		await self.redis.set(pending_key, json.dumps(data), keepttl=True)

		# Set new cooldown and send
		await self._set_cooldown(mobile)
		await self.provider.send_otp(mobile, new_otp)

		logger.info("otp_resent", mobile_suffix=mobile[-4:], pending_id=pending_id[:8])

	# --- Password reset flow ---

	async def create_password_reset(self, mobile: str, ip_address: str, *, phone_exists: bool = True) -> None:
		"""Create a password reset OTP.

		Anti-enumeration design: rate limit and cooldown ALWAYS run (timing consistency).
		OTP is only stored and sent when phone_exists=True.
		When phone_exists=False, cooldown is still set but no OTP is generated.

		Args:
			mobile: Phone number
			ip_address: Client IP for rate limiting
			phone_exists: Whether the phone is registered (controls OTP storage/sending)

		Raises:
			HTTPException(429): Rate limit or cooldown exceeded
		"""
		# ALWAYS check rate limit and cooldown (timing consistency for anti-enumeration)
		await self._check_otp_rate_limit(mobile, ip_address)
		await self._check_cooldown(mobile)

		# ALWAYS set cooldown (timing consistency)
		await self._set_cooldown(mobile)

		if phone_exists:
			# Generate and store OTP only for existing phones
			otp = "1111"
			reset_data = {"otp": otp, "attempts": 0}
			reset_key = reset_otp_key(mobile)
			await self.redis.set(reset_key, json.dumps(reset_data), ex=self.OTP_TTL)

			# Send via provider
			await self.provider.send_otp(mobile, otp)
			logger.info("password_reset_otp_sent", mobile_suffix=mobile[-4:])
		else:
			# Phone not found -- no OTP stored, no SMS sent (anti-enumeration)
			logger.info("password_reset_phone_not_found", mobile_suffix=mobile[-4:])

	async def verify_password_reset_otp(self, mobile: str, otp: str) -> str:
		"""Verify OTP for password reset.

		On success, generates a single-use temporary reset token.

		Returns:
			Temporary reset token (secrets.token_urlsafe(32))

		Raises:
			HTTPException(401): OTP expired, invalid, or too many attempts
		"""
		reset_key = reset_otp_key(mobile)
		raw = await self.redis.get(reset_key)

		if raw is None:
			raise HTTPException(status_code=401, detail="OTP expired or invalid")

		data = json.loads(raw)

		# Check max attempts
		if data["attempts"] >= self.MAX_ATTEMPTS:
			await self.redis.delete(reset_key)
			logger.warning("reset_otp_max_attempts", mobile_suffix=mobile[-4:])
			raise HTTPException(
				status_code=401,
				detail="Too many attempts. Please request a new OTP.",
			)

		# Verify OTP
		if otp != data["otp"]:
			data["attempts"] += 1
			await self.redis.set(reset_key, json.dumps(data), keepttl=True)
			remaining = self.MAX_ATTEMPTS - data["attempts"]
			logger.info(
				"reset_otp_failed",
				mobile_suffix=mobile[-4:],
				remaining_attempts=remaining,
			)
			raise HTTPException(
				status_code=401,
				detail={"detail": "Invalid OTP", "remaining_attempts": remaining},
			)

		# Success: delete reset key and generate temp token
		await self.redis.delete(reset_key)

		token = secrets.token_urlsafe(32)
		token_key = reset_token_key(token)
		await self.redis.set(token_key, mobile, ex=self.RESET_TOKEN_TTL)

		logger.info("reset_otp_verified", mobile_suffix=mobile[-4:])
		return token

	async def validate_reset_token(self, token: str) -> str:
		"""Validate and consume a single-use reset token.

		Returns the mobile number associated with the token.
		Token is deleted after use (single-use).

		Returns:
			Mobile number string

		Raises:
			HTTPException(401): Token expired or invalid
		"""
		token_key = reset_token_key(token)
		mobile = await self.redis.get(token_key)

		if mobile is None:
			raise HTTPException(status_code=401, detail="Reset token expired or invalid")

		# Single-use: delete immediately
		await self.redis.delete(token_key)

		logger.info("reset_token_validated", mobile_suffix=mobile[-4:])
		return mobile
