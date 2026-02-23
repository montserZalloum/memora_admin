# Copyright (c) 2026, corex and contributors
"""Tests for OTPService — OTP verification with rate limiting and cooldown."""

import json
import pytest
import redis.asyncio as redis
from fastapi import HTTPException

from fastapi_app.core.redis_keys import (
	pending_reg_key,
	phone_reserved_key,
	ratelimit_otp_cooldown_key,
	reset_otp_key,
	reset_token_key,
)
from fastapi_app.services.otp import OTPService, StaticOTPProvider

# Test constants
TEST_MOBILE = "201000000000"
TEST_IP = "192.168.1.1"
TEST_IP_2 = "192.168.1.2"
TEST_PASSWORD = "hashed_password_123"
TEST_PLAN = "PLAN-001"


@pytest.fixture
async def otp_service(redis_client: redis.Redis, test_prefix: str) -> OTPService:
	"""Create OTPService with test prefix and StaticOTPProvider."""
	return OTPService(
		redis_client=redis_client,
		provider=StaticOTPProvider(),
	)


class TestRegistrationFlow:
	"""Test OTP registration flow."""

	async def test_tc_otp_01_create_pending_returns_pending_id(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-01: Create pending registration returns pending_id with Redis state."""
		pending_id = await otp_service.create_pending_registration(
			mobile=TEST_MOBILE,
			password=TEST_PASSWORD,
			display_name="Test Player",
			gender="M",
			grade="Grade 10",
			plan=TEST_PLAN,
			major=None,
			ip_address=TEST_IP,
		)

		# Verify pending_id is returned
		assert pending_id, "pending_id should not be empty"
		assert len(pending_id) > 20, "pending_id should be long enough (token_urlsafe)"

		# Verify Redis state
		pk = pending_reg_key(pending_id)
		raw = await redis_client.get(pk)
		assert raw is not None, "Pending key should exist in Redis"

		data = json.loads(raw)
		assert data["mobile"] == TEST_MOBILE, "Mobile should match"
		assert data["otp"] == "1111", "OTP should be 1111 (StaticOTPProvider)"
		assert data["attempts"] == 0, "Attempts should start at 0"

		# Verify phone_reserved key exists
		rk = phone_reserved_key(TEST_MOBILE)
		reserved = await redis_client.get(rk)
		assert reserved is not None, "Phone should be reserved"

		# Verify TTL
		ttl = await redis_client.ttl(pk)
		assert ttl > 0 and ttl <= 300, f"TTL should be <= 300, got {ttl}"

	async def test_tc_otp_02_verify_correct_otp_returns_data_and_cleans(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-02: Verify correct OTP returns registration data and cleans up."""
		# Create pending
		pending_id = await otp_service.create_pending_registration(
			mobile=TEST_MOBILE,
			password=TEST_PASSWORD,
			display_name="Test Player",
			gender="M",
			grade="Grade 10",
			plan=TEST_PLAN,
			major="Science",
			ip_address=TEST_IP,
		)

		# Verify correct OTP
		data = await otp_service.verify_registration_otp(pending_id, "1111")

		# Verify returned data (without otp and attempts)
		assert data["mobile"] == TEST_MOBILE, "Mobile should match"
		assert data["password"] == TEST_PASSWORD, "Password should match"
		assert data["display_name"] == "Test Player", "Display name should match"
		assert "otp" not in data, "OTP should not be returned"
		assert "attempts" not in data, "Attempts should not be returned"

		# Verify pending key is deleted
		exists = await redis_client.exists(pending_reg_key(pending_id))
		assert exists == 0, "Pending key should be deleted"

		# Verify phone_reserved is deleted
		exists = await redis_client.exists(phone_reserved_key(TEST_MOBILE))
		assert exists == 0, "Phone reservation should be deleted"

	async def test_tc_otp_03_verify_wrong_otp_increments_attempts(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-03: Verify wrong OTP increments attempts."""
		pending_id = await otp_service.create_pending_registration(
			mobile=TEST_MOBILE,
			password=TEST_PASSWORD,
			display_name="Test Player",
			gender="M",
			grade="Grade 10",
			plan=TEST_PLAN,
			major=None,
			ip_address=TEST_IP,
		)

		# Submit wrong OTP
		with pytest.raises(HTTPException) as exc_info:
			await otp_service.verify_registration_otp(pending_id, "9999")

		assert exc_info.value.status_code == 401, "Should return 401"

		# Verify attempts incremented
		raw = await redis_client.get(pending_reg_key(pending_id))
		data = json.loads(raw)
		assert data["attempts"] == 1, "Attempts should be 1"

	async def test_tc_otp_04_max_attempts_exhausted_deletes_pending(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-04: Max attempts exhausted deletes pending."""
		pending_id = await otp_service.create_pending_registration(
			mobile=TEST_MOBILE,
			password=TEST_PASSWORD,
			display_name="Test Player",
			gender="M",
			grade="Grade 10",
			plan=TEST_PLAN,
			major=None,
			ip_address=TEST_IP,
		)

		# Submit 3 wrong OTPs
		for _ in range(3):
			with pytest.raises(HTTPException):
				await otp_service.verify_registration_otp(pending_id, "9999")

		# 4th attempt should fail with "Too many attempts"
		with pytest.raises(HTTPException) as exc_info:
			await otp_service.verify_registration_otp(pending_id, "9999")

		assert exc_info.value.status_code == 401, "Should return 401"
		assert "Too many attempts" in str(exc_info.value.detail), "Should mention too many attempts"

		# Verify pending key is deleted
		exists = await redis_client.exists(pending_reg_key(pending_id))
		assert exists == 0, "Pending key should be deleted after max attempts"

		# Verify phone_reserved is deleted
		exists = await redis_client.exists(phone_reserved_key(TEST_MOBILE))
		assert exists == 0, "Phone reservation should be deleted"

	async def test_tc_otp_05_resend_cooldown_blocks_rapid_resend(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-05: Resend cooldown blocks rapid resend."""
		pending_id = await otp_service.create_pending_registration(
			mobile=TEST_MOBILE,
			password=TEST_PASSWORD,
			display_name="Test Player",
			gender="M",
			grade="Grade 10",
			plan=TEST_PLAN,
			major=None,
			ip_address=TEST_IP,
		)

		# Immediately try to resend (cooldown is active from create)
		with pytest.raises(HTTPException) as exc_info:
			await otp_service.resend_registration_otp(pending_id, TEST_IP)

		assert exc_info.value.status_code == 429, "Should return 429 for rate limit"
		assert "Please wait" in str(exc_info.value.detail), "Should mention wait"


class TestOTPRateLimits:
	"""Test OTP rate limiting."""

	async def test_tc_otp_06_phone_rate_limit_blocks_excess(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-06: Phone rate limit blocks after PHONE_LIMIT=3 requests."""
		# Make 3 successful requests (need to clear phone_reserved between each)
		for i in range(3):
			try:
				await otp_service.create_pending_registration(
					mobile=TEST_MOBILE,
					password=TEST_PASSWORD,
					display_name=f"Test Player {i}",
					gender="M",
					grade="Grade 10",
					plan=TEST_PLAN,
					major=None,
					ip_address=TEST_IP,
				)
			except HTTPException:
				pass

			# Clear phone_reserved to allow another pending
			await redis_client.delete(phone_reserved_key(TEST_MOBILE))

		# 4th request should be blocked by phone rate limit
		with pytest.raises(HTTPException) as exc_info:
			await otp_service.create_pending_registration(
				mobile=TEST_MOBILE,
				password=TEST_PASSWORD,
				display_name="Test Player 4",
				gender="M",
				grade="Grade 10",
				plan=TEST_PLAN,
				major=None,
				ip_address=TEST_IP,
			)

		assert exc_info.value.status_code == 429, "Should return 429 for rate limit"
		assert "Too many OTP requests for this phone number" in str(
			exc_info.value.detail
		), "Should mention phone rate limit"

	async def test_tc_otp_07_ip_rate_limit_blocks_excess(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-07: IP rate limit blocks after IP_LIMIT=10 requests."""
		# Make 10 successful requests from different phones (same IP)
		for i in range(10):
			try:
				mobile = f"2010000000{i:02d}"
				await otp_service.create_pending_registration(
					mobile=mobile,
					password=TEST_PASSWORD,
					display_name=f"Test Player {i}",
					gender="M",
					grade="Grade 10",
					plan=TEST_PLAN,
					major=None,
					ip_address=TEST_IP,
				)
			except HTTPException:
				pass

			# Clear phone_reserved for this phone
			await redis_client.delete(phone_reserved_key(f"2010000000{i:02d}"))

		# 11th request should be blocked by IP rate limit
		with pytest.raises(HTTPException) as exc_info:
			await otp_service.create_pending_registration(
				mobile="20100000010",
				password=TEST_PASSWORD,
				display_name="Test Player 11",
				gender="M",
				grade="Grade 10",
				plan=TEST_PLAN,
				major=None,
				ip_address=TEST_IP,
			)

		assert exc_info.value.status_code == 429, "Should return 429 for rate limit"
		assert "Too many OTP requests from this IP address" in str(
			exc_info.value.detail
		), "Should mention IP rate limit"


class TestPasswordReset:
	"""Test password reset OTP flow."""

	async def test_tc_otp_08_create_password_reset_stores_otp(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-08: Create password reset stores OTP in Redis."""
		await otp_service.create_password_reset(
			mobile=TEST_MOBILE, ip_address=TEST_IP, phone_exists=True
		)

		# Verify reset state is stored
		raw = await redis_client.get(reset_otp_key(TEST_MOBILE))
		assert raw is not None, "Reset key should exist"

		data = json.loads(raw)
		assert data["otp"] == "1111", "OTP should be 1111"
		assert data["attempts"] == 0, "Attempts should start at 0"

	async def test_tc_otp_09_anti_enumeration_skips_when_phone_not_exists(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-09: Anti-enumeration silently skips when phone_exists=False."""
		await otp_service.create_password_reset(
			mobile="9999999999", ip_address=TEST_IP, phone_exists=False
		)

		# Verify no reset state is stored
		exists = await redis_client.exists(reset_otp_key("9999999999"))
		assert exists == 0, "Reset key should NOT exist when phone_exists=False"

		# Verify cooldown IS still set (timing consistency)
		exists = await redis_client.exists(ratelimit_otp_cooldown_key("9999999999"))
		assert exists == 1, "Cooldown should be set even when phone_exists=False"

	async def test_tc_otp_10_verify_password_reset_otp_returns_token_with_ttl(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-10: Verify password reset OTP returns single-use token with 900s TTL."""
		await otp_service.create_password_reset(
			mobile=TEST_MOBILE, ip_address=TEST_IP, phone_exists=True
		)

		# Verify correct OTP
		token = await otp_service.verify_password_reset_otp(TEST_MOBILE, "1111")

		# Verify token is returned
		assert token, "Token should not be empty"
		assert len(token) > 20, "Token should be long enough"

		# Verify token is stored with correct TTL
		ttl = await redis_client.ttl(reset_token_key(token))
		assert ttl > 0 and ttl <= 900, f"TTL should be <= 900, got {ttl}"

		# Verify reset state is deleted
		exists = await redis_client.exists(reset_otp_key(TEST_MOBILE))
		assert exists == 0, "Reset key should be deleted after verification"

	async def test_tc_otp_11_validate_reset_token_consumed_on_first_use(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-11: Validate reset token consumed on first use, second call raises 401."""
		await otp_service.create_password_reset(
			mobile=TEST_MOBILE, ip_address=TEST_IP, phone_exists=True
		)

		token = await otp_service.verify_password_reset_otp(TEST_MOBILE, "1111")

		# First validation succeeds
		mobile = await otp_service.validate_reset_token(token)
		assert mobile == TEST_MOBILE, "Should return correct mobile"

		# Second validation fails (token consumed)
		with pytest.raises(HTTPException) as exc_info:
			await otp_service.validate_reset_token(token)

		assert exc_info.value.status_code == 401, "Should return 401"
		assert "expired or invalid" in str(exc_info.value.detail), "Should mention expired"

	async def test_tc_otp_12_expired_missing_pending_raises_401(
		self, otp_service: OTPService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-OTP-12: Expired/missing pending raises HTTPException 401."""
		# Try to verify a non-existent pending
		with pytest.raises(HTTPException) as exc_info:
			await otp_service.verify_registration_otp("nonexistent-pending-id", "1111")

		assert exc_info.value.status_code == 401, "Should return 401"
		assert "expired or invalid" in str(exc_info.value.detail), "Should mention expired"
