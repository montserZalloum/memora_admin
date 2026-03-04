"""Tests for leaderboard endpoint fixes.

Verifies:
1. limit/offset query params are respected
2. Dense ranking (1,1,2) not competition ranking (1,1,3)
3. Full trace of weekly endpoint with pagination and ties
"""

import json
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from fastapi_app.core.redis_keys import (
	lb_weekly_plan_key,
)
from fastapi_app.core.redis_keys import (
	session_key as _session_key_fn,
)
from fastapi_app.core.security import create_access_token

AMMAN_TZ = ZoneInfo("Asia/Amman")


def _current_friday() -> str:
	"""Get the Friday date string for the current Islamic week."""
	now = datetime.now(AMMAN_TZ)
	weekday = now.isoweekday()
	days_since_friday = (weekday - 5) % 7
	return (now - timedelta(days=days_since_friday)).strftime("%Y-%m-%d")


async def _seed_leaderboard_players(redis_client, plan_id: str, players: list[tuple[str, int]]):
	"""Seed multiple players into the weekly plan-scoped leaderboard."""
	friday = _current_friday()
	key = lb_weekly_plan_key(friday, plan_id)

	pipe = redis_client.pipeline()
	for player_id, xp in players:
		pipe.zadd(key, {player_id: xp})
	pipe.expire(key, 3600)
	await pipe.execute()


async def _make_authed_request(app_client, redis_client, player_id, plan_id, path):
	"""Create an authenticated request and return the response."""
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
		resp = await app_client.get(path)
		return resp
	finally:
		del app_client.headers["Authorization"]
		await redis_client.delete(sess_key)


@pytest.mark.asyncio
class TestLeaderboardFixes:
	"""Tests verifying leaderboard bug fixes."""

	PLAN_ID = "PLAN-TEST-LB-BUGS"

	async def _cleanup_lb_keys(self, redis_client):
		friday = _current_friday()
		keys_to_clean = [
			lb_weekly_plan_key(friday, self.PLAN_ID),
			lb_weekly_plan_key(friday, self.PLAN_ID, "SUBJ-TEST-001"),
		]
		for key in keys_to_clean:
			await redis_client.delete(key)

	# ------------------------------------------------------------------ #
	# Fix 1: limit and offset are now respected
	# ------------------------------------------------------------------ #

	async def test_limit_param_respected(self, app_client, redis_client, mock_frappe):
		"""?limit=3 returns exactly 3 entries."""
		players = [(f"PLAYER-TEST-LB-{i:03d}", (10 - i) * 10) for i in range(10)]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			resp = await _make_authed_request(
				app_client,
				redis_client,
				players[0][0],
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly?limit=3",
			)
			assert resp.status_code == 200
			data = resp.json()
			assert len(data["entries"]) == 3
			# First 3 should be highest XP players
			xps = [e["xp"] for e in data["entries"]]
			assert xps == [100, 90, 80]
		finally:
			await self._cleanup_lb_keys(redis_client)

	async def test_offset_param_respected(self, app_client, redis_client, mock_frappe):
		"""?offset=3&limit=3 skips first 3 entries."""
		players = [(f"PLAYER-TEST-LB-{i:03d}", (10 - i) * 10) for i in range(10)]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			resp = await _make_authed_request(
				app_client,
				redis_client,
				players[0][0],
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly?limit=3&offset=3",
			)
			assert resp.status_code == 200
			data = resp.json()
			assert len(data["entries"]) == 3
			# Should get players at positions 3, 4, 5 (0-indexed)
			xps = [e["xp"] for e in data["entries"]]
			assert xps == [70, 60, 50]
		finally:
			await self._cleanup_lb_keys(redis_client)

	async def test_offset_beyond_data_returns_empty(self, app_client, redis_client, mock_frappe):
		"""?offset=100 with only 5 players returns empty entries."""
		players = [(f"PLAYER-TEST-LB-{i:03d}", (5 - i) * 10) for i in range(5)]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			resp = await _make_authed_request(
				app_client,
				redis_client,
				players[0][0],
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly?offset=100",
			)
			assert resp.status_code == 200
			data = resp.json()
			assert len(data["entries"]) == 0
			# total_players should still reflect full count
			assert data["total_players"] == 5
		finally:
			await self._cleanup_lb_keys(redis_client)

	async def test_default_limit_is_20(self, app_client, redis_client, mock_frappe):
		"""No limit param defaults to 20 entries."""
		players = [(f"PLAYER-TEST-LB-{i:03d}", (30 - i) * 10) for i in range(25)]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			resp = await _make_authed_request(
				app_client,
				redis_client,
				players[0][0],
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly",
			)
			assert resp.status_code == 200
			data = resp.json()
			assert len(data["entries"]) == 20
		finally:
			await self._cleanup_lb_keys(redis_client)

	# ------------------------------------------------------------------ #
	# Fix 2: Dense ranking (1,1,2) instead of competition (1,1,3)
	# ------------------------------------------------------------------ #

	async def test_dense_ranking_in_get_top(self, app_client, redis_client, mock_frappe):
		"""Tied players share rank, next rank increments by 1 (dense)."""
		players = [
			("PLAYER-TEST-LB-AAA", 100),
			("PLAYER-TEST-LB-BBB", 100),
			("PLAYER-TEST-LB-CCC", 50),
		]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			resp = await _make_authed_request(
				app_client,
				redis_client,
				players[0][0],
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly",
			)
			assert resp.status_code == 200
			data = resp.json()

			ranks = {e["player_id"]: e["rank"] for e in data["entries"]}

			# Dense ranking: 1, 1, 2 (not competition 1, 1, 3)
			assert ranks["PLAYER-TEST-LB-AAA"] == 1
			assert ranks["PLAYER-TEST-LB-BBB"] == 1
			assert ranks["PLAYER-TEST-LB-CCC"] == 2
		finally:
			await self._cleanup_lb_keys(redis_client)

	async def test_dense_ranking_multiple_ties(self, app_client, redis_client, mock_frappe):
		"""Multiple tie groups produce correct dense ranks."""
		players = [
			("PLAYER-TEST-LB-A1", 500),
			("PLAYER-TEST-LB-B1", 300),
			("PLAYER-TEST-LB-B2", 300),
			("PLAYER-TEST-LB-C1", 100),
			("PLAYER-TEST-LB-C2", 100),
			("PLAYER-TEST-LB-D1", 50),
		]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			resp = await _make_authed_request(
				app_client,
				redis_client,
				players[0][0],
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly",
			)
			assert resp.status_code == 200
			data = resp.json()

			ranks = [e["rank"] for e in data["entries"]]
			# Dense: [1, 2, 2, 3, 3, 4]
			assert ranks == [1, 2, 2, 3, 3, 4]
		finally:
			await self._cleanup_lb_keys(redis_client)

	async def test_dense_ranking_with_offset(self, app_client, redis_client, mock_frappe):
		"""Dense ranks are absolute (not reset) when using offset."""
		players = [
			("PLAYER-TEST-LB-A1", 500),
			("PLAYER-TEST-LB-B1", 300),
			("PLAYER-TEST-LB-B2", 300),
			("PLAYER-TEST-LB-C1", 100),
		]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			resp = await _make_authed_request(
				app_client,
				redis_client,
				players[0][0],
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly?offset=2&limit=2",
			)
			assert resp.status_code == 200
			data = resp.json()

			# offset=2 skips A1 and B1, returns B2 (rank 2) and C1 (rank 3)
			assert len(data["entries"]) == 2
			assert data["entries"][0]["rank"] == 2  # B2 (tied with B1)
			assert data["entries"][1]["rank"] == 3  # C1
		finally:
			await self._cleanup_lb_keys(redis_client)

	async def test_dense_ranking_in_my_rank(self, app_client, redis_client, mock_frappe):
		"""GET /me uses same dense ranking as GET /{type}."""
		players = [
			("PLAYER-TEST-LB-001", 500),
			("PLAYER-TEST-LB-002", 300),
			("PLAYER-TEST-LB-003", 300),
			("PLAYER-TEST-LB-ME1", 150),
			("PLAYER-TEST-LB-005", 50),
		]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			resp = await _make_authed_request(
				app_client,
				redis_client,
				"PLAYER-TEST-LB-ME1",
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly/me",
			)
			assert resp.status_code == 200
			data = resp.json()

			# Dense rank: {500}=1, {300}=2, {150}=3, {50}=4
			# 2 distinct tiers above 150: {500, 300}
			assert data["rank"] == 3
			assert data["xp"] == 150
			assert data["xp_to_next"] == 150  # 300 - 150

			# Verify neighbor ranks are also dense
			neighbor_ranks = {n["player_id"]: n["rank"] for n in data["neighbors"]}
			assert neighbor_ranks["PLAYER-TEST-LB-002"] == 2
			assert neighbor_ranks["PLAYER-TEST-LB-003"] == 2  # tied
			assert neighbor_ranks["PLAYER-TEST-LB-ME1"] == 3
			assert neighbor_ranks["PLAYER-TEST-LB-005"] == 4
		finally:
			await self._cleanup_lb_keys(redis_client)

	async def test_ranking_consistency_top_vs_me(self, app_client, redis_client, mock_frappe):
		"""get_top and get_my_rank produce identical ranks for same data."""
		players = [
			("PLAYER-TEST-LB-A", 500),
			("PLAYER-TEST-LB-B", 300),
			("PLAYER-TEST-LB-C", 300),
			("PLAYER-TEST-LB-D", 100),
		]
		await _seed_leaderboard_players(redis_client, self.PLAN_ID, players)

		try:
			# Get top leaderboard
			resp_top = await _make_authed_request(
				app_client,
				redis_client,
				"PLAYER-TEST-LB-D",
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly",
			)
			# Get my rank (as player D)
			resp_me = await _make_authed_request(
				app_client,
				redis_client,
				"PLAYER-TEST-LB-D",
				self.PLAN_ID,
				"/api/v1/leaderboard/weekly/me",
			)

			top_data = resp_top.json()
			me_data = resp_me.json()

			# Player D's rank should match in both endpoints
			top_rank_d = next(e["rank"] for e in top_data["entries"] if e["player_id"] == "PLAYER-TEST-LB-D")
			assert top_rank_d == me_data["rank"]

			# Dense: A=1, B=2, C=2, D=3
			assert me_data["rank"] == 3
		finally:
			await self._cleanup_lb_keys(redis_client)
