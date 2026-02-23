"""Tests for PurchaseService - Purchase request delegation."""

import pytest
from fastapi import HTTPException

from fastapi_app.services.purchase import PurchaseService
from fastapi_app.services.frappe_client import FrappeAPIError
from fastapi_app.core.redis_keys import pending_key as _pending_key_fn
from fastapi_app.models.purchase import PurchaseRequest

# Test constants
TEST_USER = "USER-TEST-PUR-001"
TEST_PLAN = "PLAN-TEST-PUR-001"
TEST_PRODUCT = "GRANT-TEST-PUR-001"


@pytest.fixture
async def purchase_svc(redis_client, mock_frappe):
	"""PurchaseService with test dependencies."""
	return PurchaseService(redis_client, frappe_client=mock_frappe)


@pytest.fixture(autouse=True)
async def cleanup_purchase_keys(redis_client):
	"""Auto-cleanup pending purchase keys after each test."""
	yield
	# SCAN and delete all memora:pending:* keys
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match="memora:pending:*", count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


class TestSuccessfulPurchase:
	"""Successful purchase adds to pending set."""

	async def test_tc_pur_01_submit_purchase_success(self, purchase_svc, redis_client, mock_frappe):
		"""TC-PUR-01: submit_purchase success - grant added to pending set."""
		# Setup: configure mock
		mock_frappe.call.return_value = {"name": "TXN-001"}

		# Action: submit purchase
		req = PurchaseRequest(product_grant_id=TEST_PRODUCT, payment_method="credit_card", payment_proof_url="http://proof")
		result = await purchase_svc.submit_purchase(TEST_USER, TEST_PLAN, req)

		# Assert: success (no exception)
		assert result is not None

		# Assert: Frappe called
		mock_frappe.call.assert_called_once()

		# Assert: product added to pending set
		pk = _pending_key_fn(TEST_USER)
		is_member = await redis_client.sismember(pk, TEST_PRODUCT)
		assert bool(is_member) is True


class TestDuplicateInRedis:
	"""Duplicate in Redis pending set raises 409."""

	async def test_tc_pur_02_duplicate_in_redis_raises_409(self, purchase_svc, redis_client, mock_frappe):
		"""TC-PUR-02: Duplicate in Redis pending - raises HTTPException(409)."""
		# Setup: add product to pending set
		pk = _pending_key_fn(TEST_USER)
		await redis_client.sadd(pk, TEST_PRODUCT)

		# Action & Assert: raises 409
		req = PurchaseRequest(product_grant_id=TEST_PRODUCT, payment_method="credit_card", payment_proof_url="http://proof")
		with pytest.raises(HTTPException) as exc_info:
			await purchase_svc.submit_purchase(TEST_USER, TEST_PLAN, req)

		assert exc_info.value.status_code == 409

		# Assert: Frappe NOT called
		mock_frappe.call.assert_not_called()


class TestDuplicateFromFrappe:
	"""Frappe duplicate error raises 409."""

	async def test_tc_pur_03_frappe_duplicate_error_raises_409(self, purchase_svc, redis_client, mock_frappe):
		"""TC-PUR-03: Frappe DuplicateEntryError (417) - raises HTTPException(409)."""
		# Setup: Frappe raises duplicate error
		error = FrappeAPIError(417, "DuplicateEntryError: Request already exists")
		mock_frappe.call.side_effect = error

		# Action & Assert: raises 409
		req = PurchaseRequest(product_grant_id=TEST_PRODUCT, payment_method="credit_card", payment_proof_url="http://proof")
		with pytest.raises(HTTPException) as exc_info:
			await purchase_svc.submit_purchase(TEST_USER, TEST_PLAN, req)

		assert exc_info.value.status_code == 409


class TestNotFoundError:
	"""Frappe 404 error raises 404."""

	async def test_tc_pur_04_frappe_404_raises_404(self, purchase_svc, redis_client, mock_frappe):
		"""TC-PUR-04: Frappe 404 (Not Found) - raises HTTPException(404)."""
		# Setup: Frappe raises 404
		error = FrappeAPIError(404, "Product Grant not found")
		mock_frappe.call.side_effect = error

		# Action & Assert: raises 404
		req = PurchaseRequest(product_grant_id=TEST_PRODUCT, payment_method="credit_card", payment_proof_url="http://proof")
		with pytest.raises(HTTPException) as exc_info:
			await purchase_svc.submit_purchase(TEST_USER, TEST_PLAN, req)

		assert exc_info.value.status_code == 404
