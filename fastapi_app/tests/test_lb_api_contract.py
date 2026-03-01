"""API contract validation tests for leaderboard endpoints.

Tests HTTP endpoints with seeded multi-user data to validate:
- Response schema correctness
- Sorting correctness
- Rank consistency between GET /{type} and GET /{type}/me
- Edge cases in neighbor window at API level
- Plan-scoped isolation through the API
- Pagination through the API

FINDINGS:
┌─────────────────────────────────────────────────────────────────────────┐
│ FINDING-16: The endpoint calls leaderboard_service._get_plan_key()     │
│ directly to get ZCARD (leaderboard.py:73). This is a private method   │
│ access pattern. If the key format changes, both the service and the   │
│ endpoint would need updating. Consider adding a get_total_players()    │
│ method to the service.                                                  │
│                                                                         │
│ FINDING-17: profile_service.get_profiles_batch() is called for every   │
│ leaderboard request. If the profile cache is cold, this could add      │
│ latency to leaderboard responses. The warm_profile_cache task mitigates │
│ this but only runs hourly.                                              │
│                                                                         │
│ FINDING-18: The LeaderboardEntry model requires display_name (str),    │
│ but if profile_service returns a missing profile, it would KeyError    │
│ on profiles[entry["player_id"]].display_name. The profile service     │
│ should return a fallback display name — verify this is handled.        │
└─────────────────────────────────────────────────────────────────────────┘
"""

import json
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from fastapi_app.core.redis_keys import (
	lb_daily_plan_key,
	lb_weekly_plan_key,
	session_key as _session_key_fn,
)
from fastapi_app.core.security import create_access_token

AMMAN_TZ = ZoneInfo("Asia/Amman")


# -- Helpers -------------------------------------------------------------------


def _current_date_str() -> str:
	return datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")


def _current_friday() -> str:
	now = datetime.now(AMMAN_TZ)
	weekday = now.isoweekday()
	days_since_friday = (weekday - 5) % 7
	return (now - timedelta(days=days_since_friday)).strftime("%Y-%m-%d")


async def _seed_plan_leaderboard(redis_client, plan_id, players, lb_type="weekly"):
	"""Directly seed ZSET for testing (bypasses service layer)."""
	if lb_type == "weekly":
		key = lb_weekly_plan_key(_current_friday(), plan_id)
	else:
		key = lb_daily_plan_key(_current_date_str(), plan_id)

	pipe = redis_client.pipeline()
	for player_id, xp in players:
		pipe.zadd(key, {player_id: xp})
	pipe.expire(key, 3600)
	await pipe.execute()


async def _authed_request(app_client, redis_client, player_id, plan_id, path, method="get"):
	"""Make authenticated request and return response."""
	family_id = str(uuid4())
	token = create_access_token(
		user_id=player_id,
		plan_id=plan_id,
		display_name="Test Player",
		family_id=family_id,
		mobile="201000000000",
	)

	sess_key = _session_key_fn(player_id)
	await redis_client.set(sess_key, json.dumps({"fid": family_id}))

	try:
		app_client.headers["Authorization"] = f"Bearer {token}"
		if method == "get":
			resp = await app_client.get(path)
		return resp
	finally:
		del app_client.headers["Authorization"]
		await redis_client.delete(sess_key)


async def _scan_delete(redis_client, pattern):
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match=pattern, count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


@pytest.fixture(autouse=True)
async def cleanup_lb_keys(redis_client):
	yield
	for pattern in ("memora:lb:*", "memora:lbmeta:*"):
		await _scan_delete(redis_client, pattern)


# -- Test Classes --------------------------------------------------------------


@pytest.mark.asyncio
class TestLeaderboardResponseSchema:
	"""Validate response schema for /leaderboard/{type}."""

	PLAN = "PLAN-TEST-SCHEMA"

	async def test_daily_response_has_all_fields(self, app_client, redis_client, mock_frappe):
		"""Response includes leaderboard_type, subject_id, entries, total_players."""
		players = [(f"PLAYER-TEST-SCH-{i:03d}", (10 - i) * 50) for i in range(5)]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, players[0][0], self.PLAN,
			"/api/v1/leaderboard/daily",
		)
		assert resp.status_code == 200
		data = resp.json()

		# Required fields
		assert data["leaderboard_type"] == "daily"
		assert data["subject_id"] is None
		assert isinstance(data["entries"], list)
		assert isinstance(data["total_players"], int)
		assert data["total_players"] == 5

	async def test_entry_has_all_fields(self, app_client, redis_client, mock_frappe):
		"""Each entry has rank, player_id, display_name, xp, avatar, is_me."""
		players = [("PLAYER-TEST-SCH-ENT", 100)]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-SCH-ENT", self.PLAN,
			"/api/v1/leaderboard/daily",
		)
		data = resp.json()
		entry = data["entries"][0]

		assert "rank" in entry
		assert "player_id" in entry
		assert "display_name" in entry
		assert "xp" in entry
		assert "avatar" in entry
		assert "is_me" in entry

		assert isinstance(entry["rank"], int)
		assert isinstance(entry["player_id"], str)
		assert isinstance(entry["display_name"], str)
		assert isinstance(entry["xp"], int)
		assert isinstance(entry["is_me"], bool)


@pytest.mark.asyncio
class TestMyRankResponseSchema:
	"""Validate response schema for /leaderboard/{type}/me."""

	PLAN = "PLAN-TEST-MESCH"

	async def test_me_response_has_all_fields(self, app_client, redis_client, mock_frappe):
		"""MyRankResponse includes rank, xp, xp_to_next, neighbors, total_players."""
		players = [
			("PLAYER-TEST-MESCH-001", 500),
			("PLAYER-TEST-MESCH-002", 300),
			("PLAYER-TEST-MESCH-003", 100),
		]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-MESCH-002", self.PLAN,
			"/api/v1/leaderboard/daily/me",
		)
		assert resp.status_code == 200
		data = resp.json()

		assert "rank" in data
		assert "xp" in data
		assert "xp_to_next" in data
		assert "neighbors" in data
		assert "total_players" in data

		assert isinstance(data["rank"], int)
		assert isinstance(data["xp"], int)
		assert isinstance(data["neighbors"], list)
		assert isinstance(data["total_players"], int)

	async def test_me_unranked_response(self, app_client, redis_client, mock_frappe):
		"""Unranked player gets rank=null, xp=0, empty neighbors."""
		# Seed other players but not the requesting player
		players = [("PLAYER-TEST-MESCH-OTHER", 500)]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-MESCH-NONE", self.PLAN,
			"/api/v1/leaderboard/daily/me",
		)
		data = resp.json()

		assert data["rank"] is None
		assert data["xp"] == 0
		assert data["neighbors"] == []
		assert data["total_players"] == 1  # 1 other player exists

	async def test_me_neighbor_has_is_me_flag(self, app_client, redis_client, mock_frappe):
		"""Exactly one neighbor entry has is_me=True."""
		players = [
			("PLAYER-TEST-MESCH-A", 500),
			("PLAYER-TEST-MESCH-B", 300),
			("PLAYER-TEST-MESCH-C", 100),
		]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-MESCH-B", self.PLAN,
			"/api/v1/leaderboard/daily/me",
		)
		data = resp.json()

		is_me_count = sum(1 for n in data["neighbors"] if n["is_me"])
		assert is_me_count == 1, f"Expected exactly 1 is_me=True, got {is_me_count}"


@pytest.mark.asyncio
class TestAPISortingCorrectness:
	"""Verify entries are sorted by XP descending through the API."""

	PLAN = "PLAN-TEST-SORT"

	async def test_entries_sorted_descending(self, app_client, redis_client, mock_frappe):
		"""Entries returned from API are sorted by XP descending."""
		import random

		random.seed(42)
		players = [
			(f"PLAYER-TEST-SORT-{i:03d}", random.randint(1, 10000))
			for i in range(20)
		]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, players[0][0], self.PLAN,
			"/api/v1/leaderboard/daily",
		)
		data = resp.json()
		xps = [e["xp"] for e in data["entries"]]

		for i in range(1, len(xps)):
			assert xps[i] <= xps[i - 1], (
				f"Not sorted at position {i}: {xps[i-1]} → {xps[i]}"
			)


@pytest.mark.asyncio
class TestAPIRankConsistency:
	"""Verify rank consistency between GET /{type} and GET /{type}/me through API."""

	PLAN = "PLAN-TEST-CONSIST"

	async def test_get_top_and_get_me_agree(self, app_client, redis_client, mock_frappe):
		"""Player's rank in GET /{type} matches their rank in GET /{type}/me."""
		players = [
			("PLAYER-TEST-CON-A", 500),
			("PLAYER-TEST-CON-B", 300),
			("PLAYER-TEST-CON-C", 300),
			("PLAYER-TEST-CON-D", 100),
		]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="weekly")

		# Get top (as player D)
		resp_top = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-CON-D", self.PLAN,
			"/api/v1/leaderboard/weekly",
		)
		# Get me (as player D)
		resp_me = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-CON-D", self.PLAN,
			"/api/v1/leaderboard/weekly/me",
		)

		top_data = resp_top.json()
		me_data = resp_me.json()

		# Find D's rank in top
		d_entries = [e for e in top_data["entries"] if e["player_id"] == "PLAYER-TEST-CON-D"]
		assert len(d_entries) == 1

		# Must match /me rank
		assert d_entries[0]["rank"] == me_data["rank"]
		assert d_entries[0]["xp"] == me_data["xp"]

	async def test_dense_ranking_through_api(self, app_client, redis_client, mock_frappe):
		"""Dense ranking (1,1,2) is consistent through the API."""
		players = [
			("PLAYER-TEST-DENSE-A", 500),
			("PLAYER-TEST-DENSE-B", 500),
			("PLAYER-TEST-DENSE-C", 300),
		]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-DENSE-A", self.PLAN,
			"/api/v1/leaderboard/daily",
		)
		data = resp.json()
		ranks = {e["player_id"]: e["rank"] for e in data["entries"]}

		assert ranks["PLAYER-TEST-DENSE-A"] == 1
		assert ranks["PLAYER-TEST-DENSE-B"] == 1
		assert ranks["PLAYER-TEST-DENSE-C"] == 2


@pytest.mark.asyncio
class TestAPIPagination:
	"""Pagination through the API layer."""

	PLAN = "PLAN-TEST-APIPAG"

	async def test_limit_respected(self, app_client, redis_client, mock_frappe):
		"""?limit=5 returns exactly 5 entries."""
		players = [(f"PLAYER-TEST-APIPAG-{i:03d}", (20 - i) * 10) for i in range(20)]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, players[0][0], self.PLAN,
			"/api/v1/leaderboard/daily?limit=5",
		)
		data = resp.json()
		assert len(data["entries"]) == 5
		assert data["total_players"] == 20

	async def test_offset_with_limit(self, app_client, redis_client, mock_frappe):
		"""?offset=5&limit=5 returns entries 5-9."""
		players = [(f"PLAYER-TEST-APIPAG-{i:03d}", (20 - i) * 10) for i in range(20)]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, players[0][0], self.PLAN,
			"/api/v1/leaderboard/daily?offset=5&limit=5",
		)
		data = resp.json()
		assert len(data["entries"]) == 5
		# XP should be lower than first page
		assert data["entries"][0]["xp"] <= 150  # position 5: 200 - 5*10

	async def test_offset_beyond_returns_empty(self, app_client, redis_client, mock_frappe):
		"""?offset=100 with 10 players returns empty entries, correct total."""
		players = [(f"PLAYER-TEST-APIPAG-{i:03d}", (10 - i) * 10) for i in range(10)]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, players[0][0], self.PLAN,
			"/api/v1/leaderboard/daily?offset=100",
		)
		data = resp.json()
		assert data["entries"] == []
		assert data["total_players"] == 10

	async def test_paginated_ranks_are_absolute(self, app_client, redis_client, mock_frappe):
		"""Ranks in page 2 are absolute (not reset from 1)."""
		players = [
			("PLAYER-TEST-APIPAG-A1", 500),
			("PLAYER-TEST-APIPAG-B1", 400),
			("PLAYER-TEST-APIPAG-C1", 300),
			("PLAYER-TEST-APIPAG-D1", 200),
			("PLAYER-TEST-APIPAG-E1", 100),
		]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		resp = await _authed_request(
			app_client, redis_client, players[0][0], self.PLAN,
			"/api/v1/leaderboard/daily?offset=2&limit=3",
		)
		data = resp.json()

		# Offset=2 skips A1 (rank 1) and B1 (rank 2)
		# Returns C1 (rank 3), D1 (rank 4), E1 (rank 5)
		ranks = [e["rank"] for e in data["entries"]]
		assert ranks == [3, 4, 5]


@pytest.mark.asyncio
class TestAPIPlanIsolation:
	"""Plan isolation through the API layer."""

	PLAN_A = "PLAN-TEST-ISO-A"
	PLAN_B = "PLAN-TEST-ISO-B"

	async def test_different_plans_see_different_data(self, app_client, redis_client, mock_frappe):
		"""Players in Plan A don't see Plan B players."""
		plan_a_players = [
			("PLAYER-TEST-ISO-A1", 500),
			("PLAYER-TEST-ISO-A2", 300),
		]
		plan_b_players = [
			("PLAYER-TEST-ISO-B1", 1000),
			("PLAYER-TEST-ISO-B2", 800),
		]
		await _seed_plan_leaderboard(redis_client, self.PLAN_A, plan_a_players, lb_type="daily")
		await _seed_plan_leaderboard(redis_client, self.PLAN_B, plan_b_players, lb_type="daily")

		# Plan A player sees only Plan A
		resp_a = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-ISO-A1", self.PLAN_A,
			"/api/v1/leaderboard/daily",
		)
		data_a = resp_a.json()
		a_ids = {e["player_id"] for e in data_a["entries"]}
		assert "PLAYER-TEST-ISO-A1" in a_ids
		assert "PLAYER-TEST-ISO-A2" in a_ids
		assert "PLAYER-TEST-ISO-B1" not in a_ids

		# Plan B player sees only Plan B
		resp_b = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-ISO-B1", self.PLAN_B,
			"/api/v1/leaderboard/daily",
		)
		data_b = resp_b.json()
		b_ids = {e["player_id"] for e in data_b["entries"]}
		assert "PLAYER-TEST-ISO-B1" in b_ids
		assert "PLAYER-TEST-ISO-A1" not in b_ids


@pytest.mark.asyncio
class TestAPISubjectFilter:
	"""Subject filter through the API layer."""

	PLAN = "PLAN-TEST-SUBJ-API"

	async def test_subject_filter_returns_subject_data(self, app_client, redis_client, mock_frappe):
		"""?subject_id filters to subject-specific leaderboard."""
		# Seed subject-specific leaderboard
		friday = _current_friday()
		key = lb_weekly_plan_key(friday, self.PLAN, "SUBJ-API-001")
		await redis_client.zadd(key, {"PLAYER-TEST-SUBJ-A": 500, "PLAYER-TEST-SUBJ-B": 300})
		await redis_client.expire(key, 3600)

		# Also seed non-subject leaderboard with different players
		key_global = lb_weekly_plan_key(friday, self.PLAN)
		await redis_client.zadd(key_global, {"PLAYER-TEST-SUBJ-C": 1000})
		await redis_client.expire(key_global, 3600)

		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-SUBJ-A", self.PLAN,
			"/api/v1/leaderboard/weekly?subject_id=SUBJ-API-001",
		)
		data = resp.json()
		assert data["subject_id"] == "SUBJ-API-001"
		assert data["total_players"] == 2
		player_ids = {e["player_id"] for e in data["entries"]}
		assert "PLAYER-TEST-SUBJ-A" in player_ids
		assert "PLAYER-TEST-SUBJ-B" in player_ids
		# Player C is NOT in subject-scoped leaderboard
		assert "PLAYER-TEST-SUBJ-C" not in player_ids


@pytest.mark.asyncio
class TestAPIEdgeCases:
	"""API-level edge cases."""

	PLAN = "PLAN-TEST-APIEDGE"

	async def test_empty_leaderboard_200(self, app_client, redis_client, mock_frappe):
		"""Empty leaderboard returns 200 with empty entries."""
		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-EDGE-001", self.PLAN,
			"/api/v1/leaderboard/daily",
		)
		assert resp.status_code == 200
		data = resp.json()
		assert data["entries"] == []
		assert data["total_players"] == 0

	async def test_single_player_leaderboard(self, app_client, redis_client, mock_frappe):
		"""Single player = rank 1 in both top and me."""
		players = [("PLAYER-TEST-EDGE-SOLO", 100)]
		await _seed_plan_leaderboard(redis_client, self.PLAN, players, lb_type="daily")

		# Top
		resp_top = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-EDGE-SOLO", self.PLAN,
			"/api/v1/leaderboard/daily",
		)
		data_top = resp_top.json()
		assert len(data_top["entries"]) == 1
		assert data_top["entries"][0]["rank"] == 1
		assert data_top["entries"][0]["is_me"] is True

		# Me
		resp_me = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-EDGE-SOLO", self.PLAN,
			"/api/v1/leaderboard/daily/me",
		)
		data_me = resp_me.json()
		assert data_me["rank"] == 1
		assert data_me["xp_to_next"] is None
		assert data_me["total_players"] == 1

	async def test_limit_max_100(self, app_client, redis_client, mock_frappe):
		"""?limit=200 is clamped/rejected (FastAPI validation: le=100)."""
		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-EDGE-LIM", self.PLAN,
			"/api/v1/leaderboard/daily?limit=200",
		)
		assert resp.status_code == 422  # Validation error

	async def test_offset_max_1000(self, app_client, redis_client, mock_frappe):
		"""?offset=2000 is clamped/rejected (FastAPI validation: le=1000)."""
		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-EDGE-OFF", self.PLAN,
			"/api/v1/leaderboard/daily?offset=2000",
		)
		assert resp.status_code == 422

	async def test_negative_limit_rejected(self, app_client, redis_client, mock_frappe):
		"""?limit=-1 is rejected."""
		resp = await _authed_request(
			app_client, redis_client, "PLAYER-TEST-EDGE-NEG", self.PLAN,
			"/api/v1/leaderboard/daily?limit=-1",
		)
		assert resp.status_code == 422
