"""Tests for leaderboard endpoints.

Tests verify leaderboard endpoints:
- GET /api/v1/leaderboard/{lb_type} - Get top players
- GET /api/v1/leaderboard/{lb_type}/me - Get player's rank

Reference: contracts/endpoint-test-contracts.md §5
"""
import pytest


@pytest.mark.asyncio
class TestLeaderboardEndpoints:
	"""Leaderboard data retrieval tests."""

	async def test_leaderboard_top_list(self, authed_client, redis_client, mock_frappe):
		"""Get top players on daily leaderboard."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock LeaderboardService.get_top()
			mock_frappe.call.return_value = {
				"entries": [
					{
						"rank": 1,
						"player_id": "PLAYER-TOP-1",
						"display_name": "Champion",
						"xp": 1500,
						"avatar": "avatar_03",
						"is_me": False,
					},
					{
						"rank": 2,
						"player_id": player_id,
						"display_name": "Test Player",
						"xp": 1200,
						"avatar": "avatar_01",
						"is_me": True,
					},
				],
				"total_players": 5000,
			}

			resp = await client.get("/api/v1/leaderboard/daily?limit=10")

			assert resp.status_code == 200
			data = resp.json()
			assert "entries" in data
			assert len(data["entries"]) == 2
			assert data["entries"][0]["rank"] == 1
			assert "total_players" in data
			assert data["total_players"] == 5000
		finally:
			pass

	async def test_leaderboard_my_rank(self, authed_client, redis_client, mock_frappe):
		"""Get player's rank on leaderboard."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock LeaderboardService.get_my_rank()
			mock_frappe.call.return_value = {
				"rank": 42,
				"xp": 800,
				"neighbors": [
					{
						"rank": 41,
						"player_id": "PLAYER-ABOVE",
						"display_name": "Player Above",
						"xp": 850,
					},
					{
						"rank": 43,
						"player_id": "PLAYER-BELOW",
						"display_name": "Player Below",
						"xp": 750,
					},
				],
			}

			resp = await client.get("/api/v1/leaderboard/daily/me")

			assert resp.status_code == 200
			data = resp.json()
			assert data["rank"] == 42
			assert data["xp"] == 800
			assert "neighbors" in data
			assert len(data["neighbors"]) == 2
		finally:
			pass

	async def test_leaderboard_empty(self, authed_client, redis_client, mock_frappe):
		"""Empty leaderboard returns empty entries array."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock empty leaderboard
			mock_frappe.call.return_value = {
				"entries": [],
				"total_players": 0,
			}

			resp = await client.get("/api/v1/leaderboard/weekly")

			assert resp.status_code == 200
			data = resp.json()
			assert data["entries"] == []
		finally:
			pass

	async def test_leaderboard_invalid_type_422(self, authed_client, redis_client):
		"""Invalid leaderboard type returns 422."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/leaderboard/invalid_type")

		assert resp.status_code == 422

	async def test_leaderboard_unauthenticated_401(self, app_client):
		"""Unauthenticated request returns 401."""
		resp = await app_client.get("/api/v1/leaderboard/daily")

		assert resp.status_code == 401
