"""Tests for plan change endpoint POST /api/v1/plans/change.

Tests verify:
1. Successful plan change returns 200 with PlanChangeResponse
2. Same plan returns 400 SAME_PLAN
3. Invalid plan returns 400 INVALID_PLAN
4. Cooldown active returns 429 COOLDOWN_ACTIVE with retry_after
5. Concurrent request returns 409 PLAN_CHANGE_IN_PROGRESS
6. Freeze key exists during Frappe API call (mid-flight)
7. Freeze key removed after operation completes
8. Session key deleted after change (old JWT -> 401)

Uses real Redis + mock FrappeClient following project test patterns.

Note: The mock_frappe fixture is injected into the deps module singleton
(_frappe_client) because get_plan_change_service() calls get_frappe_client()
as a direct Python function call, not via FastAPI Depends(). The DI override
alone does not intercept this call path.
"""

import time
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

import fastapi_app.api.deps as deps_module
from fastapi_app.core.redis_keys import (
	PLAN_CHANGE_COOLDOWN_TTL,
	freeze_key,
	plan_change_ts_key,
	session_key,
	wallet_key,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _inject_mock_frappe(mock_frappe: AsyncMock):
	"""Inject mock_frappe into deps singleton so service factory picks it up.

	get_plan_change_service() calls get_frappe_client() directly (not via
	Depends), so the FastAPI dependency override alone is insufficient.
	Setting deps._frappe_client ensures PlanChangeService receives the mock.
	"""
	deps_module._frappe_client = mock_frappe
	yield
	deps_module._frappe_client = None


class TestPlanChangeEndpoint:
	"""Tests for POST /api/v1/plans/change."""

	# ------------------------------------------------------------------
	# 1. Successful plan change
	# ------------------------------------------------------------------

	async def test_successful_plan_change(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Successful plan change returns 200 with history_id, previous/new plan IDs.

		Mock Frappe API returns success with history_id and previous_plan.
		Endpoint should return PlanChangeResponse with success=True.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {
			"status": "ok",
			"history_id": "PLHIST-00001",
			"previous_plan": "PLAN-TEST-001",
			"trigger_reason": "Player Request",
		}

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-001"},
		)

		assert resp.status_code == 200
		data = resp.json()
		assert data["success"] is True
		assert data["history_id"] == "PLHIST-00001"
		assert data["previous_plan_id"] == "PLAN-TEST-001"
		assert data["new_plan_id"] == "PLAN-NEW-001"
		assert "message" in data

		# Verify Frappe API was called with correct params
		mock_frappe.call.assert_awaited_once_with(
			"memora_admin.api.plan_change.execute_plan_change",
			params={"player_id": player_id, "new_plan_id": "PLAN-NEW-001"},
		)

	# ------------------------------------------------------------------
	# 2. Same plan error
	# ------------------------------------------------------------------

	async def test_same_plan_returns_400(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Changing to the same plan returns 400 SAME_PLAN.

		Frappe API returns error with code=SAME_PLAN.
		Endpoint maps this to HTTP 400.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {
			"status": "error",
			"code": "SAME_PLAN",
			"message": "You are already on this plan.",
		}

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-TEST-001"},
		)

		assert resp.status_code == 400
		detail = resp.json()["detail"]
		assert detail["error"] == "SAME_PLAN"
		assert "already" in detail["message"].lower()

	# ------------------------------------------------------------------
	# 3. Invalid plan error
	# ------------------------------------------------------------------

	async def test_invalid_plan_returns_400(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Non-existent or unavailable plan returns 400 INVALID_PLAN.

		Frappe API returns error with code=INVALID_PLAN.
		Endpoint maps this to HTTP 400.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {
			"status": "error",
			"code": "INVALID_PLAN",
			"message": "The selected plan is not available.",
		}

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NONEXISTENT"},
		)

		assert resp.status_code == 400
		detail = resp.json()["detail"]
		assert detail["error"] == "INVALID_PLAN"
		assert "not available" in detail["message"].lower()

	# ------------------------------------------------------------------
	# 4. Cooldown active
	# ------------------------------------------------------------------

	async def test_cooldown_active_returns_429(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Active cooldown returns 429 COOLDOWN_ACTIVE with retry_after.

		Pre-set plan_change_ts key in Redis (recent timestamp).
		Endpoint should reject without calling Frappe API.
		"""
		client, token, player_id, family_id = authed_client

		# Set cooldown timestamp to "just now" so it's still active
		ts_key = plan_change_ts_key(player_id)
		await redis_client.set(ts_key, str(time.time()), ex=PLAN_CHANGE_COOLDOWN_TTL)

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-001"},
		)

		assert resp.status_code == 429
		detail = resp.json()["detail"]
		assert detail["error"] == "COOLDOWN_ACTIVE"
		assert "retry_after" in detail

		# Frappe API should NOT have been called
		mock_frappe.call.assert_not_awaited()

		# Cleanup
		await redis_client.delete(ts_key)

	# ------------------------------------------------------------------
	# 5. Concurrent request (freeze already held)
	# ------------------------------------------------------------------

	async def test_concurrent_request_returns_409(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Concurrent plan change returns 409 PLAN_CHANGE_IN_PROGRESS.

		Pre-set freeze key in Redis to simulate an in-progress change.
		Endpoint should reject without calling Frappe API.
		"""
		client, token, player_id, family_id = authed_client

		# Simulate an in-progress plan change by pre-setting the freeze key
		fk = freeze_key(player_id)
		await redis_client.set(fk, str(time.time()), ex=30)

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-001"},
		)

		assert resp.status_code == 409
		detail = resp.json()["detail"]
		assert detail["error"] == "PLAN_CHANGE_IN_PROGRESS"

		# Frappe API should NOT have been called
		mock_frappe.call.assert_not_awaited()

		# Cleanup
		await redis_client.delete(fk)

	# ------------------------------------------------------------------
	# 6. Freeze key exists during Frappe API call (mid-flight check)
	# ------------------------------------------------------------------

	async def test_freeze_key_exists_during_operation(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Freeze key is held while Frappe API call is in progress.

		Use a side_effect on mock_frappe.call to check freeze key mid-flight.
		The key should exist when the Frappe API call is executing.
		"""
		client, token, player_id, family_id = authed_client

		freeze_existed_during_call = False
		fk = freeze_key(player_id)

		async def frappe_call_side_effect(*args, **kwargs):
			"""Check freeze key exists while inside the Frappe API call."""
			nonlocal freeze_existed_during_call
			exists = await redis_client.exists(fk)
			freeze_existed_during_call = bool(exists)
			return {
				"status": "ok",
				"history_id": "PLHIST-00002",
				"previous_plan": "PLAN-TEST-001",
				"trigger_reason": "Player Request",
			}

		mock_frappe.call = AsyncMock(side_effect=frappe_call_side_effect)

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-002"},
		)

		assert resp.status_code == 200
		assert freeze_existed_during_call is True, "Freeze key should exist during Frappe API call"

	# ------------------------------------------------------------------
	# 7. Freeze key removed after operation
	# ------------------------------------------------------------------

	async def test_freeze_key_removed_after_operation(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Freeze key is removed after plan change completes (success path).

		After a successful plan change, the freeze lock should be released.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {
			"status": "ok",
			"history_id": "PLHIST-00003",
			"previous_plan": "PLAN-TEST-001",
			"trigger_reason": "Player Request",
		}

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-003"},
		)

		assert resp.status_code == 200

		# Freeze key should be gone after completion
		fk = freeze_key(player_id)
		exists = await redis_client.exists(fk)
		assert exists == 0, "Freeze key should be deleted after plan change completes"

	async def test_freeze_key_removed_after_frappe_error(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Freeze key is removed even when Frappe API returns an error.

		The finally block in PlanChangeService.execute() should always
		release the freeze, regardless of success or failure.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {
			"status": "error",
			"code": "INVALID_PLAN",
			"message": "The selected plan is not available.",
		}

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-BAD"},
		)

		assert resp.status_code == 400

		# Freeze key should still be gone after error
		fk = freeze_key(player_id)
		exists = await redis_client.exists(fk)
		assert exists == 0, "Freeze key should be deleted even after Frappe API error"

	# ------------------------------------------------------------------
	# 8. Session key deleted after change (old JWT -> 401)
	# ------------------------------------------------------------------

	async def test_session_invalidated_after_change(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Session key is deleted after plan change, making the old JWT invalid.

		Post-cleanup deletes memora:session:{player_id}.
		Subsequent requests with the same token should fail with 401.

		The PERF-18 in-process session cache must be cleared between requests
		so the second request actually checks Redis (where session is gone).
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {
			"status": "ok",
			"history_id": "PLHIST-00004",
			"previous_plan": "PLAN-TEST-001",
			"trigger_reason": "Player Request",
		}

		# First request: plan change succeeds
		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-004"},
		)
		assert resp.status_code == 200

		# Session key should be deleted by post-cleanup
		sk = session_key(player_id)
		exists = await redis_client.exists(sk)
		assert exists == 0, "Session key should be deleted after plan change"

		# Clear PERF-18 in-process session cache so next request hits Redis
		deps_module._session_fid_cache.clear()

		# Subsequent request with the same token should fail with 401
		# (session no longer exists in Redis)
		resp2 = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-005"},
		)
		assert resp2.status_code == 401, "Old JWT should be rejected after session invalidation"


class TestPlanChangeRedisCleanup:
	"""Tests verifying Redis state cleanup after plan change."""

	async def test_wallet_key_deleted(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Wallet key is deleted during post-cleanup.

		Seed a wallet hash, then execute plan change.
		After success, wallet key should be gone.
		"""
		client, token, player_id, family_id = authed_client

		# Seed wallet
		wk = wallet_key(player_id)
		await redis_client.hset(wk, mapping={"xp": "500", "streak": "3"})

		mock_frappe.call.return_value = {
			"status": "ok",
			"history_id": "PLHIST-00005",
			"previous_plan": "PLAN-TEST-001",
			"trigger_reason": "Player Request",
		}

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-005"},
		)

		assert resp.status_code == 200

		# Wallet key should be gone
		exists = await redis_client.exists(wk)
		assert exists == 0, "Wallet key should be deleted after plan change"

	async def test_cooldown_set_after_success(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Cooldown timestamp is set after successful plan change.

		After a successful change, plan_change_ts:{player_id} should be set
		with a TTL of PLAN_CHANGE_COOLDOWN_TTL (24h).
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {
			"status": "ok",
			"history_id": "PLHIST-00006",
			"previous_plan": "PLAN-TEST-001",
			"trigger_reason": "Player Request",
		}

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-006"},
		)

		assert resp.status_code == 200

		# Cooldown key should exist with a TTL
		ts_key = plan_change_ts_key(player_id)
		ts_value = await redis_client.get(ts_key)
		assert ts_value is not None, "Cooldown timestamp should be set after success"

		# Value should be a valid float timestamp
		ts_float = float(ts_value)
		assert abs(ts_float - time.time()) < 5, "Cooldown timestamp should be close to now"

		# TTL should be set (approximately PLAN_CHANGE_COOLDOWN_TTL)
		ttl = await redis_client.ttl(ts_key)
		assert ttl > 0, "Cooldown key should have a positive TTL"
		assert ttl <= PLAN_CHANGE_COOLDOWN_TTL, "TTL should not exceed cooldown duration"

		# Cleanup
		await redis_client.delete(ts_key)

	async def test_cooldown_not_set_after_frappe_error(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	) -> None:
		"""Cooldown timestamp is NOT set when Frappe API returns an error.

		The cooldown is only set after a successful plan change (step 6),
		but Frappe errors exit at step 4. No cooldown should be written.
		"""
		client, token, player_id, family_id = authed_client

		mock_frappe.call.return_value = {
			"status": "error",
			"code": "SAME_PLAN",
			"message": "You are already on this plan.",
		}

		resp = await client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-TEST-001"},
		)

		assert resp.status_code == 400

		# Cooldown key should NOT exist
		ts_key = plan_change_ts_key(player_id)
		exists = await redis_client.exists(ts_key)
		assert exists == 0, "Cooldown should not be set when Frappe returns an error"


class TestPlanChangeAuth:
	"""Tests for authentication requirements on plan change endpoint."""

	async def test_unauthenticated_returns_401(
		self,
		app_client: AsyncClient,
	) -> None:
		"""Unauthenticated request returns 401 (no Bearer token).

		FastAPI's HTTPBearer returns 401 when no credentials are provided.
		"""
		resp = await app_client.post(
			"/api/v1/plans/change",
			json={"new_plan_id": "PLAN-NEW-001"},
		)

		assert resp.status_code == 401
