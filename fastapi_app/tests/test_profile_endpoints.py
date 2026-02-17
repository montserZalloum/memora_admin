"""Tests for profile endpoints.

Tests verify all profile endpoints:
- GET /api/v1/profile - Get hero profile section
- GET /api/v1/profile/stats - Get profile stats
- PUT /api/v1/profile/avatar - Update player avatar
- POST /api/v1/profile/logout - Player logout

Reference: contracts/endpoint-test-contracts.md §4
"""
import pytest


@pytest.mark.asyncio
class TestProfileHero:
	"""Profile hero section tests."""

	async def test_get_hero_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player gets hero profile section."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock ProfilePageService.get_hero()
			mock_frappe.call.return_value = {
				"display_name": "Ahmed",
				"avatar": "avatar_01",
				"level": 5,
				"level_title": "Explorer",
				"current_xp": 500,
				"xp_in_level": 200,
				"xp_for_next_level": 100,
			}

			resp = await client.get("/api/v1/profile")

			assert resp.status_code == 200
			data = resp.json()
			assert data["display_name"] == "Ahmed"
			assert data["avatar"] == "avatar_01"
			assert data["level"] == 5
			assert "current_xp" in data
		finally:
			pass


@pytest.mark.asyncio
class TestProfileStats:
	"""Profile stats tests."""

	async def test_get_stats_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player gets profile stats."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock ProfilePageService.get_stats()
			mock_frappe.call.return_value = {
				"streak": 7,
				"items_learned": 42,
				"total_xp": 5000,
				"rank": 123,
			}

			resp = await client.get("/api/v1/profile/stats")

			assert resp.status_code == 200
			data = resp.json()
			assert data["streak"] == 7
			assert data["items_learned"] == 42
			assert "total_xp" in data
		finally:
			pass


@pytest.mark.asyncio
class TestProfileAvatar:
	"""Profile avatar update tests."""

	async def test_update_avatar_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player successfully updates avatar."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock ProfilePageService.update_avatar()
			mock_frappe.call.return_value = {
				"avatar": "avatar_02",
				"success": True,
			}

			resp = await client.put(
				"/api/v1/profile/avatar",
				json={"avatar": "avatar_02"},
			)

			assert resp.status_code == 200
			data = resp.json()
			assert data["avatar"] == "avatar_02"
			assert data["success"] is True
		finally:
			pass

	async def test_update_avatar_invalid_400(self, authed_client, redis_client, mock_frappe):
		"""Invalid avatar ID returns 400."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock raises error for invalid avatar
			mock_frappe.call.side_effect = ValueError("Invalid avatar")

			resp = await client.put(
				"/api/v1/profile/avatar",
				json={"avatar": "INVALID_AVATAR"},
			)

			assert resp.status_code == 400
		finally:
			mock_frappe.call.side_effect = None


@pytest.mark.asyncio
class TestProfileLogout:
	"""Profile logout tests."""

	async def test_logout_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player successfully logs out."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock ProfilePageService.logout()
			mock_frappe.call.return_value = {
				"success": True,
			}

			resp = await client.post("/api/v1/profile/logout")

			assert resp.status_code == 200
			data = resp.json()
			assert data["success"] is True
		finally:
			pass


@pytest.mark.asyncio
class TestProfileAuth:
	"""Profile authentication tests."""

	async def test_profile_unauthenticated_401(self, app_client):
		"""Unauthenticated request returns 401."""
		resp = await app_client.get("/api/v1/profile")

		assert resp.status_code == 401
