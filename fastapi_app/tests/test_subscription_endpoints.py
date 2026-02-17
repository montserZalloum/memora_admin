"""Tests for subscription endpoints.

Tests verify subscriptions endpoint:
- GET /api/v1/subscriptions - Get player's grants and plan subjects

Reference: contracts/endpoint-test-contracts.md §8
"""
import pytest


@pytest.mark.asyncio
class TestSubscriptionEndpoints:
	"""Subscription data retrieval tests."""

	async def test_subscriptions_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player gets subscriptions (grants + plan subjects)."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock AccessService.get_player_grants() and get_plan_free_subjects()
			mock_frappe.call.return_value = {
				"grants": ["SUB-MATH", "SUB-SCIENCE", "TRK-GEOMETRY"],
				"plan_subjects": ["SUB-ENGLISH", "SUB-HISTORY"],
			}

			resp = await client.get("/api/v1/subscriptions")

			assert resp.status_code == 200
			data = resp.json()
			assert "grants" in data
			assert "plan_subjects" in data
			assert isinstance(data["grants"], list)
			assert isinstance(data["plan_subjects"], list)
			# Verify sorted order
			assert data["grants"] == sorted(data["grants"])
			assert data["plan_subjects"] == sorted(data["plan_subjects"])
		finally:
			pass

	async def test_subscriptions_unauthenticated_401(self, app_client):
		"""Unauthenticated request returns 401."""
		resp = await app_client.get("/api/v1/subscriptions/")

		assert resp.status_code == 401
