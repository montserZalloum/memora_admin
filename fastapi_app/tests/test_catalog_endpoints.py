"""Tests for catalog endpoints.

Tests verify catalog endpoint:
- GET /api/v1/catalog/ - Get player's available products

Reference: contracts/endpoint-test-contracts.md §1
"""
import json

import pytest


@pytest.mark.asyncio
class TestCatalogEndpoints:
	"""Catalog data retrieval tests."""

	async def test_catalog_success_with_products(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player gets catalog with products."""
		client, token, player_id, family_id = authed_client
		from fastapi_app.core.config import get_settings

		settings = get_settings()
		prefix = settings.redis_key_prefix

		try:
			# Seed catalog cache in Redis
			products = [
				{
					"product_grant_id": "GRNT-001",
					"bundle_name": "Math Bundle",
					"price": 100.0,
					"subjects": [{"subject_id": "SUB-MATH", "alias_title": "Mathematics"}],
				},
				{
					"product_grant_id": "GRNT-002",
					"bundle_name": "Science Bundle",
					"price": 150.0,
					"subjects": [{"subject_id": "SUB-SCI", "alias_title": "Science"}],
				},
			]

			# Seed the catalog cache (plan ID comes from authed_client fixture)
			plan_id = "PLAN-TEST-001"  # Default from fixture
			await redis_client.set(f"{prefix}catalog:{plan_id}", json.dumps(products))

			resp = await client.get("/api/v1/catalog/")

			assert resp.status_code == 200
			data = resp.json()
			assert "products" in data
			assert len(data["products"]) == 2
			assert data["products"][0]["product_grant_id"] == "GRNT-001"
			assert data["products"][1]["bundle_name"] == "Science Bundle"
		finally:
			# Cleanup handled by authed_client fixture
			pass

	async def test_catalog_empty_for_no_plan_player(self, authed_client, redis_client, mock_frappe):
		"""Player with no plan gets empty catalog."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock empty catalog response
			mock_frappe.call.return_value = {"products": []}

			resp = await client.get("/api/v1/catalog/")

			assert resp.status_code == 200
			data = resp.json()
			assert "products" in data
			assert data["products"] == []
		finally:
			pass

	async def test_catalog_unauthenticated_401(self, app_client):
		"""Unauthenticated request returns 401."""
		resp = await app_client.get("/api/v1/catalog/")

		assert resp.status_code == 401
