"""Tests for plans endpoints.

Tests verify plans endpoint:
- GET /api/v1/plans/{plan_id}/manifest - Get plan manifest (public endpoint)

Reference: contracts/endpoint-test-contracts.md §3
"""

import pytest


@pytest.mark.asyncio
class TestPlansEndpoints:
	"""Plans manifest retrieval tests (public endpoint)."""

	async def test_plans_manifest_success(self, app_client, mock_frappe):
		"""Public request returns plan manifest with subjects."""
		plan_id = "PLAN-TEST-001"

		# Mock must return all PlanManifest required fields
		mock_frappe.call.return_value = {
			"schema_version": 1,
			"version": 1708000000,
			"generated_at": "2026-02-17T12:00:00Z",
			"plan_id": plan_id,
			"title": "Grade 6 Plan",
			"grade_id": "GRD-001",
			"subjects": [
				{
					"id": "SUB-MATH",
					"title": "Mathematics",
					"total_lessons": 100,
				},
				{
					"id": "SUB-SCIENCE",
					"title": "Science",
					"total_lessons": 80,
				},
			],
		}

		resp = await app_client.get(f"/api/v1/plans/{plan_id}/manifest")

		assert resp.status_code == 200
		data = resp.json()
		assert data["plan_id"] == plan_id
		assert "subjects" in data
		assert len(data["subjects"]) == 2
		assert data["subjects"][0]["id"] == "SUB-MATH"

	async def test_plans_manifest_nonexistent_plan_404(self, app_client, mock_frappe):
		"""Nonexistent plan returns 404."""
		plan_id = "PLAN-NONEXISTENT"

		# Mock returns None for nonexistent plan
		mock_frappe.call.return_value = None

		resp = await app_client.get(f"/api/v1/plans/{plan_id}/manifest")

		assert resp.status_code == 404

	async def test_plans_manifest_public_no_auth_required(self, app_client, mock_frappe):
		"""Plans manifest endpoint is public - no auth required."""
		plan_id = "PLAN-TEST-002"

		# Mock must return all PlanManifest required fields
		mock_frappe.call.return_value = {
			"schema_version": 1,
			"version": 1708000000,
			"generated_at": "2026-02-17T12:00:00Z",
			"plan_id": plan_id,
			"title": "Public Plan",
			"subjects": [],
		}

		# No Authorization header - but should succeed because endpoint is public
		resp = await app_client.get(f"/api/v1/plans/{plan_id}/manifest")

		assert resp.status_code == 200  # Not 401
		data = resp.json()
		assert data["plan_id"] == plan_id
