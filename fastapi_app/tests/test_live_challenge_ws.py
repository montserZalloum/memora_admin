"""Integration tests for Live Challenge WebSocket waiting room.

Tests: countdown messages, exam_start broadcast, reconnection during Active,
and event_ended broadcast. Uses real Redis, mocked FrappeClient.
"""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock

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
from fastapi_app.core.security import create_access_token
from fastapi_app.services.live_challenge import LiveChallengeService

# Re-use conftest fixtures: redis_client, app_client, mock_frappe, make_player_token, etc.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EVENT_ID = "LC-TEST-WS"

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
	{
		"idx": 1,
		"question_text": "Capital of Egypt?",
		"option_a": "Cairo",
		"option_b": "Giza",
		"option_c": "Luxor",
		"option_d": "Alex",
		"correct_answer": "A",
	},
]


async def seed_event_redis(
	r: redis.Redis,
	event_id: str = EVENT_ID,
	status: str = "waiting",
	exam_start_offset_seconds: int = 5,
	exam_duration_minutes: int = 10,
	capacity: int = 100,
	questions: list | None = None,
) -> dict:
	"""Seed LC Redis keys as if the scheduled task ran.

	Returns the meta dict for assertions.
	"""
	now = datetime.now(ZoneInfo("Asia/Amman")).replace(tzinfo=None)
	exam_start_ts = now + timedelta(seconds=exam_start_offset_seconds)
	exam_end_ts = exam_start_ts + timedelta(minutes=exam_duration_minutes)

	meta = {
		"exam_start_ts": exam_start_ts.strftime("%Y-%m-%d %H:%M:%S"),
		"exam_end_ts": exam_end_ts.strftime("%Y-%m-%d %H:%M:%S"),
		"capacity": str(capacity),
		"show_correct_answers": "1",
		"show_student_rank": "1",
		"enable_question_timer": "1",
		"question_time_limit": "30",
		"eligible_plans": "[]",
		"waiting_room_duration": str(exam_start_offset_seconds),
	}

	pipe = r.pipeline()
	pipe.set(lc_status_key(event_id), status, ex=LC_KEY_TTL)
	pipe.set(lc_questions_key(event_id), json.dumps(questions or SAMPLE_QUESTIONS), ex=LC_KEY_TTL)
	pipe.set(lc_count_key(event_id), "0", ex=LC_KEY_TTL)
	pipe.hset(lc_meta_key(event_id), mapping=meta)
	pipe.expire(lc_meta_key(event_id), LC_KEY_TTL)
	await pipe.execute()

	return meta


async def seed_player_joined(r: redis.Redis, event_id: str, player_id: str) -> None:
	"""Mark a player as joined in Redis (simulating the join flow)."""
	await r.sadd(lc_joined_key(event_id), player_id)
	await r.incr(lc_count_key(event_id))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWSCountdown:
	"""Verify countdown messages include remaining seconds and participant_count."""

	async def test_countdown_message_fields(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Connect during Waiting and verify we get countdown messages."""
		# Seed event with 60s until start (plenty of time)
		await seed_event_redis(redis_client, exam_start_offset_seconds=60)

		# Create player and mark as joined
		player_id = "PLAYER-TEST-WS-001"
		token, fid = make_player_token(player_id=player_id)
		from fastapi_app.core.redis_keys import session_key

		await redis_client.set(session_key(player_id), json.dumps({"fid": fid}))
		await seed_player_joined(redis_client, EVENT_ID, player_id)

		# Connect WebSocket
		from starlette.testclient import TestClient

		from fastapi_app.main import app

		# Use Starlette's sync TestClient for WebSocket (httpx doesn't support WS)
		with TestClient(app) as client:
			with client.websocket_connect(
				f"/api/v1/live-challenge/{EVENT_ID}/ws?token={token}"
			) as ws:
				# Receive at least one countdown message
				data = ws.receive_json()
				assert data["type"] == "countdown"
				assert "remaining" in data
				assert isinstance(data["remaining"], int)
				assert data["remaining"] > 0
				assert "participant_count" in data
				assert isinstance(data["participant_count"], int)


@pytest.mark.asyncio
class TestWSExamStart:
	"""Verify exam_start message contains questions without correct_answer."""

	async def test_exam_start_broadcast(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Connect during Waiting, wait for exam_start when countdown ends."""
		# Seed event with 2s until start (short countdown for fast test)
		await seed_event_redis(redis_client, exam_start_offset_seconds=2)

		player_id = "PLAYER-TEST-WS-002"
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
				# Collect messages until we get exam_start (max 10s timeout)
				exam_start_msg = None
				deadline = asyncio.get_event_loop().time() + 10
				while asyncio.get_event_loop().time() < deadline:
					try:
						data = ws.receive_json()
					except Exception:
						break
					if data["type"] == "exam_start":
						exam_start_msg = data
						break

				assert exam_start_msg is not None, "Did not receive exam_start message"
				assert "exam_end_ts" in exam_start_msg
				assert "total_questions" in exam_start_msg
				assert exam_start_msg["total_questions"] == len(SAMPLE_QUESTIONS)
				assert "questions" in exam_start_msg
				# Verify NO correct_answer in questions
				for q in exam_start_msg["questions"]:
					assert "correct_answer" not in q
					assert "question_text" in q
					assert "option_a" in q
					assert "idx" in q


@pytest.mark.asyncio
class TestWSReconnectDuringActive:
	"""Verify reconnection during Active receives exam_start immediately."""

	async def test_reconnect_active_gets_exam_start(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""Connect when event is already Active — should get exam_start immediately."""
		# Seed event as already active
		await seed_event_redis(redis_client, status="active", exam_start_offset_seconds=-60)

		player_id = "PLAYER-TEST-WS-003"
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
				# First message should be exam_start (immediate for Active events)
				data = ws.receive_json()
				assert data["type"] == "exam_start"
				assert "questions" in data
				assert len(data["questions"]) == len(SAMPLE_QUESTIONS)
				# No correct_answer
				for q in data["questions"]:
					assert "correct_answer" not in q


@pytest.mark.asyncio
class TestWSEventEnded:
	"""Verify event_ended message is broadcast."""

	async def test_event_ended_broadcast(
		self,
		redis_client: redis.Redis,
		app_client: AsyncClient,
		make_player_token,
	):
		"""When event status changes to ended, connected clients get event_ended."""
		# Seed as active with exam_end_ts in 2 seconds
		now = datetime.now(ZoneInfo("Asia/Amman")).replace(tzinfo=None)
		exam_end_ts = now + timedelta(seconds=2)

		# Seed manually with near-future exam_end
		meta = {
			"exam_start_ts": (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
			"exam_end_ts": exam_end_ts.strftime("%Y-%m-%d %H:%M:%S"),
			"capacity": "100",
			"show_correct_answers": "1",
			"show_student_rank": "1",
			"enable_question_timer": "1",
			"question_time_limit": "30",
			"eligible_plans": "[]",
			"waiting_room_duration": "180",
		}

		pipe = redis_client.pipeline()
		pipe.set(lc_status_key(EVENT_ID), "active", ex=LC_KEY_TTL)
		pipe.set(lc_questions_key(EVENT_ID), json.dumps(SAMPLE_QUESTIONS), ex=LC_KEY_TTL)
		pipe.set(lc_count_key(EVENT_ID), "1", ex=LC_KEY_TTL)
		pipe.hset(lc_meta_key(EVENT_ID), mapping=meta)
		pipe.expire(lc_meta_key(EVENT_ID), LC_KEY_TTL)
		await pipe.execute()

		player_id = "PLAYER-TEST-WS-004"
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
				# First message will be exam_start (Active event)
				data = ws.receive_json()
				assert data["type"] == "exam_start"

				# Wait for event_ended (should come when exam_end_ts passes)
				ended_msg = None
				for _ in range(15):
					try:
						data = ws.receive_json()
					except Exception:
						break
					if data["type"] == "event_ended":
						ended_msg = data
						break

				assert ended_msg is not None, "Did not receive event_ended message"
				assert ended_msg["type"] == "event_ended"
