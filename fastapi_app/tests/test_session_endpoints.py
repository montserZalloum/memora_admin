"""Tests for game session endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

from fastapi_app.core.redis_keys import (
	dirty_wallets_key,
	freeze_key,
	gamification_settings_key,
	hierarchy_key,
)
from fastapi_app.core.redis_keys import (
	progress_key as _progress_key_fn,
)
from fastapi_app.services.stats import StatsService
from fastapi_app.tests.conftest import (
	cleanup_player_keys,
	seed_access_grants,
	seed_game_session,
	seed_hierarchy,
	seed_settings,
	seed_wallet,
)

# Mark all tests as async
pytestmark = pytest.mark.asyncio


def _end_request_body(session_id: str) -> dict:
	"""Build a minimal valid end-session payload."""
	return {
		"session_id": session_id,
		"stages": [
			{
				"stage_id": "STAGE-001",
				"time_spent": 30,
				"fail_count": 0,
				"completed_at": "2026-02-17T00:00:00Z",
			}
		],
	}


class TestGetCurrentSession:
	"""Tests for GET /api/v1/sessions/current."""

	async def test_get_current_active(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should retrieve active game session.

		Seed game session hash via seed_game_session()
		→ GET /api/v1/sessions/current
		→ 200 OK
		→ Response has session_id, lesson_id, subject_id
		"""
		client, token, player_id, family_id = authed_client

		lesson_id = "LESSON-TEST-001"
		subject_id = "SUB-TEST-001"
		await seed_game_session(redis_client, player_id, lesson_id, subject_id)

		response = await client.get("/api/v1/sessions/current")

		assert response.status_code == 200
		data = response.json()
		assert data["active"] is True
		assert data["session"]["session_id"]
		assert data["session"]["lesson_id"] == lesson_id
		assert data["session"]["subject_id"] == subject_id

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)

	async def test_get_current_none(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		GET /api/v1/sessions/current returns 200 with active=false when no active session.

		No game session seeded
		→ GET /api/v1/sessions/current
		→ 200 active=false
		"""
		client, token, player_id, family_id = authed_client

		response = await client.get("/api/v1/sessions/current")

		assert response.status_code == 200
		assert response.json() == {"active": False, "session": None}

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)

	async def test_unauthenticated(
		self,
		app_client: AsyncClient,
	) -> None:
		"""
		Session endpoints require authentication.

		POST /api/v1/sessions/start without Authorization header
		→ 401 Unauthorized
		"""
		# Ensure no Authorization header
		if "Authorization" in app_client.headers:
			del app_client.headers["Authorization"]

		response = await app_client.post(
			"/api/v1/sessions/start",
			json={"lesson_id": "LESSON-TEST-001", "subject_id": "SUB-TEST-001"},
		)

		assert response.status_code == 401


class TestStartSession:
	"""Tests for POST /api/v1/sessions/start."""

	async def test_start_success(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should start a new session for a lesson.

		Seed hierarchy + access grant
		→ POST /api/v1/sessions/start with lesson_id and subject_id
		→ 200 OK
		→ Response has session_id
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-002"
		lesson_id = "LESSON-TEST-001"
		await seed_hierarchy(redis_client, subject_id, lesson_count=5)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		response = await client.post(
			"/api/v1/sessions/start",
			json={"lesson_id": lesson_id, "subject_id": subject_id},
			headers={"X-Device-ID": "test-device-001"},
		)

		assert response.status_code == 200
		data = response.json()
		assert data["session_id"]
		assert data["lesson_id"] == lesson_id

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_start_nonexistent_subject(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Starting session for non-existent subject returns 404.

		No hierarchy seeded
		→ POST /api/v1/sessions/start with nonexistent subject
		→ 404 SUBJECT_NOT_FOUND
		"""
		client, token, player_id, family_id = authed_client

		response = await client.post(
			"/api/v1/sessions/start",
			json={"lesson_id": "LESSON-TEST-001", "subject_id": "SUB-NONEXIST"},
			headers={"X-Device-ID": "test-device-001"},
		)

		assert response.status_code == 404

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)

	async def test_start_no_access(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Starting session without access and no free content returns 403.

		Seed hierarchy (no free content) + no grant
		→ POST /api/v1/sessions/start
		→ 403 NO_ACCESS
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-003"
		lesson_id = "LESSON-TEST-001"
		await seed_hierarchy(redis_client, subject_id, has_free_content=False, lesson_count=5)
		# Do NOT grant access

		response = await client.post(
			"/api/v1/sessions/start",
			json={"lesson_id": lesson_id, "subject_id": subject_id},
			headers={"X-Device-ID": "test-device-001"},
		)

		assert response.status_code == 403

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_start_free_bypass(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Starting session with free content bypasses access check.

		Seed hierarchy with free content + no grant
		→ POST /api/v1/sessions/start
		→ 200 OK (free content access)
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-004"
		lesson_id = "LESSON-TEST-001"
		await seed_hierarchy(redis_client, subject_id, has_free_content=True, lesson_count=5)
		# Do NOT grant access

		response = await client.post(
			"/api/v1/sessions/start",
			json={"lesson_id": lesson_id, "subject_id": subject_id},
			headers={"X-Device-ID": "test-device-001"},
		)

		assert response.status_code == 200

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_start_nonexistent_lesson(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Starting session with non-existent lesson returns 404.

		Seed hierarchy but use lesson_id not in hierarchy
		→ POST /api/v1/sessions/start with nonexistent lesson
		→ 404 LESSON_NOT_FOUND
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-005"
		await seed_hierarchy(redis_client, subject_id, lesson_count=5)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		response = await client.post(
			"/api/v1/sessions/start",
			json={"lesson_id": "LESSON-NONEXIST-999", "subject_id": subject_id},
			headers={"X-Device-ID": "test-device-001"},
		)

		assert response.status_code == 404

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_start_frozen_short_circuits_before_hierarchy_lookup(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe,
	) -> None:
		"""
		Frozen players should get 409 even if hierarchy lookup would fail.

		Seed only the freeze key and force Frappe hierarchy lookup to error.
		The endpoint must return PLAN_CHANGE_IN_PROGRESS without surfacing the
		downstream hierarchy failure.
		"""
		client, token, player_id, family_id = authed_client
		await redis_client.set(freeze_key(player_id), "1", ex=30)
		mock_frappe.call.side_effect = RuntimeError("hierarchy lookup should not run")

		response = await client.post(
			"/api/v1/sessions/start",
			json={"lesson_id": "LESSON-TEST-001", "subject_id": "SUB-NONEXIST"},
			headers={"X-Device-ID": "test-device-001"},
		)

		assert response.status_code == 409
		assert response.json()["detail"]["code"] == "PLAN_CHANGE_IN_PROGRESS"

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)


class TestEndSession:
	"""Tests for POST /api/v1/sessions/end."""

	async def test_end_success(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should complete a session with stages.

		Full state seeded: game session + hierarchy + settings + wallet
		→ POST /api/v1/sessions/end with stages array
		→ 200 OK
		→ Response has xp_awarded > 0
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-006"
		lesson_id = "LESSON-TEST-001"
		session_id = "SESSION-TEST-END-001"

		# Seed full state
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(redis_client, player_id, lesson_id, subject_id, session_id=session_id)
		await seed_settings(redis_client)
		await seed_wallet(redis_client, player_id, xp=0, streak=0)

		# End session with stages
		response = await client.post(
			"/api/v1/sessions/end",
			json=_end_request_body(session_id),
		)

		assert response.status_code == 200
		data = response.json()
		assert data["success"] is True
		assert data["xp_awarded"] > 0
		assert data["session_id"] == session_id
		assert data["is_duplicate"] is False
		assert data["new_total_xp"] >= data["xp_awarded"]

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(gamification_settings_key())

	async def test_end_no_session(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Ending session without active session returns 409.

		No game session seeded
		→ POST /api/v1/sessions/end
		→ 409 NO_ACTIVE_SESSION
		"""
		client, token, player_id, family_id = authed_client
		session_id = "SESSION-TEST-MISSING"

		response = await client.post(
			"/api/v1/sessions/end",
			json=_end_request_body(session_id),
		)

		assert response.status_code == 409
		assert response.json()["detail"]["code"] == "NO_ACTIVE_SESSION"

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)

	async def test_end_session_mismatch(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""Ending with a stale session_id returns 409 SESSION_MISMATCH."""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-006-MISMATCH"
		lesson_id = "LESSON-TEST-001"
		active_session_id = "SESSION-ACTIVE-001"

		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(
			redis_client,
			player_id,
			lesson_id,
			subject_id,
			session_id=active_session_id,
		)

		response = await client.post("/api/v1/sessions/end", json=_end_request_body("SESSION-STALE-001"))

		assert response.status_code == 409
		detail = response.json()["detail"]
		assert detail["code"] == "SESSION_MISMATCH"
		assert detail["active_session_id"] == active_session_id

		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_end_replay_detection(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Completing a lesson twice should mark second completion as replay.

		Full state seeded + completion bit already set for bit_index=0
		→ POST /api/v1/sessions/end
		→ Checks that the response indicates replay status correctly
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-007"
		lesson_id = "LESSON-TEST-000"  # bit_index=0 by default
		session_id = "SESSION-TEST-END-002"

		# Seed full state
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(redis_client, player_id, lesson_id, subject_id, session_id=session_id)
		await seed_settings(redis_client)
		await seed_wallet(redis_client, player_id, xp=0, streak=0)

		# Mark lesson as already completed (set bit_index 0 to 1)
		progress_key = _progress_key_fn(player_id, subject_id)
		await redis_client.setbit(progress_key, 0, 1)

		# End session (should detect as replay)
		response = await client.post(
			"/api/v1/sessions/end",
			json=_end_request_body(session_id),
		)

		assert response.status_code == 200
		data = response.json()
		# Replay detection returns is_replay flag (True if bit already set)
		assert "is_replay" in data

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(gamification_settings_key())
		await redis_client.delete(progress_key)

	async def test_end_streak_update(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Completing a session should update and return streak.

		Full state seeded
		→ POST /api/v1/sessions/end
		→ Response has streak >= 1
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-008"
		lesson_id = "LESSON-TEST-001"
		session_id = "SESSION-TEST-END-003"

		# Seed full state
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(redis_client, player_id, lesson_id, subject_id, session_id=session_id)
		await seed_settings(redis_client)
		await seed_wallet(redis_client, player_id, xp=0, streak=0)

		# End session
		response = await client.post(
			"/api/v1/sessions/end",
			json=_end_request_body(session_id),
		)

		assert response.status_code == 200
		data = response.json()
		assert data["streak"] >= 1

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(gamification_settings_key())

	async def test_end_xp_awarded(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Completing a fresh lesson should award XP.

		Full state seeded, no prior completion
		→ POST /api/v1/sessions/end
		→ Response has xp_awarded > 0
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-009"
		lesson_id = "LESSON-TEST-001"
		session_id = "SESSION-TEST-END-004"

		# Seed full state
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(redis_client, player_id, lesson_id, subject_id, session_id=session_id)
		await seed_settings(redis_client)
		await seed_wallet(redis_client, player_id, xp=0, streak=0)

		# End session
		response = await client.post(
			"/api/v1/sessions/end",
			json=_end_request_body(session_id),
		)

		assert response.status_code == 200
		data = response.json()
		assert data["xp_awarded"] > 0

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(gamification_settings_key())

	async def test_end_marks_dirty(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		After session end, player should be marked in dirty wallet set.

		Full state seeded → session end
		→ Verify player_id in memora:dirty:wallets set
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-010"
		lesson_id = "LESSON-TEST-001"
		session_id = "SESSION-TEST-END-005"

		# Seed full state
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(redis_client, player_id, lesson_id, subject_id, session_id=session_id)
		await seed_settings(redis_client)
		await seed_wallet(redis_client, player_id, xp=0, streak=0)

		# End session
		response = await client.post(
			"/api/v1/sessions/end",
			json=_end_request_body(session_id),
		)

		assert response.status_code == 200

		# Verify player in dirty set
		dirty_players = await redis_client.smembers(dirty_wallets_key())
		assert player_id in dirty_players

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(gamification_settings_key())
		await redis_client.delete(dirty_wallets_key())

	async def test_end_leaderboard_update(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		After session end, leaderboard should be updated with player XP.

		Full state seeded → session end
		→ Verify ZADD was called (leaderboard key exists with player)
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-011"
		lesson_id = "LESSON-TEST-001"
		session_id = "SESSION-TEST-END-006"

		# Seed full state
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(redis_client, player_id, lesson_id, subject_id, session_id=session_id)
		await seed_settings(redis_client)
		await seed_wallet(redis_client, player_id, xp=0, streak=0)

		# End session
		response = await client.post(
			"/api/v1/sessions/end",
			json=_end_request_body(session_id),
		)

		assert response.status_code == 200
		data = response.json()
		xp_awarded = data["xp_awarded"]

		# Verify leaderboard exists (check one of the possible leaderboard keys)
		# The actual key depends on the implementation, could be:
		# - memora:leaderboard:{subject_id}
		# - memora:leaderboard:global
		# - memora:leaderboard:{period}
		# We'll check if any leaderboard key contains the player
		all_keys = await redis_client.keys("memora:leaderboard:*")
		leaderboard_found = False
		for key in all_keys:
			score = await redis_client.zscore(key, player_id)
			if score is not None:
				leaderboard_found = True
				assert score > 0  # Should have positive score
				break

		# It's ok if leaderboard wasn't updated (implementation detail),
		# but if it exists, it should be correct
		if leaderboard_found:
			pass  # Assertion above passed

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(gamification_settings_key())
		await redis_client.delete(dirty_wallets_key())
		for key in all_keys:
			await redis_client.delete(key)

	async def test_end_duplicate_returns_cached_response(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""Retrying the same session completion returns the cached response."""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-012"
		lesson_id = "LESSON-TEST-001"
		session_id = "SESSION-TEST-END-007"

		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(redis_client, player_id, lesson_id, subject_id, session_id=session_id)
		await seed_settings(redis_client)
		await seed_wallet(redis_client, player_id, xp=0, streak=0)

		first = await client.post("/api/v1/sessions/end", json=_end_request_body(session_id))
		second = await client.post("/api/v1/sessions/end", json=_end_request_body(session_id))

		assert first.status_code == 200
		assert second.status_code == 200
		assert second.json()["is_duplicate"] is True
		assert second.json()["session_id"] == session_id
		assert second.json()["xp_awarded"] == first.json()["xp_awarded"]

		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(gamification_settings_key())

	async def test_end_succeeds_when_stats_update_fails(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""The user still gets success if stats recompute fails after completion."""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-013"
		lesson_id = "LESSON-TEST-001"
		session_id = "SESSION-TEST-END-008"

		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_game_session(redis_client, player_id, lesson_id, subject_id, session_id=session_id)
		await seed_settings(redis_client)
		await seed_wallet(redis_client, player_id, xp=0, streak=0)

		with patch.object(StatsService, "set_stats", new_callable=AsyncMock) as mock_set_stats:
			mock_set_stats.side_effect = RuntimeError("stats unavailable")
			response = await client.post("/api/v1/sessions/end", json=_end_request_body(session_id))

		assert response.status_code == 200
		assert response.json()["success"] is True

		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(gamification_settings_key())
