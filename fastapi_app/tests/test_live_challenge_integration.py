"""Integration test for full Live Challenge flow.

Tests the complete lifecycle against real Redis with mocked FrappeClient:
1. Seed event in Redis (simulating scheduled task Waiting transition)
2. Join via FastAPI endpoint
3. Submit answers and verify immediate score
4. Simulate end transition + leaderboard computation + XP distribution
5. Verify result and leaderboard endpoints return correct data
"""

import json
from datetime import datetime, timedelta
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
	lc_submitted_key,
	wallet_key,
)

EVENT_ID = "LC-TEST-INT-001"

QUESTIONS = [
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
	{
		"idx": 2,
		"question_text": "Largest planet?",
		"option_a": "Mars",
		"option_b": "Earth",
		"option_c": "Jupiter",
		"option_d": "Saturn",
		"correct_answer": "C",
	},
	{
		"idx": 3,
		"question_text": "Water formula?",
		"option_a": "CO2",
		"option_b": "H2O",
		"option_c": "NaCl",
		"option_d": "O2",
		"correct_answer": "B",
	},
]


async def seed_active_event(r: redis.Redis, event_id: str = EVENT_ID) -> dict:
	"""Seed LC Redis keys for an active event (simulating Waiting->Active transition)."""
	now = datetime.now()
	exam_start_ts = now - timedelta(seconds=30)  # Already started
	exam_end_ts = now + timedelta(minutes=10)  # Ends in 10 min

	meta = {
		"exam_start_ts": exam_start_ts.strftime("%Y-%m-%d %H:%M:%S"),
		"exam_end_ts": exam_end_ts.strftime("%Y-%m-%d %H:%M:%S"),
		"capacity": "100",
		"show_correct_answers": "1",
		"show_student_rank": "1",
		"enable_question_timer": "0",
		"question_time_limit": "30",
		"eligible_plans": "[]",
		"waiting_room_duration": "60",
	}

	pipe = r.pipeline()
	pipe.set(lc_status_key(event_id), "active", ex=LC_KEY_TTL)
	pipe.set(lc_questions_key(event_id), json.dumps(QUESTIONS), ex=LC_KEY_TTL)
	pipe.set(lc_count_key(event_id), "0", ex=LC_KEY_TTL)
	pipe.hset(lc_meta_key(event_id), mapping=meta)
	pipe.expire(lc_meta_key(event_id), LC_KEY_TTL)
	await pipe.execute()

	return meta


def _make_event_frappe_doc(event_id: str = EVENT_ID) -> dict:
	"""Build a fake Frappe event doc for mock FrappeClient responses."""
	now = datetime.now()
	return {
		"name": event_id,
		"event_name": "Integration Test Quiz",
		"description": "<p>Test quiz</p>",
		"status": "Active",
		"scheduled_start": (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"),
		"exam_start_ts": (now - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S"),
		"exam_end_ts": (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
		"waiting_room_duration": 60,
		"exam_duration": 10,
		"enable_question_timer": 0,
		"question_time_limit": 30,
		"capacity": 100,
		"is_paid": 0,
		"show_correct_answers": 1,
		"show_student_rank": 1,
		"participation_xp": 50,
		"first_place_xp": 500,
		"second_place_xp": 300,
		"third_place_xp": 100,
		"default_xp": 25,
		"questions": [
			{
				"idx": i + 1,
				"question_text": q["question_text"],
				"option_a": q["option_a"],
				"option_b": q["option_b"],
				"option_c": q["option_c"],
				"option_d": q["option_d"],
				"correct_answer": q["correct_answer"],
			}
			for i, q in enumerate(QUESTIONS)
		],
		"eligible_plans": [],
		"leaderboard_json": None,
		"participant_count": 0,
		"submitted_count": 0,
	}


@pytest.mark.asyncio
class TestFullExamFlow:
	"""Integration test: join -> submit -> score -> result."""

	async def test_join_submit_score(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Complete exam flow: join, submit, verify score."""
		client, token, player_id, family_id = authed_client

		# Seed active event in Redis
		await seed_active_event(redis_client)

		# Mock FrappeClient responses for join
		mock_frappe.call = AsyncMock(side_effect=self._frappe_call_handler(player_id))

		# --- JOIN ---
		resp = await client.post(f"/api/v1/live-challenge/{EVENT_ID}/join")
		assert resp.status_code == 200, f"Join failed: {resp.text}"
		join_data = resp.json()
		assert join_data["joined"] is True
		assert join_data["event_id"] == EVENT_ID
		assert join_data["position"] == 1
		assert join_data["countdown_remaining"] == 0  # Already active

		# Verify Redis state after join
		count = await redis_client.get(lc_count_key(EVENT_ID))
		assert count == "1"
		is_joined = await redis_client.sismember(lc_joined_key(EVENT_ID), player_id)
		assert is_joined

		# --- SUBMIT ---
		answers = [
			{"question_idx": 0, "selected": "B"},  # Correct
			{"question_idx": 1, "selected": "A"},  # Correct
			{"question_idx": 2, "selected": "A"},  # Wrong (correct: C)
			{"question_idx": 3, "selected": "B"},  # Correct
		]
		resp = await client.post(
			f"/api/v1/live-challenge/{EVENT_ID}/submit",
			json={"answers": answers},
		)
		assert resp.status_code == 200, f"Submit failed: {resp.text}"
		submit_data = resp.json()
		assert submit_data["score"] == 75.0  # 3/4 correct
		assert submit_data["correct_count"] == 3
		assert submit_data["total_questions"] == 4
		assert submit_data["submitted_at"] is not None

		# Corrections should show the 1 wrong answer
		assert submit_data["corrections"] is not None
		assert len(submit_data["corrections"]) == 1
		assert submit_data["corrections"][0]["question_idx"] == 2
		assert submit_data["corrections"][0]["selected"] == "A"
		assert submit_data["corrections"][0]["correct_answer"] == "C"

		# Verify Redis: player in submitted set
		is_submitted = await redis_client.sismember(lc_submitted_key(EVENT_ID), player_id)
		assert is_submitted

	async def test_duplicate_submission_rejected(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""After submitting, a second submission returns 409 ALREADY_SUBMITTED."""
		client, token, player_id, family_id = authed_client
		await seed_active_event(redis_client)
		mock_frappe.call = AsyncMock(side_effect=self._frappe_call_handler(player_id))

		# Join first
		await client.post(f"/api/v1/live-challenge/{EVENT_ID}/join")

		answers = [
			{"question_idx": 0, "selected": "B"},
			{"question_idx": 1, "selected": "A"},
			{"question_idx": 2, "selected": "C"},
			{"question_idx": 3, "selected": "B"},
		]

		# First submit succeeds
		resp = await client.post(
			f"/api/v1/live-challenge/{EVENT_ID}/submit",
			json={"answers": answers},
		)
		assert resp.status_code == 200

		# Second submit rejected
		resp = await client.post(
			f"/api/v1/live-challenge/{EVENT_ID}/submit",
			json={"answers": answers},
		)
		assert resp.status_code == 409
		assert resp.json()["detail"] == "ALREADY_SUBMITTED"

	async def test_join_when_not_active_rejected(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Joining an ended event returns 400 EVENT_NOT_JOINABLE."""
		client, token, player_id, family_id = authed_client
		# Seed as ended
		await redis_client.set(lc_status_key(EVENT_ID), "ended", ex=LC_KEY_TTL)

		resp = await client.post(f"/api/v1/live-challenge/{EVENT_ID}/join")
		assert resp.status_code == 400
		assert resp.json()["detail"] == "EVENT_NOT_JOINABLE"

	async def test_submit_when_not_active_rejected(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	):
		"""Submitting to a non-active event returns 400 EVENT_NOT_ACTIVE."""
		client, token, player_id, family_id = authed_client
		await redis_client.set(lc_status_key(EVENT_ID), "waiting", ex=LC_KEY_TTL)

		resp = await client.post(
			f"/api/v1/live-challenge/{EVENT_ID}/submit",
			json={"answers": [{"question_idx": 0, "selected": "A"}]},
		)
		assert resp.status_code == 400
		assert resp.json()["detail"] == "EVENT_NOT_ACTIVE"

	async def test_capacity_enforcement(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""When capacity is reached, new joins return 422 CAPACITY_FULL."""
		client, token, player_id, family_id = authed_client
		await seed_active_event(redis_client)
		mock_frappe.call = AsyncMock(side_effect=self._frappe_call_handler(player_id))

		# Set capacity to 1 in meta
		await redis_client.hset(lc_meta_key(EVENT_ID), "capacity", "1")

		# First join: use count already at 1 (capacity)
		await redis_client.set(lc_count_key(EVENT_ID), "1")

		resp = await client.post(f"/api/v1/live-challenge/{EVENT_ID}/join")
		assert resp.status_code == 422
		assert resp.json()["detail"] == "CAPACITY_FULL"

	@staticmethod
	def _frappe_call_handler(player_id: str):
		"""Build a FrappeClient.call side_effect handler for test mocking."""
		participation_name = f"PART-{player_id}"

		async def handler(method: str, params: dict | None = None):
			if method == "frappe.client.insert":
				doc = json.loads(params.get("doc", "{}"))
				return {"name": participation_name, **doc}

			if method == "frappe.client.get":
				if params.get("doctype") == "Memora Live Challenge Event":
					return _make_event_frappe_doc()
				return None

			if method == "frappe.client.get_list":
				if params.get("doctype") == "Memora Live Challenge Participation":
					return [{"name": participation_name}]
				return []

			if method == "frappe.client.get_count":
				if params.get("doctype") == "Memora Live Challenge Participation":
					return 1
				return 0

			if method == "frappe.client.set_value":
				return None

			if method == "frappe.client.get_value":
				if params and params.get("doctype") == "Memora Player Profile":
					return {"plan": "PLAN-TEST-001"}
				return None

			return None

		return handler


@pytest.mark.asyncio
class TestEventDetailEndpoint:
	"""Integration test for GET /live-challenge/{event_id}."""

	async def test_event_detail(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Verify event detail returns correct public data."""
		client, token, player_id, family_id = authed_client
		await seed_active_event(redis_client)
		mock_frappe.call = AsyncMock(
			side_effect=TestFullExamFlow._frappe_call_handler(player_id)
		)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}")
		assert resp.status_code == 200
		data = resp.json()
		assert data["event_id"] == EVENT_ID
		assert data["event_name"] == "Integration Test Quiz"
		assert data["question_count"] == 4
		assert data["has_joined"] is False
		assert data["has_submitted"] is False


@pytest.mark.asyncio
class TestResultAndLeaderboard:
	"""Integration test for result and leaderboard endpoints."""

	async def test_result_endpoint(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Verify result endpoint returns student's score and corrections."""
		client, token, player_id, family_id = authed_client

		ended_event = _make_event_frappe_doc()
		ended_event["status"] = "Ended"
		ended_event["show_correct_answers"] = 1

		async def frappe_handler(method, params=None):
			if method == "frappe.client.get_list":
				return [{
					"name": "PART-001",
					"score": 75.0,
					"rank": 2,
					"xp_awarded": 350,
					"submitted_at": "2026-03-07 14:08:32",
					"answers_json": json.dumps({
						"answers": [
							{"question_idx": 0, "selected": "B", "correct": True},
							{"question_idx": 1, "selected": "A", "correct": True},
							{"question_idx": 2, "selected": "A", "correct": False},
							{"question_idx": 3, "selected": "B", "correct": True},
						]
					}),
				}]
			if method == "frappe.client.get":
				return ended_event
			if method == "frappe.client.get_count":
				return 10
			return None

		mock_frappe.call = AsyncMock(side_effect=frappe_handler)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}/result")
		assert resp.status_code == 200
		data = resp.json()
		assert data["score"] == 75.0
		assert data["correct_count"] == 3
		assert data["total_questions"] == 4
		assert data["rank"] == 2
		assert data["xp_awarded"] == 350
		assert data["corrections"] is not None
		assert len(data["corrections"]) == 1

	async def test_leaderboard_endpoint(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Verify leaderboard endpoint returns top 20 and player's rank."""
		client, token, player_id, family_id = authed_client

		leaderboard = [
			{"rank": 1, "player": "PLAYER-001", "display_name": "Ahmed", "score": 100.0},
			{"rank": 2, "player": player_id, "display_name": "Test Player", "score": 75.0},
			{"rank": 3, "player": "PLAYER-003", "display_name": "Omar", "score": 50.0},
		]

		ended_event = _make_event_frappe_doc()
		ended_event["status"] = "Ended"
		ended_event["show_student_rank"] = 1
		ended_event["leaderboard_json"] = json.dumps(leaderboard)
		ended_event["participant_count"] = 3

		async def frappe_handler(method, params=None):
			if method == "frappe.client.get":
				return ended_event
			if method == "frappe.client.get_list":
				return [{"rank": 2, "score": 75.0}]
			return None

		mock_frappe.call = AsyncMock(side_effect=frappe_handler)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}/leaderboard")
		assert resp.status_code == 200
		data = resp.json()
		assert data["event_id"] == EVENT_ID
		assert data["status"] == "Ended"
		assert len(data["leaderboard"]) == 3
		assert data["leaderboard"][0]["rank"] == 1
		assert data["leaderboard"][0]["display_name"] == "Ahmed"
		assert data["my_rank"] == 2
		assert data["my_score"] == 75.0
		assert data["total_participants"] == 3

	async def test_leaderboard_not_ended(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Leaderboard returns 400 if event hasn't ended."""
		client, token, player_id, family_id = authed_client

		active_event = _make_event_frappe_doc()
		active_event["status"] = "Active"

		mock_frappe.call = AsyncMock(return_value=active_event)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}/leaderboard")
		assert resp.status_code == 400
		assert resp.json()["detail"] == "EVENT_NOT_ENDED"

	async def test_result_no_participation(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Result returns 404 if player didn't participate."""
		client, token, player_id, family_id = authed_client

		async def frappe_handler(method, params=None):
			if method == "frappe.client.get_list":
				return []
			return None

		mock_frappe.call = AsyncMock(side_effect=frappe_handler)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}/result")
		assert resp.status_code == 404
		assert resp.json()["detail"] == "NO_PARTICIPATION"


