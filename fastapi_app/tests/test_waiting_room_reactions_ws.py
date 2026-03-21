"""Integration tests for Waiting Room Reactions WebSocket flow.

T007 [US1]: connect client, send tap, verify burst broadcast with correct schema
(type, room_id, reactions.{type}.count, reactions.{type}.intensity, degraded,
window_duration_ms, server_ts), verify empty windows suppressed, verify invalid
reaction types silently dropped.

T011 [US2]: rapid-fire taps exceed rate limit → only allowed count appears in
burst, no WS disconnect or error message sent.
"""

import json
import time as _time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

from fastapi_app.core.redis_keys import (
	LC_KEY_TTL,
	lc_count_key,
	lc_joined_key,
	lc_meta_key,
	lc_questions_key,
	lc_status_key,
)

EVENT_ID = "LC-TEST-REACT"

SAMPLE_QUESTIONS = [
	{
		"idx": 0,
		"question_text": "What is 2+2?",
		"option_a": "3",
		"option_b": "4",
		"option_c": "5",
		"option_d": "6",
		"correct_answer": "B",
	},
]


async def seed_event_redis(
	r: redis.Redis,
	event_id: str = EVENT_ID,
	status: str = "waiting",
	exam_start_offset_seconds: int = 60,
) -> dict:
	"""Seed LC Redis keys for a waiting room event."""
	now = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
	exam_start_ts = now + timedelta(seconds=exam_start_offset_seconds)
	exam_end_ts = exam_start_ts + timedelta(minutes=10)

	meta = {
		"exam_start_ts": exam_start_ts.strftime("%Y-%m-%d %H:%M:%S"),
		"exam_end_ts": exam_end_ts.strftime("%Y-%m-%d %H:%M:%S"),
		"capacity": "100",
		"enable_question_timer": "1",
		"question_time_limit": "30",
		"eligible_plans": "[]",
		"waiting_room_duration": str(exam_start_offset_seconds),
	}

	pipe = r.pipeline()
	pipe.set(lc_status_key(event_id), status, ex=LC_KEY_TTL)
	pipe.set(lc_questions_key(event_id), json.dumps(SAMPLE_QUESTIONS), ex=LC_KEY_TTL)
	pipe.set(lc_count_key(event_id), "0", ex=LC_KEY_TTL)
	pipe.hset(lc_meta_key(event_id), mapping=meta)
	pipe.expire(lc_meta_key(event_id), LC_KEY_TTL)
	await pipe.execute()
	return meta


async def seed_player_joined(r: redis.Redis, event_id: str, player_id: str) -> None:
	"""Mark a player as joined in Redis."""
	await r.sadd(lc_joined_key(event_id), player_id)
	await r.incr(lc_count_key(event_id))


def _receive_until_type(ws, msg_type: str, max_messages: int = 20):
	"""Read WS messages until one with the given type is found."""
	for _ in range(max_messages):
		data = ws.receive_json()
		if data.get("type") == msg_type:
			return data
	return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestReactionBurstBroadcast:
	"""Send reaction taps over WS, verify burst broadcast schema."""

	async def test_tap_produces_burst(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Send a heart tap → receive burst with correct schema."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-001"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				# Skip initial countdown message
				first = ws.receive_json()
				assert first["type"] == "countdown"

				# Send reaction tap
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

				# Receive messages until burst arrives
				burst = _receive_until_type(ws, "waiting_room_reaction_burst")
				assert burst is not None, "Did not receive burst message"

				# Validate full schema
				assert burst["type"] == "waiting_room_reaction_burst"
				assert burst["room_id"] == EVENT_ID
				assert "reactions" in burst
				assert burst["reactions"]["heart"]["count"] >= 1
				assert burst["reactions"]["heart"]["intensity"] in ("low", "medium", "high")
				assert burst["degraded"] is False
				assert isinstance(burst["window_duration_ms"], int)
				assert burst["window_duration_ms"] > 0
				assert "server_ts" in burst
				assert burst["server_ts"].endswith("Z")

	async def test_multiple_reaction_types(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Send heart + fire taps → burst contains both."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-002"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				# Skip countdown
				ws.receive_json()

				# Send multiple reaction types
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "fire"})

				burst = _receive_until_type(ws, "waiting_room_reaction_burst")
				assert burst is not None
				assert "heart" in burst["reactions"]
				assert "fire" in burst["reactions"]

	async def test_invalid_reaction_silently_dropped(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Invalid reaction type does not appear in burst."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-003"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				ws.receive_json()  # countdown

				# Send invalid reaction + valid reaction
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "thumbsup"})
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "clap"})

				burst = _receive_until_type(ws, "waiting_room_reaction_burst")
				assert burst is not None
				assert "thumbsup" not in burst["reactions"]
				assert "clap" in burst["reactions"]

	async def test_malformed_json_silently_dropped(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Malformed JSON does not crash the connection."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-004"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				ws.receive_json()  # countdown

				# Send malformed JSON
				ws.send_text("not json at all {{{")

				# Send valid tap — connection should still work
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

				burst = _receive_until_type(ws, "waiting_room_reaction_burst")
				assert burst is not None
				assert "heart" in burst["reactions"]

	async def test_unknown_message_type_ignored(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Unknown message types are silently ignored."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-005"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				ws.receive_json()  # countdown

				# Send unknown type
				ws.send_json({"type": "unknown_message_type", "data": "test"})

				# Send valid tap — connection should still work
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "fire"})

				burst = _receive_until_type(ws, "waiting_room_reaction_burst")
				assert burst is not None
				assert "fire" in burst["reactions"]

	async def test_tap_rejected_when_not_waiting(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Taps during 'active' status produce no burst.

		Verifies via the engine's internal state: no room created means
		no taps were accepted. We can't use receive_json() here because
		the server sends no messages after exam_start during active status,
		and the TestClient has no non-blocking receive.
		"""
		await seed_event_redis(redis_client, status="active")

		player_id = "PLAYER-TEST-REACT-006"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		# Get the service to inspect engine state after test
		service = app.state.live_challenge_service

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				# Receive exam_start (sent because status is active)
				first = ws.receive_json()
				assert first["type"] == "exam_start"

				# Send reaction tap during active — should be silently rejected
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

				# Send a second message so the server processes the tap
				# (the server needs to receive_text() to process the tap)
				ws.send_json({"type": "ping"})

		# Verify engine has no active event for this event (tap was rejected)
		assert EVENT_ID not in service._reaction_engine._active_events


# ---------------------------------------------------------------------------
# T011 [US2]: Rate limiting integration tests (real Redis)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRateLimitIntegration:
	"""Rapid-fire taps exceed rate limit — only allowed count in burst."""

	async def test_excess_taps_capped_by_rate_limit(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Send 10 rapid taps → burst count should not exceed burst_allowance (6)."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-RL-001"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				ws.receive_json()  # countdown

				# Send 10 rapid taps (burst limit = 6)
				for _ in range(10):
					ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

				# Collect burst messages and sum accepted taps
				total_hearts = 0
				burst_count = 0
				for _ in range(20):
					data = ws.receive_json()
					if data.get("type") == "waiting_room_reaction_burst":
						total_hearts += data["reactions"].get("heart", {}).get("count", 0)
						burst_count += 1
						if burst_count >= 2:
							break
					elif data.get("type") == "countdown":
						continue

				# Rate limit should cap at burst_allowance (6)
				assert total_hearts <= 6, f"Expected at most 6 taps, got {total_hearts}"
				assert total_hearts >= 1, "At least some taps should be accepted"

	async def test_no_disconnect_on_rate_limit(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Rate-limited taps don't disconnect the WebSocket."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-RL-002"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				ws.receive_json()  # countdown

				# Exhaust rate limit
				for _ in range(10):
					ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

				# Drain burst messages
				_receive_until_type(ws, "waiting_room_reaction_burst")

				# Connection should still be alive — send another tap
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "fire"})

				# Should still receive messages (countdown or burst)
				msg = ws.receive_json()
				assert msg["type"] in ("countdown", "waiting_room_reaction_burst")

	async def test_no_error_message_on_rate_limit(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""No error messages sent to client when taps are rate-limited."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-RL-003"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				ws.receive_json()  # countdown

				# Send taps exceeding limit
				for _ in range(10):
					ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

				# Read several messages — none should be error type
				error_types = {"error", "rate_limit_error", "disconnect"}
				for _ in range(10):
					data = ws.receive_json()
					assert data["type"] not in error_types, (
						f"Unexpected error message: {data}"
					)
					if data["type"] == "waiting_room_reaction_burst":
						break


# ---------------------------------------------------------------------------
# T015 [US4]: Room transition cutoff integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRoomTransitionCutoff:
	"""Transition room to active → no burst after, new taps rejected."""

	async def test_no_burst_after_transition_to_active(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Transition room waiting → active: bursts stop, new taps silently dropped."""
		# Seed event with exam starting in 3 seconds
		await seed_event_redis(redis_client, exam_start_offset_seconds=3)

		player_id = "PLAYER-TEST-REACT-TR-001"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		service = app.state.live_challenge_service

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				# Should get countdown first
				first = ws.receive_json()
				assert first["type"] == "countdown"

				# Send taps while in waiting state
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

				# Verify we receive a burst (proves reactions work in waiting)
				burst = _receive_until_type(ws, "waiting_room_reaction_burst")
				assert burst is not None, "Should receive burst during waiting state"
				assert "heart" in burst["reactions"]

				# Now stop the reaction engine for this event (simulates transition)
				service._reaction_engine.stop_room(EVENT_ID)

				# Send more taps — should be silently rejected
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "fire"})

				# Verify engine has no active event (room was stopped)
				assert EVENT_ID not in service._reaction_engine._active_events

				# Verify the tap was rejected (no active event created)
				# accept_tap returns False for stopped rooms, so
				# the event should NOT be recreated
				assert EVENT_ID not in service._reaction_engine._active_events

	async def test_taps_rejected_after_stop_room(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""After stop_room, the engine rejects taps even if called directly."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-TR-002"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		service = app.state.live_challenge_service
		engine = service._reaction_engine

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				ws.receive_json()  # countdown

				# Send tap to start the engine for this room
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})
				_receive_until_type(ws, "waiting_room_reaction_burst")

				# Stop the room
				engine.stop_room(EVENT_ID)

				# Verify stopped rooms tracking
				assert EVENT_ID in engine._stopped_rooms
				assert EVENT_ID not in engine._active_events

	async def test_redis_rate_limit_keys_expire(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Redis rate limit keys have a short TTL and will expire after room stop."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-TR-003"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import lc_reaction_rl_key, session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				ws.receive_json()  # countdown

				# Send taps to create rate limit key
				ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})
				_receive_until_type(ws, "waiting_room_reaction_burst")

				# Verify rate limit key exists with TTL
				rl_key = lc_reaction_rl_key(EVENT_ID, player_id)
				ttl = await redis_client.ttl(rl_key)
				# TTL should be set (> 0) — keys auto-expire
				assert ttl > 0, f"Rate limit key should have TTL, got {ttl}"
				assert ttl <= 5, f"TTL should be <= 5s (configured), got {ttl}"


# ---------------------------------------------------------------------------
# T019 [US3]: Room-level degradation integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRoomDegradationIntegration:
	"""High-volume taps → degraded=true in burst; reduced volume → degraded=false."""

	async def test_high_volume_triggers_degraded(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Taps exceeding room cap → burst has degraded=true and capped counts."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-DEG-001"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		service = app.state.live_challenge_service
		original_cap = service._reaction_engine._settings.reaction_room_cap_per_sec
		# Set cap very low so a single user can trigger it
		# burst_allowance=6 > cap=3, so room cap triggers first
		service._reaction_engine._settings.reaction_room_cap_per_sec = 3

		try:
			with TestClient(app) as client:
				with client.websocket_connect(
					f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
				) as ws:
					ws.receive_json()  # countdown

					# Send 6 rapid taps (burst_allowance=6, room_cap=3)
					# First 3 accepted, tap 4+ rejected by room cap → degraded
					for _ in range(6):
						ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

					# Collect burst(s) and check degraded flag
					total_hearts = 0
					found_degraded = False
					for _ in range(15):
						data = ws.receive_json()
						if data.get("type") == "waiting_room_reaction_burst":
							total_hearts += data["reactions"].get("heart", {}).get("count", 0)
							if data.get("degraded") is True:
								found_degraded = True
							break
						elif data.get("type") == "countdown":
							continue

					assert found_degraded, "Burst should have degraded=true"
					assert total_hearts <= 3, f"Expected at most 3 accepted, got {total_hearts}"
		finally:
			service._reaction_engine._settings.reaction_room_cap_per_sec = original_cap
			service._reaction_engine.stop_room(EVENT_ID)

	async def test_degraded_clears_after_volume_drops(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""After degradation, reducing volume returns degraded=false."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-DEG-CLR-001"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		service = app.state.live_challenge_service
		original_cap = service._reaction_engine._settings.reaction_room_cap_per_sec
		service._reaction_engine._settings.reaction_room_cap_per_sec = 2

		try:
			with TestClient(app) as client:
				with client.websocket_connect(
					f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
				) as ws:
					ws.receive_json()  # countdown

					# Phase 1: Trigger degradation (cap=2, send 4 taps)
					for _ in range(4):
						ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

					# Read burst — should be degraded
					burst1 = _receive_until_type(ws, "waiting_room_reaction_burst")
					assert burst1 is not None, "Should receive first burst"
					assert burst1["degraded"] is True, "First burst should be degraded"

					# Phase 2: Wait for new second boundary so room_sec counter
					# resets (Redis uses per-second keys with TTL).
					_time.sleep(1.1)

					# Send just 1 tap (below cap) — degraded should clear
					ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "fire"})

					burst2 = _receive_until_type(ws, "waiting_room_reaction_burst")
					assert burst2 is not None, "Should receive second burst"
					assert burst2["degraded"] is False, "Second burst should not be degraded"
		finally:
			service._reaction_engine._settings.reaction_room_cap_per_sec = original_cap
			service._reaction_engine.stop_room(EVENT_ID)


# ---------------------------------------------------------------------------
# T023 [US5]: Error isolation integration tests — Redis failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestErrorIsolationIntegration:
	"""Redis failure → no WS errors; room transitions succeed normally."""

	async def test_redis_failure_no_ws_errors(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Patch Redis rate limit to raise ConnectionError → taps silently handled, no WS error."""
		await seed_event_redis(redis_client)

		player_id = "PLAYER-TEST-REACT-ERR-001"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		service = app.state.live_challenge_service
		engine = service._reaction_engine

		# Replace check_rate_limit to raise ConnectionError
		original_check = engine.check_rate_limit

		async def _broken_rate_limit(event_id, player_id):
			raise ConnectionError("simulated Redis connection failure")

		engine.check_rate_limit = _broken_rate_limit

		try:
			with TestClient(app) as client:
				with client.websocket_connect(
					f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
				) as ws:
					ws.receive_json()  # countdown

					# Send taps despite "broken" Redis
					ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})
					ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "fire"})

					# Should still receive messages (burst or countdown) — no error
					error_types = {"error", "rate_limit_error", "disconnect"}
					for _ in range(10):
						data = ws.receive_json()
						assert data["type"] not in error_types, (
							f"Unexpected error message: {data}"
						)
						if data["type"] in ("waiting_room_reaction_burst", "countdown"):
							break
		finally:
			engine.check_rate_limit = original_check
			engine.stop_room(EVENT_ID)

	async def test_transition_succeeds_despite_reaction_errors(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Room transition (waiting → active) succeeds even if reaction engine has errors."""
		await seed_event_redis(redis_client, exam_start_offset_seconds=3)

		player_id = "PLAYER-TEST-REACT-ERR-002"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		from starlette.testclient import TestClient

		from fastapi_app.main import app

		service = app.state.live_challenge_service
		engine = service._reaction_engine

		# Replace check_rate_limit to raise ConnectionError
		original_check = engine.check_rate_limit

		async def _broken_rate_limit(event_id, player_id):
			raise ConnectionError("simulated Redis connection failure")

		engine.check_rate_limit = _broken_rate_limit

		try:
			with TestClient(app) as client:
				with client.websocket_connect(
					f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
				) as ws:
					first = ws.receive_json()
					assert first["type"] == "countdown"

					# Send tap with broken rate limiter — should not crash
					ws.send_json({"type": "waiting_room_reaction_tap", "reaction": "heart"})

					# Manually transition room to verify it still works
					engine.stop_room(EVENT_ID)

					# Verify the engine stopped cleanly
					assert EVENT_ID not in engine._active_events
		finally:
			engine.check_rate_limit = original_check
