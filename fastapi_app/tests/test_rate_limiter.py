# Copyright (c) 2026, corex and contributors
"""Tests for RateLimiter — dual-key sliding window rate limiting."""

import pytest
import redis.asyncio as redis

from fastapi_app.services.rate_limit import RateLimiter

# Test constants
TEST_IP = "192.168.1.1"
TEST_ACCOUNT = "test@example.com"


@pytest.fixture
async def rate_limiter(redis_client: redis.Redis, test_prefix: str) -> RateLimiter:
	"""Create RateLimiter with test prefix for isolation."""
	return RateLimiter(redis_client)


class TestRateLimiter:
	"""Test rate limiting with dual keys (IP and account)."""

	async def test_tc_rl_01_first_request_allowed(self, rate_limiter: RateLimiter):
		"""TC-RL-01: First request allowed returns (True, 0, '')."""
		allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(TEST_IP, TEST_ACCOUNT)

		assert allowed is True, "First request should be allowed"
		assert retry_after == 0, "Should have no retry_after on first request"
		assert limit_type == "", "limit_type should be empty on allowed request"

	async def test_tc_rl_02_ip_limit_exceeded(self, rate_limiter: RateLimiter):
		"""TC-RL-02: IP limit exceeded after 10 requests returns (False, retry_after, 'ip')."""
		# Make 10 allowed requests
		for i in range(10):
			allowed, _, _ = await rate_limiter.check_rate_limit(TEST_IP, f"account{i}@example.com")
			assert allowed is True, f"Request {i+1} should be allowed"

		# 11th request should be blocked
		allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(
			TEST_IP, "new-account@example.com"
		)

		assert allowed is False, "11th request should be blocked"
		assert retry_after > 0, "Should have retry_after value"
		assert limit_type == "ip", "Should be IP limit that triggered"

	async def test_tc_rl_03_account_limit_exceeded(self, rate_limiter: RateLimiter):
		"""TC-RL-03: Account limit exceeded after 5 requests returns (False, retry_after, 'account')."""
		# Make 5 allowed requests for same account
		for i in range(5):
			allowed, _, _ = await rate_limiter.check_rate_limit(f"192.168.1.{i}", TEST_ACCOUNT)
			assert allowed is True, f"Request {i+1} for account should be allowed"

		# 6th request should be blocked (account limit)
		allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(
			"192.168.1.100",
			TEST_ACCOUNT,
		)

		assert allowed is False, "6th request for same account should be blocked"
		assert retry_after > 0, "Should have retry_after value"
		assert limit_type == "account", "Should be account limit that triggered"

	async def test_tc_rl_04_get_remaining_returns_counts(self, rate_limiter: RateLimiter):
		"""TC-RL-04: Get remaining returns correct ip/account remaining counts."""
		test_ip = "192.168.2.50"
		test_account = "test.account@example.com"

		# Make 3 requests from test_ip (same account)
		for _ in range(3):
			await rate_limiter.check_rate_limit(test_ip, test_account)

		# Get remaining
		ip_remaining, account_remaining = await rate_limiter.get_remaining(test_ip, test_account)

		# IP: 10 - 3 = 7
		assert ip_remaining == 7, f"IP remaining should be 7, got {ip_remaining}"

		# Account: 5 - 3 = 2
		assert account_remaining == 2, f"Account remaining should be 2, got {account_remaining}"

	async def test_tc_rl_05_account_lowercase_normalization(self, rate_limiter: RateLimiter):
		"""TC-RL-05: Account normalized to lowercase shares counter."""
		# Make 2 requests with different case
		await rate_limiter.check_rate_limit("192.168.1.1", "Test@Example.COM")
		await rate_limiter.check_rate_limit("192.168.1.2", "test@example.com")

		# Both should count toward same account limit
		# Next 3 requests should succeed, 6th total should fail
		for i in range(3):
			allowed, _, _ = await rate_limiter.check_rate_limit(f"192.168.1.{i+3}", "TEST@EXAMPLE.COM")
			assert allowed is True, f"Request {i+3} should be allowed"

		# 6th request should be blocked (5 total for account)
		allowed, _, limit_type = await rate_limiter.check_rate_limit("192.168.1.10", "test@example.com")
		assert allowed is False, "6th request should be blocked"
		assert limit_type == "account", "Should be account limit"

	async def test_tc_rl_06_no_account_skips_account_check(self, rate_limiter: RateLimiter):
		"""TC-RL-06: No account skips account check."""
		# Make multiple requests without account
		for i in range(10):
			allowed, _, limit_type = await rate_limiter.check_rate_limit(f"192.168.1.1", None)
			assert allowed is True, f"Request {i+1} without account should be allowed (IP limit only)"
			assert limit_type == "", "limit_type should be empty"

		# 11th request should be blocked (IP limit)
		allowed, retry_after, limit_type = await rate_limiter.check_rate_limit("192.168.1.1", None)
		assert allowed is False, "11th request should be blocked by IP limit"
		assert limit_type == "ip", "Should be IP limit"

		# Get remaining without account
		ip_remaining, account_remaining = await rate_limiter.get_remaining("192.168.1.1", None)
		assert ip_remaining == 0, f"IP remaining should be 0, got {ip_remaining}"
		assert account_remaining == 5, f"Account remaining should stay at max (5), got {account_remaining}"
