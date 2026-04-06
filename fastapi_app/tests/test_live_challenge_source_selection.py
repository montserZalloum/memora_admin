"""Tests for explicit data source selection in Live Challenge service.

Verifies the deterministic routing contract:
- Active/waiting events → Redis path only (zero Frappe calls)
- Ended events → DB path only (Frappe reads)
- Missing Redis data during active event → fail (no silent fallback)
- Ended events return correct has_joined / has_submitted from DB
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import redis.asyncio as redis

from fastapi_app.core.redis_keys import (
	LC_KEY_TTL,
	lc_count_key,
	lc_joined_key,
	lc_meta_key,
	lc_questions_key,
	lc_reconciled_key,
	lc_status_key,
	lc_submitted_key,
)
from fastapi_app.services.live_challenge import LiveChallengeService

EVENT_ID = "LC-SRC-TEST-001"
PLAYER_ID = "PLAYER-SRC-TEST-001"

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
]


def _make_meta() -> dict[str, str]:
	now = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
	return {
		"exam_start_ts": (now - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S"),
		"exam_end_ts": (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
		"scheduled_start": (now - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S"),
		"capacity": "100",
		"enable_question_timer": "0",
		"question_time_limit": "30",
		"eligible_plans": "[]",
		"waiting_room_duration": "60",
		"event_name": "Source Selection Test",
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


async def _seed_active_event(r: redis.Redis, event_id: str = EVENT_ID) -> None:
	meta = _make_meta()
	pipe = r.pipeline()
	pipe.set(lc_status_key(event_id), "active", ex=LC_KEY_TTL)
	pipe.set(lc_questions_key(event_id), json.dumps(QUESTIONS), ex=LC_KEY_TTL)
	pipe.set(lc_count_key(event_id), "5", ex=LC_KEY_TTL)
	pipe.hset(lc_meta_key(event_id), mapping=meta)
	pipe.expire(lc_meta_key(event_id), LC_KEY_TTL)
	await pipe.execute()


def _make_frappe_event_doc(event_id: str = EVENT_ID) -> dict:
	now = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
	return {
		"name": event_id,
		"event_name": "Source Selection Test",
		"description": "",
		"status": "Ended",
		"scheduled_start": (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
		"exam_start_ts": (now - timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M:%S"),
		"exam_end_ts": (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
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
				"idx": 1,
				"question_text": "Q1",
				"option_a": "A",
				"option_b": "B",
				"option_c": "C",
				"option_d": "D",
				"correct_answer": "B",
			}
		],
		"eligible_plans": [],
		"participant_count": 10,
	}


@pytest.mark.asyncio
class TestResolveEventSource:
	"""Verify _resolve_event_source returns the correct data source."""

	async def test_active_event_returns_redis(self, redis_client: redis.Redis):
		"""Active event → source is 'redis'."""
		mock_frappe = AsyncMock()
		service = LiveChallengeService(redis_client, mock_frappe)
		await _seed_active_event(redis_client)

		source = await service._resolve_event_source(EVENT_ID)
		assert source == "redis"
		mock_frappe.call.assert_not_called()

	async def test_waiting_event_returns_redis(self, redis_client: redis.Redis):
		"""Waiting event → source is 'redis'."""
		mock_frappe = AsyncMock()
		service = LiveChallengeService(redis_client, mock_frappe)
		await redis_client.set(lc_status_key(EVENT_ID), "waiting", ex=LC_KEY_TTL)

		source = await service._resolve_event_source(EVENT_ID)
		assert source == "redis"

	async def test_ended_status_returns_db(self, redis_client: redis.Redis):
		"""Ended event (status key still in Redis) → source is 'db'."""
		mock_frappe = AsyncMock()
		service = LiveChallengeService(redis_client, mock_frappe)
		await redis_client.set(lc_status_key(EVENT_ID), "ended", ex=LC_KEY_TTL)

		source = await service._resolve_event_source(EVENT_ID)
		assert source == "db"

	async def test_reconciled_with_status_returns_db(self, redis_client: redis.Redis):
		"""Post-reconciliation (status="ended" kept alive) → source is 'db'."""
		mock_frappe = AsyncMock()
		service = LiveChallengeService(redis_client, mock_frappe)
		# Reconciliation keeps status="ended" + sets reconciled flag
		await redis_client.set(lc_status_key(EVENT_ID), "ended", ex=LC_KEY_TTL)
		await redis_client.set(lc_reconciled_key(EVENT_ID), "1", ex=LC_KEY_TTL)

		source = await service._resolve_event_source(EVENT_ID)
		assert source == "db"
		mock_frappe.call.assert_not_called()

	async def test_nonexistent_event_returns_none(self, redis_client: redis.Redis):
		"""Event not in Redis and not in Frappe → None."""
		mock_frappe = AsyncMock()
		mock_frappe.call = AsyncMock(return_value=None)
		service = LiveChallengeService(redis_client, mock_frappe)

		source = await service._resolve_event_source("LC-DOES-NOT-EXIST")
		assert source is None


@pytest.mark.asyncio
class TestActiveEventUsesRedisOnly:
	"""Active events MUST be served entirely from Redis — zero Frappe calls."""

	async def test_active_event_detail_from_redis(self, redis_client: redis.Redis):
		"""get_event_detail for active event reads Redis only."""
		mock_frappe = AsyncMock()
		service = LiveChallengeService(redis_client, mock_frappe)
		await _seed_active_event(redis_client)

		# Mark player as joined + submitted in Redis
		await redis_client.sadd(lc_joined_key(EVENT_ID), PLAYER_ID)
		await redis_client.sadd(lc_submitted_key(EVENT_ID), PLAYER_ID)

		detail = await service.get_event_detail(EVENT_ID, PLAYER_ID)

		assert detail is not None
		assert detail["event_id"] == EVENT_ID
		assert detail["status"] == "Active"
		assert detail["event_name"] == "Source Selection Test"
		assert detail["current_count"] == 5
		assert detail["question_count"] == 1
		assert detail["has_joined"] is True
		assert detail["has_submitted"] is True
		# Zero Frappe calls
		mock_frappe.call.assert_not_called()

	async def test_active_event_not_joined(self, redis_client: redis.Redis):
		"""Active event with player not joined returns has_joined=False."""
		mock_frappe = AsyncMock()
		service = LiveChallengeService(redis_client, mock_frappe)
		await _seed_active_event(redis_client)

		detail = await service.get_event_detail(EVENT_ID, PLAYER_ID)

		assert detail is not None
		assert detail["has_joined"] is False
		assert detail["has_submitted"] is False
		mock_frappe.call.assert_not_called()


@pytest.mark.asyncio
class TestEndedEventUsesDBOnly:
	"""Ended events MUST be served from Frappe DB."""

	async def test_ended_event_detail_from_db(self, redis_client: redis.Redis):
		"""get_event_detail for ended event reads from Frappe."""
		mock_frappe = AsyncMock()
		frappe_doc = _make_frappe_event_doc()

		async def handler(method, params=None):
			if method == "frappe.client.get":
				return frappe_doc
			if method == "frappe.client.get_list":
				return [{"name": "PART-001", "submitted_at": "2026-03-07 14:08:32"}]
			return None

		mock_frappe.call = AsyncMock(side_effect=handler)
		service = LiveChallengeService(redis_client, mock_frappe)

		# Set status to ended in Redis
		await redis_client.set(lc_status_key(EVENT_ID), "ended", ex=LC_KEY_TTL)

		detail = await service.get_event_detail(EVENT_ID, PLAYER_ID)

		assert detail is not None
		assert detail["status"] == "Ended"
		assert detail["event_name"] == "Source Selection Test"
		assert detail["current_count"] == 10
		assert detail["has_joined"] is True
		assert detail["has_submitted"] is True
		# Frappe was called
		assert mock_frappe.call.call_count >= 1

	async def test_ended_event_no_participation(self, redis_client: redis.Redis):
		"""Ended event where player didn't participate → has_joined=False."""
		mock_frappe = AsyncMock()
		frappe_doc = _make_frappe_event_doc()

		async def handler(method, params=None):
			if method == "frappe.client.get":
				return frappe_doc
			if method == "frappe.client.get_list":
				return []  # No participation record
			return None

		mock_frappe.call = AsyncMock(side_effect=handler)
		service = LiveChallengeService(redis_client, mock_frappe)
		await redis_client.set(lc_status_key(EVENT_ID), "ended", ex=LC_KEY_TTL)

		detail = await service.get_event_detail(EVENT_ID, PLAYER_ID)

		assert detail is not None
		assert detail["has_joined"] is False
		assert detail["has_submitted"] is False

	async def test_ended_event_joined_not_submitted(self, redis_client: redis.Redis):
		"""Ended event where player joined but didn't submit."""
		mock_frappe = AsyncMock()
		frappe_doc = _make_frappe_event_doc()

		async def handler(method, params=None):
			if method == "frappe.client.get":
				return frappe_doc
			if method == "frappe.client.get_list":
				return [{"name": "PART-001", "submitted_at": None}]
			return None

		mock_frappe.call = AsyncMock(side_effect=handler)
		service = LiveChallengeService(redis_client, mock_frappe)
		await redis_client.set(lc_status_key(EVENT_ID), "ended", ex=LC_KEY_TTL)

		detail = await service.get_event_detail(EVENT_ID, PLAYER_ID)

		assert detail is not None
		assert detail["has_joined"] is True
		assert detail["has_submitted"] is False

	async def test_reconciled_event_uses_db(self, redis_client: redis.Redis):
		"""Post-reconciliation (status="ended" kept, ephemeral keys deleted) → DB path."""
		mock_frappe = AsyncMock()
		frappe_doc = _make_frappe_event_doc()

		async def handler(method, params=None):
			if method == "frappe.client.get":
				return frappe_doc
			if method == "frappe.client.get_list":
				return [{"name": "PART-001", "submitted_at": "2026-03-07 14:08:32"}]
			return None

		mock_frappe.call = AsyncMock(side_effect=handler)
		service = LiveChallengeService(redis_client, mock_frappe)
		# Status key kept alive after reconciliation, ephemeral keys gone
		await redis_client.set(lc_status_key(EVENT_ID), "ended", ex=LC_KEY_TTL)
		await redis_client.set(lc_reconciled_key(EVENT_ID), "1", ex=LC_KEY_TTL)

		detail = await service.get_event_detail(EVENT_ID, PLAYER_ID)

		assert detail is not None
		assert detail["status"] == "Ended"
		assert detail["has_joined"] is True
		assert detail["has_submitted"] is True


@pytest.mark.asyncio
class TestRedisMissingDuringActiveEvent:
	"""When Redis data is corrupt/missing for an active event, fail — no silent DB fallback."""

	async def test_active_event_missing_meta_returns_none(self, redis_client: redis.Redis):
		"""Active event with status but no meta hash → returns None (controlled failure)."""
		mock_frappe = AsyncMock()
		service = LiveChallengeService(redis_client, mock_frappe)

		# Set status to active but do NOT set meta hash
		await redis_client.set(lc_status_key(EVENT_ID), "active", ex=LC_KEY_TTL)

		detail = await service.get_event_detail(EVENT_ID, PLAYER_ID)

		# Controlled failure — NOT a silent fallback to DB
		assert detail is None
		mock_frappe.call.assert_not_called()

	async def test_grade_missing_joined_set_fails_fast(self, redis_client: redis.Redis):
		"""Grading when player not in Redis joined set → NOT_A_PARTICIPANT (no DB fallback)."""
		mock_frappe = AsyncMock()
		service = LiveChallengeService(redis_client, mock_frappe)
		await _seed_active_event(redis_client)

		# Player is NOT in the joined set — should fail fast
		with pytest.raises(ValueError, match="NOT_A_PARTICIPANT"):
			await service.grade(EVENT_ID, PLAYER_ID, [{"question_idx": 0, "selected": "B"}])

		# No Frappe fallback attempted
		mock_frappe.call.assert_not_called()


@pytest.mark.asyncio
class TestHydrationStampedeGuard:
	"""Hydration is protected by a SETNX guard — only one request hydrates at a time."""

	async def test_concurrent_hydration_blocked(self, redis_client: redis.Redis):
		"""Second concurrent resolve for unknown event returns None (guard blocks it)."""
		mock_frappe = AsyncMock()
		mock_frappe.call = AsyncMock(return_value=None)
		service = LiveChallengeService(redis_client, mock_frappe)

		# Pre-set the guard key — simulates another request already hydrating
		await redis_client.set(f"memora:lc:{EVENT_ID}:hydrate_guard", "1", ex=30)

		source = await service._resolve_event_source(EVENT_ID)
		assert source is None
		# Frappe was NOT called — guard blocked hydration
		mock_frappe.call.assert_not_called()

	async def test_guard_expires_allows_retry(self, redis_client: redis.Redis):
		"""After guard key expires, hydration can be attempted again."""
		mock_frappe = AsyncMock()

		frappe_doc = _make_frappe_event_doc()
		frappe_doc["status"] = "Active"

		async def handler(method, params=None):
			if method == "frappe.client.get":
				return frappe_doc
			return None

		mock_frappe.call = AsyncMock(side_effect=handler)
		service = LiveChallengeService(redis_client, mock_frappe)

		# Set guard with 1s TTL — will expire quickly
		await redis_client.set(f"memora:lc:{EVENT_ID}:hydrate_guard", "1", ex=1)

		# Wait for guard to expire
		import asyncio

		await asyncio.sleep(1.1)

		source = await service._resolve_event_source(EVENT_ID)
		# Now hydration was allowed (event hydrated as active → redis source)
		assert source == "redis"

	async def test_successful_hydration_sets_status(self, redis_client: redis.Redis):
		"""After successful hydration, status key is set and guard is irrelevant."""
		mock_frappe = AsyncMock()

		frappe_doc = _make_frappe_event_doc()
		frappe_doc["status"] = "Ended"

		async def handler(method, params=None):
			if method == "frappe.client.get":
				return frappe_doc
			return None

		mock_frappe.call = AsyncMock(side_effect=handler)
		service = LiveChallengeService(redis_client, mock_frappe)

		# First call: hydrates
		source = await service._resolve_event_source(EVENT_ID)
		assert source == "db"

		# Second call: status key exists, no hydration needed, guard irrelevant
		mock_frappe.call.reset_mock()
		source = await service._resolve_event_source(EVENT_ID)
		assert source == "db"
		mock_frappe.call.assert_not_called()


@pytest.mark.asyncio
class TestEndpointIntegration:
	"""End-to-end tests via HTTP endpoints for source selection."""

	async def test_active_event_endpoint(
		self,
		authed_client: tuple,
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""GET /{event_id} for active event serves from Redis, zero Frappe."""
		from httpx import AsyncClient

		client: AsyncClient
		client, token, player_id, family_id = authed_client
		await _seed_active_event(redis_client)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}")
		assert resp.status_code == 200
		data = resp.json()
		assert data["status"] == "Active"
		assert data["event_name"] == "Source Selection Test"
		mock_frappe.call.assert_not_called()

	async def test_ended_event_endpoint(
		self,
		authed_client: tuple,
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""GET /{event_id} for ended event serves from DB."""
		from httpx import AsyncClient

		client: AsyncClient
		client, token, player_id, family_id = authed_client
		frappe_doc = _make_frappe_event_doc()

		async def handler(method, params=None):
			if method == "frappe.client.get":
				return frappe_doc
			if method == "frappe.client.get_list":
				return [{"name": "PART-001", "submitted_at": "2026-03-17 10:00:00"}]
			return None

		mock_frappe.call = AsyncMock(side_effect=handler)
		await redis_client.set(lc_status_key(EVENT_ID), "ended", ex=LC_KEY_TTL)

		resp = await client.get(f"/api/v1/live-challenge/{EVENT_ID}")
		assert resp.status_code == 200
		data = resp.json()
		assert data["status"] == "Ended"
		assert data["has_joined"] is True
		assert data["has_submitted"] is True

	async def test_missing_event_endpoint(
		self,
		authed_client: tuple,
		redis_client: redis.Redis,
		mock_frappe: AsyncMock,
	):
		"""GET /{event_id} for nonexistent event returns 404."""
		from httpx import AsyncClient

		client: AsyncClient
		client, token, player_id, family_id = authed_client
		mock_frappe.call = AsyncMock(return_value=None)

		resp = await client.get("/api/v1/live-challenge/LC-DOES-NOT-EXIST")
		assert resp.status_code == 404
