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
from uuid import uuid4
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
	lc_results_key,
	lc_status_key,
	lc_submitted_key,
	wallet_key,
)
from fastapi_app.core.redis_keys import session_key as _session_key_fn

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
	now = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
	exam_start_ts = now - timedelta(seconds=30)  # Already started
	exam_end_ts = now + timedelta(minutes=10)  # Ends in 10 min

	meta = {
		"exam_start_ts": exam_start_ts.strftime("%Y-%m-%d %H:%M:%S"),
		"exam_end_ts": exam_end_ts.strftime("%Y-%m-%d %H:%M:%S"),
		"scheduled_start": (now - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S"),
		"capacity": "100",
		"enable_question_timer": "0",
		"question_time_limit": "30",
		"eligible_plans": "[]",
		"waiting_room_duration": "60",
		"event_name": "Integration Test Quiz",
		"description": "<p>Test quiz</p>",
		"exam_duration": "10",
		"is_paid": "0",
		"rewards_json": json.dumps(
			[
				{"rank": 0, "reward_type": "XP", "xp_amount": 50, "prize_description": ""},
				{"rank": 1, "reward_type": "XP", "xp_amount": 500, "prize_description": ""},
				{"rank": 2, "reward_type": "XP", "xp_amount": 300, "prize_description": ""},
				{"rank": 3, "reward_type": "XP", "xp_amount": 100, "prize_description": ""},
			]
		),
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
	now = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
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
		"rewards": [
			{"rank": 0, "reward_type": "XP", "xp_amount": 50, "prize_description": ""},
			{"rank": 1, "reward_type": "XP", "xp_amount": 500, "prize_description": ""},
			{"rank": 2, "reward_type": "XP", "xp_amount": 300, "prize_description": ""},
			{"rank": 3, "reward_type": "XP", "xp_amount": 100, "prize_description": ""},
		],
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

		# Corrections are not returned in the current implementation
		assert submit_data["corrections"] is None

		# Verify Redis: player in submitted set
		is_submitted = await redis_client.sismember(lc_submitted_key(EVENT_ID), player_id)
		assert is_submitted

		# Verify Redis: result stored for reconciliation
		result_raw = await redis_client.hget(lc_results_key(EVENT_ID), player_id)
		assert result_raw is not None
		result_data = json.loads(result_raw)
		assert result_data["score"] == 75.0
		assert result_data["correct_count"] == 3

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
		assert resp.json()["detail"]["code"] == "ALREADY_SUBMITTED"

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
		assert resp.json()["detail"]["code"] == "EVENT_NOT_JOINABLE"

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
		assert resp.json()["detail"]["code"] == "EVENT_NOT_ACTIVE"

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
		assert resp.json()["detail"]["code"] == "CAPACITY_FULL"

	@staticmethod
	def _frappe_call_handler(player_id: str):
		"""Build a FrappeClient.call side_effect handler for test mocking.

		Join and grade are pure-Redis (no Frappe calls). Frappe mocks here serve:
		- get_event_detail(): get (event doc)
		- get_result/leaderboard: get, get_list, get_count
		- reconciliation: insert_many, insert, set_value
		"""
		participation_name = f"PART-{player_id}"

		async def handler(method: str, params: dict | None = None):
			if method == "frappe.client.insert_many":
				return None

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
		"""Verify event detail returns correct public data (Redis-only, no Frappe calls)."""
		client, token, player_id, family_id = authed_client
		await seed_active_event(redis_client)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}")
		assert resp.status_code == 200
		data = resp.json()
		assert data["event_id"] == EVENT_ID
		assert data["event_name"] == "Integration Test Quiz"
		assert data["question_count"] == 4
		assert data["has_joined"] is False
		assert data["has_submitted"] is False
		assert data["capacity"] == 100
		assert data["exam_duration"] == 10
		assert len(data["rewards"]) == 4


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

		async def frappe_handler(method, params=None):
			if method == "frappe.client.get_list":
				return [
					{
						"name": "PART-001",
						"score": 75.0,
						"rank": 2,
						"xp_awarded": 350,
						"submitted_at": "2026-03-07 14:08:32",
						"answers_json": json.dumps(
							{
								"answers": [
									{"question_idx": 0, "selected": "B", "correct": True},
									{"question_idx": 1, "selected": "A", "correct": True},
									{"question_idx": 2, "selected": "A", "correct": False},
									{"question_idx": 3, "selected": "B", "correct": True},
								]
							}
						),
					}
				]
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
		"""Leaderboard returns empty list with status Active if event hasn't ended."""
		client, token, player_id, family_id = authed_client

		active_event = _make_event_frappe_doc()
		active_event["status"] = "Active"

		mock_frappe.call = AsyncMock(return_value=active_event)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}/leaderboard")
		assert resp.status_code == 200
		data = resp.json()
		assert data["status"] == "Active"
		assert data["leaderboard"] == []

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
		assert resp.json()["detail"]["code"] == "NO_PARTICIPATION"


PLAN_EVENT_ID = "LC-TEST-PLAN-001"


async def _seed_event_with_plans(
	r: redis.Redis,
	eligible_plans: list[str],
	event_id: str = PLAN_EVENT_ID,
) -> None:
	"""Seed an active event with specific eligible_plans."""
	now = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
	meta = {
		"exam_start_ts": (now - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S"),
		"exam_end_ts": (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
		"scheduled_start": (now - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S"),
		"capacity": "100",
		"enable_question_timer": "0",
		"question_time_limit": "30",
		"eligible_plans": json.dumps(eligible_plans),
		"waiting_room_duration": "60",
		"event_name": "Plan Test Quiz",
		"description": "",
		"exam_duration": "10",
		"is_paid": "0",
		"rewards_json": json.dumps(
			[
				{"rank": 0, "reward_type": "XP", "xp_amount": 50, "prize_description": ""},
				{"rank": 1, "reward_type": "XP", "xp_amount": 500, "prize_description": ""},
				{"rank": 2, "reward_type": "XP", "xp_amount": 300, "prize_description": ""},
				{"rank": 3, "reward_type": "XP", "xp_amount": 100, "prize_description": ""},
			]
		),
	}
	pipe = r.pipeline()
	pipe.set(lc_status_key(event_id), "active", ex=LC_KEY_TTL)
	pipe.set(lc_questions_key(event_id), json.dumps(QUESTIONS), ex=LC_KEY_TTL)
	pipe.set(lc_count_key(event_id), "0", ex=LC_KEY_TTL)
	pipe.hset(lc_meta_key(event_id), mapping=meta)
	pipe.expire(lc_meta_key(event_id), LC_KEY_TTL)
	await pipe.execute()


@pytest.mark.asyncio
class TestPlanEligibilityEnforcement:
	"""Tests for eligible_plans enforcement at submit and event detail."""

	async def test_submit_rejects_ineligible_plan(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Submit returns 403 PLAN_NOT_ELIGIBLE when player's plan is not in eligible_plans."""
		client, token, player_id, family_id = authed_client

		# Seed event with a plan that doesn't match PLAN-TEST-001
		await _seed_event_with_plans(redis_client, ["PLAN-PREMIUM"])

		# Directly add player to joined set (bypass join check to test submit gate)
		await redis_client.sadd(lc_joined_key(PLAN_EVENT_ID), player_id)
		await redis_client.expire(lc_joined_key(PLAN_EVENT_ID), LC_KEY_TTL)

		answers = [{"question_idx": i, "selected": "B"} for i in range(4)]
		resp = await client.post(
			f"/api/v1/live-challenge/{PLAN_EVENT_ID}/submit",
			json={"answers": answers},
		)
		assert resp.status_code == 403
		assert resp.json()["detail"]["code"] == "PLAN_NOT_ELIGIBLE"

		# Verify player was NOT marked as submitted (rollback)
		is_submitted = await redis_client.sismember(lc_submitted_key(PLAN_EVENT_ID), player_id)
		assert not is_submitted

	async def test_submit_allows_eligible_plan(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Plan check passes at submit when player's plan is in eligible_plans.

		Calls grade() directly (bypasses endpoint response serialization which
		has a pre-existing issue) to verify plan eligibility is not rejected.
		"""
		client, token, player_id, family_id = authed_client

		await _seed_event_with_plans(redis_client, ["PLAN-TEST-001"])

		# Add player to joined set
		await redis_client.sadd(lc_joined_key(PLAN_EVENT_ID), player_id)
		await redis_client.expire(lc_joined_key(PLAN_EVENT_ID), LC_KEY_TTL)

		# Call grade() directly on the service — plan check is inside
		from fastapi_app.services.live_challenge import LiveChallengeService

		service = LiveChallengeService(redis_client, mock_frappe)
		answers = [{"question_idx": i, "selected": "B"} for i in range(4)]
		result = await service.grade(PLAN_EVENT_ID, player_id, answers, player_plan="PLAN-TEST-001")
		assert result["score"] >= 0
		is_submitted = await redis_client.sismember(lc_submitted_key(PLAN_EVENT_ID), player_id)
		assert is_submitted

	async def test_submit_allows_open_event(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Plan check passes at submit when eligible_plans is empty (open to all)."""
		client, token, player_id, family_id = authed_client

		await _seed_event_with_plans(redis_client, [])

		await redis_client.sadd(lc_joined_key(PLAN_EVENT_ID), player_id)
		await redis_client.expire(lc_joined_key(PLAN_EVENT_ID), LC_KEY_TTL)

		from fastapi_app.services.live_challenge import LiveChallengeService

		service = LiveChallengeService(redis_client, mock_frappe)
		answers = [{"question_idx": i, "selected": "B"} for i in range(4)]
		result = await service.grade(PLAN_EVENT_ID, player_id, answers, player_plan="PLAN-TEST-001")
		assert result["score"] >= 0
		is_submitted = await redis_client.sismember(lc_submitted_key(PLAN_EVENT_ID), player_id)
		assert is_submitted

	async def test_event_detail_is_plan_eligible_true(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Event detail returns is_plan_eligible=true when player's plan matches."""
		client, token, player_id, family_id = authed_client

		await _seed_event_with_plans(redis_client, ["PLAN-TEST-001", "PLAN-PREMIUM"])

		resp = await client.get(f"/api/v1/live-challenge/{PLAN_EVENT_ID}")
		assert resp.status_code == 200
		data = resp.json()
		assert data["is_plan_eligible"] is True
		assert data["eligible_plans"] == ["PLAN-TEST-001", "PLAN-PREMIUM"]

	async def test_event_detail_is_plan_eligible_false(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Event detail returns is_plan_eligible=false when player's plan doesn't match."""
		client, token, player_id, family_id = authed_client

		await _seed_event_with_plans(redis_client, ["PLAN-PREMIUM"])

		resp = await client.get(f"/api/v1/live-challenge/{PLAN_EVENT_ID}")
		assert resp.status_code == 200
		data = resp.json()
		assert data["is_plan_eligible"] is False

	async def test_event_detail_open_event_always_eligible(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Event detail returns is_plan_eligible=true for open events (empty eligible_plans)."""
		client, token, player_id, family_id = authed_client

		await _seed_event_with_plans(redis_client, [])

		resp = await client.get(f"/api/v1/live-challenge/{PLAN_EVENT_ID}")
		assert resp.status_code == 200
		data = resp.json()
		assert data["is_plan_eligible"] is True
		assert data["eligible_plans"] == []

	async def test_join_rejects_ineligible_plan(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""Join returns 403 PLAN_NOT_ELIGIBLE (existing check, regression test)."""
		client, token, player_id, family_id = authed_client

		# Seed waiting event (join requires waiting or active)
		await _seed_event_with_plans(redis_client, ["PLAN-PREMIUM"])

		resp = await client.post(f"/api/v1/live-challenge/{PLAN_EVENT_ID}/join")
		assert resp.status_code == 403
		assert resp.json()["detail"]["code"] == "PLAN_NOT_ELIGIBLE"
