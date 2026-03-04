"""Concurrency tests for the plan change freeze mechanism.

Verifies that the SET NX EX freeze lock on memora:freeze:{player_id}
correctly serializes concurrent plan change requests:
- Exactly 1 request succeeds (200) out of N simultaneous requests
- All others receive 409 PLAN_CHANGE_IN_PROGRESS
- No partial state: Frappe called once, wallet reset once, freeze released
- Cooldown key set exactly once after success

Uses real Redis (port 13001) + mock Frappe, following conftest.py patterns.

NOTE: get_plan_change_service() calls get_frappe_client() as a direct Python
function (not via Depends), so we must set deps._frappe_client to the mock
directly -- same pattern as test_practice.py and test_review_items.py.
"""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

import fastapi_app.api.deps as deps_module
from fastapi_app.api.deps import get_redis
from fastapi_app.core.redis_keys import (
	access_key,
	daily_xp_key,
	freeze_key,
	game_session_key,
	global_ratelimit_key,
	pending_key,
	plan_change_ts_key,
	player_fsrs_pattern,
	player_fsrs_processed_pattern,
	player_items_learned_pattern,
	player_mastery_pattern,
	player_plan_key,
	player_progress_pattern,
	player_stats_pattern,
	practice_session_key,
	profile_key,
	reviews_overview_key,
	session_key,
	wallet_key,
)
from fastapi_app.core.security import create_access_token
from fastapi_app.main import app

pytestmark = pytest.mark.asyncio


class TestPlanChangeConcurrency:
	"""Verify freeze lock guarantees under concurrent plan change requests."""

	async def _cleanup_player_keys(self, redis_client, player_id: str) -> None:
		"""Remove all test keys for the given player from Redis."""
		direct_keys = [
			freeze_key(player_id),
			plan_change_ts_key(player_id),
			session_key(player_id),
			wallet_key(player_id),
			access_key(player_id),
			game_session_key(player_id),
			daily_xp_key(player_id),
			player_plan_key(player_id),
			profile_key(player_id),
			reviews_overview_key(player_id),
			practice_session_key(player_id),
			pending_key(player_id),
		]
		for key in direct_keys:
			await redis_client.delete(key)

		scan_patterns = [
			player_progress_pattern(player_id),
			player_stats_pattern(player_id),
			player_items_learned_pattern(player_id),
			player_mastery_pattern(player_id),
			player_fsrs_pattern(player_id),
			player_fsrs_processed_pattern(player_id),
		]
		for pattern in scan_patterns:
			cursor = 0
			while True:
				cursor, keys = await redis_client.scan(cursor, match=pattern, count=200)
				if keys:
					await redis_client.delete(*keys)
				if cursor == 0:
					break

	async def _clear_rate_limit_keys(self, redis_client) -> None:
		"""Clear global rate limit counters to prevent cross-test 429s.

		ASGITransport sends requests from a synthetic IP; the middleware
		tracks per-IP counters. Clear them before concurrent bursts.
		"""
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:global_rl:*", count=200)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

	def _make_player(self):
		"""Create a unique player identity (token + IDs) for test isolation."""
		player_id = f"PLAYER-TEST-{uuid4().hex[:8]}"
		family_id = str(uuid4())
		token = create_access_token(
			user_id=player_id,
			plan_id="PLAN-OLD",
			display_name="Concurrency Test Player",
			family_id=family_id,
			mobile="201000000000",
		)
		return player_id, family_id, token

	def _setup_deps(self, redis_client, mock_frappe):
		"""Wire test Redis and mock Frappe into FastAPI dependency resolution.

		Sets deps._frappe_client directly because get_plan_change_service()
		calls get_frappe_client() as a plain function, not via Depends().
		"""
		app.dependency_overrides[get_redis] = lambda: redis_client
		app.state.redis_pool = redis_client.connection_pool
		deps_module._frappe_client = mock_frappe

	def _teardown_deps(self):
		"""Restore dependency overrides and clear Frappe singleton."""
		app.dependency_overrides.clear()
		deps_module._frappe_client = None

	async def test_concurrent_plan_changes_only_one_succeeds(self, redis_client, mock_frappe):
		"""Fire 10 simultaneous plan change requests; exactly 1 wins the freeze lock.

		Setup:
		- Single player with valid session in Redis
		- Mock Frappe with 100ms delay to guarantee overlap window
		- 10 concurrent POST /api/v1/plans/change requests

		Assertions:
		- Exactly 1 response is 200 (success)
		- Exactly 9 responses are 409 (PLAN_CHANGE_IN_PROGRESS)
		- Frappe API called exactly 1 time
		"""
		player_id, family_id, token = self._make_player()

		try:
			# Clear rate limit counters to prevent interference from prior tests
			await self._clear_rate_limit_keys(redis_client)

			# Seed session in Redis for auth validation
			await redis_client.set(
				session_key(player_id),
				json.dumps({"fid": family_id}),
			)

			# Mock Frappe with delay to ensure concurrent requests overlap
			async def delayed_frappe_response(method, params=None):
				await asyncio.sleep(0.1)
				return {
					"status": "ok",
					"history_id": "PLHIST-00001",
					"previous_plan": "PLAN-OLD",
					"trigger_reason": "Player Request",
				}

			mock_frappe.call = AsyncMock(side_effect=delayed_frappe_response)

			self._setup_deps(redis_client, mock_frappe)

			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				# Fire 10 concurrent requests with the same player token
				tasks = [
					client.post(
						"/api/v1/plans/change",
						json={"new_plan_id": "PLAN-NEW"},
						headers={"Authorization": f"Bearer {token}"},
					)
					for _ in range(10)
				]
				responses = await asyncio.gather(*tasks)

			# Verify status code distribution
			status_codes = [r.status_code for r in responses]
			assert status_codes.count(200) == 1, (
				f"Expected exactly 1 success (200), got {status_codes.count(200)}. "
				f"All codes: {status_codes}"
			)
			assert status_codes.count(409) == 9, (
				f"Expected exactly 9 conflicts (409), got {status_codes.count(409)}. "
				f"All codes: {status_codes}"
			)

			# Verify the 409 responses have correct error code
			for resp in responses:
				if resp.status_code == 409:
					detail = resp.json().get("detail", {})
					assert (
						detail.get("error") == "PLAN_CHANGE_IN_PROGRESS"
					), f"Expected PLAN_CHANGE_IN_PROGRESS error, got: {detail}"

			# Verify the success response has correct shape
			for resp in responses:
				if resp.status_code == 200:
					body = resp.json()
					assert body["success"] is True
					assert body["history_id"] == "PLHIST-00001"
					assert body["previous_plan_id"] == "PLAN-OLD"
					assert body["new_plan_id"] == "PLAN-NEW"

			# Verify Frappe was called exactly once (only the winner calls it)
			assert (
				mock_frappe.call.call_count == 1
			), f"Expected Frappe called exactly 1 time, got {mock_frappe.call.call_count}"

		finally:
			self._teardown_deps()
			await self._cleanup_player_keys(redis_client, player_id)

	async def test_no_partial_state_after_concurrent_requests(self, redis_client, mock_frappe):
		"""After concurrent requests complete, verify clean state: no freeze, exactly one cooldown.

		The freeze key must be released after the winning request finishes.
		A cooldown key (plan_change_ts) must exist with TTL for the next 24h window.
		No leftover session key (post-cleanup deletes it).
		"""
		player_id, family_id, token = self._make_player()

		try:
			# Clear rate limit counters
			await self._clear_rate_limit_keys(redis_client)

			# Seed session
			await redis_client.set(
				session_key(player_id),
				json.dumps({"fid": family_id}),
			)

			# Seed a wallet so we can verify it gets cleaned up
			await redis_client.hset(
				wallet_key(player_id),
				mapping={"xp": "500", "streak": "3"},
			)

			# Mock Frappe with delay
			call_count = 0

			async def delayed_frappe_response(method, params=None):
				nonlocal call_count
				call_count += 1
				await asyncio.sleep(0.1)
				return {
					"status": "ok",
					"history_id": f"PLHIST-{call_count:05d}",
					"previous_plan": "PLAN-OLD",
					"trigger_reason": "Player Request",
				}

			mock_frappe.call = AsyncMock(side_effect=delayed_frappe_response)

			self._setup_deps(redis_client, mock_frappe)

			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				tasks = [
					client.post(
						"/api/v1/plans/change",
						json={"new_plan_id": "PLAN-NEW-2"},
						headers={"Authorization": f"Bearer {token}"},
					)
					for _ in range(10)
				]
				responses = await asyncio.gather(*tasks)

			# -- State assertions after all requests complete --

			# 1. Freeze key MUST be released (deleted by _release_freeze in finally block)
			freeze_exists = await redis_client.exists(freeze_key(player_id))
			assert freeze_exists == 0, "Freeze key should be released after plan change completes"

			# 2. Cooldown key MUST exist (set by the winner after success)
			cooldown_ts = await redis_client.get(plan_change_ts_key(player_id))
			assert cooldown_ts is not None, "Cooldown timestamp should be set after successful plan change"
			# Verify TTL is set (approximately 24h = 86400s, allow some margin)
			cooldown_ttl = await redis_client.ttl(plan_change_ts_key(player_id))
			assert cooldown_ttl > 86000, f"Cooldown TTL should be ~86400s, got {cooldown_ttl}"

			# 3. Session key should be deleted by post-cleanup
			session_exists = await redis_client.exists(session_key(player_id))
			assert session_exists == 0, "Session key should be deleted by post-cleanup"

			# 4. Wallet should be deleted by post-cleanup
			wallet_exists = await redis_client.exists(wallet_key(player_id))
			assert wallet_exists == 0, "Wallet key should be deleted by post-cleanup"

			# 5. Frappe called exactly once
			assert call_count == 1, f"Frappe should have been called exactly once, got {call_count}"

			# 6. Exactly 1 success, 9 conflicts (sanity check)
			status_codes = [r.status_code for r in responses]
			assert status_codes.count(200) == 1
			assert status_codes.count(409) == 9

		finally:
			self._teardown_deps()
			await self._cleanup_player_keys(redis_client, player_id)

	async def test_second_attempt_blocked_by_cooldown(self, redis_client, mock_frappe):
		"""After a successful plan change, a second attempt is blocked by 24h cooldown (429).

		This confirms the cooldown key set by the first change prevents immediate re-change.
		"""
		player_id, family_id, token = self._make_player()

		try:
			# Clear rate limit counters
			await self._clear_rate_limit_keys(redis_client)

			# Seed session
			await redis_client.set(
				session_key(player_id),
				json.dumps({"fid": family_id}),
			)

			# Mock Frappe success
			mock_frappe.call = AsyncMock(
				return_value={
					"status": "ok",
					"history_id": "PLHIST-00001",
					"previous_plan": "PLAN-OLD",
					"trigger_reason": "Player Request",
				}
			)

			self._setup_deps(redis_client, mock_frappe)

			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				# First request should succeed
				resp1 = await client.post(
					"/api/v1/plans/change",
					json={"new_plan_id": "PLAN-NEW-3"},
					headers={"Authorization": f"Bearer {token}"},
				)
				assert (
					resp1.status_code == 200
				), f"First request should succeed, got {resp1.status_code}: {resp1.json()}"

				# Re-seed session (post-cleanup deletes it, so second request would fail auth)
				await redis_client.set(
					session_key(player_id),
					json.dumps({"fid": family_id}),
				)

				# Second request should hit cooldown (429)
				resp2 = await client.post(
					"/api/v1/plans/change",
					json={"new_plan_id": "PLAN-NEW-4"},
					headers={"Authorization": f"Bearer {token}"},
				)
				assert (
					resp2.status_code == 429
				), f"Second request should be blocked by cooldown (429), got {resp2.status_code}"
				detail = resp2.json().get("detail", {})
				assert detail.get("error") == "COOLDOWN_ACTIVE"
				assert "retry_after" in detail

		finally:
			self._teardown_deps()
			await self._cleanup_player_keys(redis_client, player_id)

	async def test_freeze_released_even_on_frappe_error(self, redis_client, mock_frappe):
		"""If Frappe API returns an error, the freeze lock is still released.

		This ensures the player is not permanently locked out by a transient failure.
		"""
		player_id, family_id, token = self._make_player()

		try:
			# Clear rate limit counters
			await self._clear_rate_limit_keys(redis_client)

			# Seed session
			await redis_client.set(
				session_key(player_id),
				json.dumps({"fid": family_id}),
			)

			# Mock Frappe returning an error (e.g., invalid plan)
			mock_frappe.call = AsyncMock(
				return_value={
					"status": "error",
					"code": "INVALID_PLAN",
					"message": "Plan does not exist.",
				}
			)

			self._setup_deps(redis_client, mock_frappe)

			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				resp = await client.post(
					"/api/v1/plans/change",
					json={"new_plan_id": "PLAN-INVALID"},
					headers={"Authorization": f"Bearer {token}"},
				)
				assert resp.status_code == 400

			# Freeze key MUST be released even after error
			freeze_exists = await redis_client.exists(freeze_key(player_id))
			assert freeze_exists == 0, "Freeze key should be released even after Frappe error"

			# No cooldown set (change failed)
			cooldown_exists = await redis_client.exists(plan_change_ts_key(player_id))
			assert cooldown_exists == 0, "Cooldown should NOT be set after a failed plan change"

		finally:
			self._teardown_deps()
			await self._cleanup_player_keys(redis_client, player_id)

	async def test_freeze_released_on_frappe_exception(self, redis_client, mock_frappe):
		"""If Frappe call raises an exception, the freeze lock is still released.

		The try/finally in PlanChangeService.execute() guarantees _release_freeze
		runs. We verify at the Redis level that the freeze key is gone, regardless
		of what HTTP status the middleware returns (500 or error propagation).
		"""
		player_id, family_id, token = self._make_player()

		try:
			# Clear rate limit counters
			await self._clear_rate_limit_keys(redis_client)

			# Seed session
			await redis_client.set(
				session_key(player_id),
				json.dumps({"fid": family_id}),
			)

			# Mock Frappe raising an exception
			mock_frappe.call = AsyncMock(side_effect=Exception("Frappe connection timeout"))

			self._setup_deps(redis_client, mock_frappe)

			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				try:
					resp = await client.post(
						"/api/v1/plans/change",
						json={"new_plan_id": "PLAN-NEW-5"},
						headers={"Authorization": f"Bearer {token}"},
					)
					# If we get a response, it should be an error status
					assert resp.status_code >= 400
				except Exception:
					# Starlette may propagate unhandled exceptions as ExceptionGroup
					# through ASGI transport; this is acceptable as long as freeze
					# is released (verified below).
					pass

			# Freeze key MUST be released even after exception
			freeze_exists = await redis_client.exists(freeze_key(player_id))
			assert freeze_exists == 0, "Freeze key should be released even after Frappe exception"

		finally:
			self._teardown_deps()
			await self._cleanup_player_keys(redis_client, player_id)
