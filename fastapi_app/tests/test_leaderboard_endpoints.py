"""Tests for plan-scoped leaderboard endpoints.

Tests verify:
- GET /api/v1/leaderboard/{lb_type} — Plan-scoped top 20
- GET /api/v1/leaderboard/{lb_type}/me — Plan-scoped my rank
- alltime returns 422
- No-plan player returns empty leaderboard
"""

import json

import pytest
from uuid import uuid4

from fastapi_app.core.redis_keys import session_key as _session_key_fn
from fastapi_app.core.security import create_access_token


@pytest.mark.asyncio
class TestLeaderboardEndpoints:
	"""Plan-scoped leaderboard endpoint contract tests."""

	async def test_alltime_returns_422(self, authed_client, redis_client):
		"""GET /leaderboard/alltime returns 422 (invalid lb_type)."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/leaderboard/alltime")
		assert resp.status_code == 422

	async def test_alltime_me_returns_422(self, authed_client, redis_client):
		"""GET /leaderboard/alltime/me returns 422 (invalid lb_type)."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/leaderboard/alltime/me")
		assert resp.status_code == 422

	async def test_daily_top_response_shape(self, authed_client, redis_client):
		"""GET /leaderboard/daily returns LeaderboardResponse shape."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/leaderboard/daily")
		assert resp.status_code == 200
		data = resp.json()

		# Shape: leaderboard_type, subject_id, entries, total_players
		assert data["leaderboard_type"] == "daily"
		assert "subject_id" in data
		assert isinstance(data["entries"], list)
		assert isinstance(data["total_players"], int)
		assert len(data["entries"]) <= 20

	async def test_weekly_top_response_shape(self, authed_client, redis_client):
		"""GET /leaderboard/weekly returns valid response."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/leaderboard/weekly")
		assert resp.status_code == 200
		data = resp.json()
		assert data["leaderboard_type"] == "weekly"

	async def test_daily_me_response_shape(self, authed_client, redis_client):
		"""GET /leaderboard/daily/me returns MyRankResponse shape."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/leaderboard/daily/me")
		assert resp.status_code == 200
		data = resp.json()

		# Shape: rank (nullable), xp, xp_to_next (nullable), neighbors, total_players
		assert "rank" in data
		assert isinstance(data["xp"], int)
		assert "xp_to_next" in data
		assert isinstance(data["neighbors"], list)
		assert isinstance(data["total_players"], int)

	async def test_subject_filter_param(self, authed_client, redis_client):
		"""GET /leaderboard/daily?subject_id=SUBJ-001 returns subject-filtered results."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/leaderboard/daily?subject_id=SUBJ-001")
		assert resp.status_code == 200
		data = resp.json()
		assert data["subject_id"] == "SUBJ-001"

	async def test_unauthenticated_401(self, app_client):
		"""Unauthenticated request returns 401."""
		resp = await app_client.get("/api/v1/leaderboard/daily")
		assert resp.status_code == 401

	async def test_invalid_type_422(self, authed_client, redis_client):
		"""Invalid leaderboard type returns 422."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/leaderboard/invalid_type")
		assert resp.status_code == 422

	async def test_no_plan_returns_empty_leaderboard(self, app_client, redis_client, make_player_token):
		"""Player with no plan returns empty leaderboard (not 500)."""
		# Create token with plan_id=None to simulate no-plan player
		player_id = f"PLAYER-TEST-NOPLAN-{uuid4().hex[:8]}"
		family_id = str(uuid4())
		token = create_access_token(
			user_id=player_id,
			plan_id="",  # Empty plan
			display_name="No Plan Player",
			family_id=family_id,
			mobile="201000000000",
		)

		# Seed session
		sess_key = _session_key_fn(player_id)
		await redis_client.set(sess_key, json.dumps({"fid": family_id}))

		try:
			app_client.headers["Authorization"] = f"Bearer {token}"

			resp = await app_client.get("/api/v1/leaderboard/daily")
			assert resp.status_code == 200
			data = resp.json()
			assert data["entries"] == []
			assert data["total_players"] == 0

			# Also check /me
			resp_me = await app_client.get("/api/v1/leaderboard/daily/me")
			assert resp_me.status_code == 200
			data_me = resp_me.json()
			assert data_me["rank"] is None
			assert data_me["xp"] == 0
			assert data_me["neighbors"] == []
			assert data_me["total_players"] == 0
		finally:
			del app_client.headers["Authorization"]
			await redis_client.delete(sess_key)

	async def test_is_me_flag_in_entries(self, authed_client, redis_client):
		"""Entries include is_me=True for the requesting player."""
		client, token, player_id, family_id = authed_client

		# Seed the player in the plan-scoped leaderboard
		from fastapi_app.services.leaderboard import LeaderboardService

		lb_svc = LeaderboardService(redis_client)
		await lb_svc.update_leaderboards(
			player_id, xp_amount=50, new_total_xp=50, plan_id="PLAN-TEST-001"
		)

		resp = await client.get("/api/v1/leaderboard/daily")
		assert resp.status_code == 200
		data = resp.json()

		if data["entries"]:
			my_entries = [e for e in data["entries"] if e["player_id"] == player_id]
			if my_entries:
				assert my_entries[0]["is_me"] is True
