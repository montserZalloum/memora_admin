"""Tests for GET /api/v1/plans/available endpoint.

Tests verify:
1. Returns plans grouped by grade with correct structure
2. Excludes player's current plan from results
3. Only includes plans with active seasons (is_published=1, end_date >= today)
4. Returns empty grades array with total=0 when no eligible plans exist
5. Each plan includes all required fields

Uses real Redis + mock FrappeClient following project test patterns.
"""

from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

import fastapi_app.api.deps as deps_module

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _inject_mock_frappe(mock_frappe: AsyncMock):
	"""Inject mock_frappe into deps singleton so service factory picks it up."""
	deps_module._frappe_client = mock_frappe
	yield
	deps_module._frappe_client = None


# Sample plan data returned by Frappe API
SAMPLE_PLANS = [
	{
		"name": "PLAN-001",
		"plan_name": "Grade 10 Scientific S2",
		"grade": "GRADE-003",
		"grade_name": "Grade 10",
		"major": "MAJOR-007",
		"major_name": "Scientific",
		"season": "SEAS-028",
		"season_title": "Second Semester 2026",
	},
	{
		"name": "PLAN-002",
		"plan_name": "Grade 10 Literary S2",
		"grade": "GRADE-003",
		"grade_name": "Grade 10",
		"major": "MAJOR-008",
		"major_name": "Literary",
		"season": "SEAS-028",
		"season_title": "Second Semester 2026",
	},
	{
		"name": "PLAN-003",
		"plan_name": "Grade 11 Scientific S2",
		"grade": "GRADE-004",
		"grade_name": "Grade 11",
		"major": "MAJOR-007",
		"major_name": "Scientific",
		"season": "SEAS-028",
		"season_title": "Second Semester 2026",
	},
]


class TestAvailablePlansEndpoint:
	"""Tests for GET /api/v1/plans/available."""

	async def test_returns_plans_grouped_by_grade(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Plans are grouped by grade with correct structure.

		Frappe returns 3 plans across 2 grades.
		Endpoint should return 2 GradePlanGroup entries with correct plans.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {"plans": SAMPLE_PLANS}

		resp = await client.get("/api/v1/plans/available")

		assert resp.status_code == 200
		data = resp.json()

		assert "grades" in data
		assert "total" in data
		assert data["total"] == 3
		assert len(data["grades"]) == 2

		# Find Grade 10 group
		grade_10 = next((g for g in data["grades"] if g["grade_id"] == "GRADE-003"), None)
		assert grade_10 is not None
		assert grade_10["grade_name"] == "Grade 10"
		assert len(grade_10["plans"]) == 2

		# Find Grade 11 group
		grade_11 = next((g for g in data["grades"] if g["grade_id"] == "GRADE-004"), None)
		assert grade_11 is not None
		assert grade_11["grade_name"] == "Grade 11"
		assert len(grade_11["plans"]) == 1

	async def test_excludes_current_plan(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Player's current plan is excluded from the results.

		The Frappe API is called with the player's current plan ID
		so the SQL filter excludes it.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {"plans": SAMPLE_PLANS}

		resp = await client.get("/api/v1/plans/available")

		assert resp.status_code == 200

		# Verify Frappe was called with the player's current plan
		mock_frappe.call.assert_awaited_once()
		call_args = mock_frappe.call.call_args
		assert call_args[0][0] == "memora_admin.api.plan_change.get_available_plans"
		params = (
			call_args[1].get("params") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["params"]
		)
		assert "current_plan_id" in params

	async def test_empty_plans_returns_empty_response(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""No eligible plans returns empty grades array with total=0.

		When Frappe returns empty plans list (e.g., no active seasons),
		the endpoint returns an empty response.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {"plans": []}

		resp = await client.get("/api/v1/plans/available")

		assert resp.status_code == 200
		data = resp.json()
		assert data["grades"] == []
		assert data["total"] == 0

	async def test_plan_includes_all_required_fields(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Each plan in the response includes all required fields.

		Required: plan_id, plan_name, grade_id, grade_name,
		major_id, major_name, season_id, season_title.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {"plans": [SAMPLE_PLANS[0]]}

		resp = await client.get("/api/v1/plans/available")

		assert resp.status_code == 200
		data = resp.json()
		assert data["total"] == 1

		plan = data["grades"][0]["plans"][0]
		assert plan["plan_id"] == "PLAN-001"
		assert plan["plan_name"] == "Grade 10 Scientific S2"
		assert plan["grade_id"] == "GRADE-003"
		assert plan["grade_name"] == "Grade 10"
		assert plan["major_id"] == "MAJOR-007"
		assert plan["major_name"] == "Scientific"
		assert plan["season_id"] == "SEAS-028"
		assert plan["season_title"] == "Second Semester 2026"

	async def test_frappe_returns_none_handled_gracefully(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Frappe returning None is handled gracefully as empty result."""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = None

		resp = await client.get("/api/v1/plans/available")

		assert resp.status_code == 200
		data = resp.json()
		assert data["grades"] == []
		assert data["total"] == 0


class TestAvailablePlansAuth:
	"""Tests for authentication requirements on available plans endpoint."""

	async def test_unauthenticated_returns_401(
		self,
		app_client: AsyncClient,
	) -> None:
		"""Unauthenticated request returns 401."""
		resp = await app_client.get("/api/v1/plans/available")
		assert resp.status_code == 401
