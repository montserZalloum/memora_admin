"""Tests for purchase endpoints.

Tests verify purchase endpoint:
- POST /api/v1/purchase/ - Submit purchase request

Reference: contracts/endpoint-test-contracts.md §2
"""
import pytest

from fastapi_app.core.redis_keys import pending_key as _pending_key_fn


@pytest.mark.asyncio
class TestPurchaseEndpoints:
	"""Purchase transactional tests."""

	async def test_purchase_success_201(self, authed_client, redis_client, mock_frappe):
		"""Successful purchase submission returns 201."""
		client, token, player_id, family_id = authed_client

		# PurchaseService calls frappe.call then returns PurchaseResponse(default message)
		mock_frappe.call.return_value = {"name": "TXN-001"}

		resp = await client.post(
			"/api/v1/purchase/",
			json={"product_grant_id": "GRNT-001", "payment_method": "Manual-Admin"},
		)

		assert resp.status_code == 201
		data = resp.json()
		assert "message" in data

	async def test_purchase_duplicate_409(self, authed_client, redis_client, mock_frappe):
		"""Duplicate purchase returns 409 via Redis pending set check."""
		client, token, player_id, family_id = authed_client

		# Seed Redis pending set so SISMEMBER returns True
		pending_key = _pending_key_fn(player_id)
		await redis_client.sadd(pending_key, "GRNT-001")

		resp = await client.post(
			"/api/v1/purchase/",
			json={"product_grant_id": "GRNT-001", "payment_method": "Manual-Admin"},
		)

		assert resp.status_code == 409

		# Cleanup
		await redis_client.delete(pending_key)

	async def test_purchase_unauthenticated_401(self, app_client):
		"""Unauthenticated request returns 401."""
		resp = await app_client.post(
			"/api/v1/purchase/",
			json={"product_grant_id": "GRNT-001", "payment_method": "Manual-Admin"},
		)

		assert resp.status_code == 401

	async def test_purchase_invalid_payload_422(self, authed_client, redis_client):
		"""Invalid payload returns 422."""
		client, token, player_id, family_id = authed_client

		# Missing required fields
		resp = await client.post(
			"/api/v1/purchase/",
			json={},
		)

		assert resp.status_code == 422
