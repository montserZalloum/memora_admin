"""Tests for VoucherService - Voucher HMAC and rate limiting."""

import hashlib
import hmac

import pytest

from fastapi_app.services.frappe_client import FrappeAPIError
from fastapi_app.services.voucher import VoucherService

# Test constants
TEST_PLAYER = "PLAYER-TEST-VCH-001"
TEST_PIN = "123456789"
TEST_HMAC_SECRET = "test-hmac-secret"
TEST_GRANT_ID = "GRANT-VCH-001"
TEST_IP = "192.168.1.100"


@pytest.fixture
async def voucher_svc(redis_client, mock_frappe):
	"""VoucherService with test dependencies."""
	return VoucherService(redis_client, frappe_client=mock_frappe, hmac_secret=TEST_HMAC_SECRET)


@pytest.fixture(autouse=True)
async def cleanup_voucher_keys(redis_client):
	"""Auto-cleanup voucher rate limit keys after each test."""
	yield
	# SCAN and delete all memora:voucher_fail:* keys
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match="memora:voucher_fail:*", count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


class TestHMACComputation:
	"""_compute_hmac is deterministic."""

	def test_tc_vch_01_compute_hmac_determinism(self, voucher_svc):
		"""TC-VCH-01: _compute_hmac twice - same PIN, same digest."""
		# Action: compute HMAC twice
		hmac1 = voucher_svc._compute_hmac(TEST_PIN)
		hmac2 = voucher_svc._compute_hmac(TEST_PIN)

		# Assert: identical hex digests
		assert hmac1 == hmac2

		# Assert: matches hmac.new(...)
		expected = hmac.new(
			TEST_HMAC_SECRET.encode("utf-8"),
			TEST_PIN.encode("utf-8"),
			hashlib.sha256,
		).hexdigest()
		assert hmac1 == expected


class TestRateLimitNoFailures:
	"""check_rate_limit with no prior failures returns None."""

	async def test_tc_vch_02_check_rate_limit_no_prior_failures(self, voucher_svc):
		"""TC-VCH-02: check_rate_limit - no prior failures returns None."""
		# Setup: no failures recorded

		# Action: check rate limit
		retry_after = await voucher_svc.check_rate_limit(TEST_PLAYER, TEST_IP)

		# Assert: returns None
		assert retry_after is None


class TestRateLimitPlayerExceeded:
	"""check_rate_limit after 5 player failures returns retry_after."""

	async def test_tc_vch_03_check_rate_limit_player_exceeded(self, voucher_svc, redis_client):
		"""TC-VCH-03: check_rate_limit - after 5 player failures returns retry_after."""
		# Setup: record 5 failures for player
		for _ in range(5):
			await voucher_svc.record_failure(TEST_PLAYER, TEST_IP)

		# Action: check rate limit
		retry_after = await voucher_svc.check_rate_limit(TEST_PLAYER, TEST_IP)

		# Assert: returns positive retry_after
		assert retry_after is not None
		assert isinstance(retry_after, int)
		assert retry_after > 0


class TestRateLimitIPExceeded:
	"""check_rate_limit after 20 IP failures returns retry_after."""

	async def test_tc_vch_04_check_rate_limit_ip_exceeded(self, voucher_svc, redis_client):
		"""TC-VCH-04: check_rate_limit - after 20 IP failures returns retry_after."""
		# Setup: record 20 failures for IP (from different players)
		for i in range(20):
			player = f"PLAYER-{i}"
			await voucher_svc.record_failure(player, TEST_IP)

		# Action: check rate limit for a player on that IP
		retry_after = await voucher_svc.check_rate_limit(TEST_PLAYER, TEST_IP)

		# Assert: returns positive retry_after
		assert retry_after is not None
		assert isinstance(retry_after, int)
		assert retry_after > 0


class TestPreviewDelegation:
	"""preview delegates to Frappe with HMAC-signed PIN."""

	async def test_tc_vch_05_preview_delegates_with_hmac(self, voucher_svc, mock_frappe):
		"""TC-VCH-05: preview - delegates to Frappe with HMAC (not plaintext PIN)."""
		# Setup: configure mock
		mock_frappe.call.return_value = {"face_value": 100, "grants": []}

		# Action: preview
		result = await voucher_svc.preview(TEST_PIN, TEST_PLAYER)

		# Assert: returns Frappe response
		assert result["face_value"] == 100

		# Assert: Frappe called with HMAC (not plaintext PIN)
		mock_frappe.call.assert_called_once()
		call_args = mock_frappe.call.call_args
		assert call_args[0][0] == "memora_admin.memora_admin.api.voucher.preview_voucher"
		assert "pin_hmac" in call_args[0][1]
		assert call_args[0][1]["pin_hmac"] == voucher_svc._compute_hmac(TEST_PIN)
		# Ensure plaintext PIN is NOT passed
		assert "pin" not in call_args[0][1]


class TestRedeemError:
	"""redeem when Frappe raises FrappeAPIError returns error dict."""

	async def test_tc_vch_06_redeem_frappe_error_returns_error_dict(self, voucher_svc, mock_frappe):
		"""TC-VCH-06: redeem - Frappe raises FrappeAPIError(417, 'EXPIRED'), returns error dict."""
		# Setup: Frappe raises error
		error = FrappeAPIError(417, "EXPIRED")
		mock_frappe.call.side_effect = error

		# Action: redeem
		result = await voucher_svc.redeem(TEST_PIN, TEST_PLAYER, TEST_GRANT_ID, TEST_IP)

		# Assert: returns error dict
		assert "error" in result
		assert result["error"] == "SERVICE_ERROR"


class TestConstructorValidation:
	"""Constructor validates hmac_secret."""

	def test_tc_vch_07_constructor_empty_hmac_secret_raises_valueerror(self, redis_client, mock_frappe):
		"""TC-VCH-07: Constructor with empty hmac_secret - raises ValueError."""
		# Action & Assert: raises ValueError
		with pytest.raises(ValueError) as exc_info:
			VoucherService(redis_client, frappe_client=mock_frappe, hmac_secret="")

		assert "not configured" in str(exc_info.value)
