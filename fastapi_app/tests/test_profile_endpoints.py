"""Tests for profile endpoints.

Tests verify all profile endpoints:
- GET /api/v1/profile - Get hero profile section
- GET /api/v1/profile/stats - Get profile stats
- PUT /api/v1/profile/avatar - Update player avatar
- POST /api/v1/profile/logout - Player logout

Reference: contracts/endpoint-test-contracts.md §4
"""
from unittest.mock import AsyncMock, patch

import pytest

from fastapi_app.services.profile_page import ProfilePageService


@pytest.mark.asyncio
class TestProfileHero:
	"""Profile hero section tests."""

	async def test_get_hero_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player gets hero profile section."""
		client, token, player_id, family_id = authed_client

		with patch.object(ProfilePageService, "get_hero", new_callable=AsyncMock) as mock_hero:
			mock_hero.return_value = {
				"display_name": "Ahmed",
				"avatar": "avatar_01",
				"level": 5,
				"level_title": "Explorer",
				"current_xp": 500,
				"xp_in_level": 200,
				"xp_for_next_level": 100,
				"xp_level_start": 300,
				"xp_level_end": 600,
			}

			resp = await client.get("/api/v1/profile")

			assert resp.status_code == 200
			data = resp.json()
			assert data["display_name"] == "Ahmed"
			assert data["avatar"] == "avatar_01"
			assert data["level"] == 5
			assert "current_xp" in data


@pytest.mark.asyncio
class TestProfileStats:
	"""Profile stats tests."""

	async def test_get_stats_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player gets profile stats."""
		client, token, player_id, family_id = authed_client

		with patch.object(ProfilePageService, "get_stats", new_callable=AsyncMock) as mock_stats:
			mock_stats.return_value = {
				"subject": None,
				"streak": 7,
				"items_learned": 42,
				"total_xp": 5000,
			}

			resp = await client.get("/api/v1/profile/stats")

			assert resp.status_code == 200
			data = resp.json()
			assert data["streak"] == 7
			assert data["items_learned"] == 42
			assert "total_xp" in data


@pytest.mark.asyncio
class TestProfileAvatar:
	"""Profile avatar update tests."""

	async def test_update_avatar_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player successfully updates avatar."""
		client, token, player_id, family_id = authed_client

		with patch.object(ProfilePageService, "update_avatar", new_callable=AsyncMock) as mock_update:
			mock_update.return_value = {
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

	async def test_update_avatar_invalid_400(self, authed_client, redis_client, mock_frappe):
		"""Invalid avatar ID returns 400."""
		client, token, player_id, family_id = authed_client

		from fastapi_app.services.frappe_client import FrappeAPIError

		with patch.object(ProfilePageService, "update_avatar", new_callable=AsyncMock) as mock_update:
			mock_update.side_effect = FrappeAPIError(400, "Invalid avatar")

			resp = await client.put(
				"/api/v1/profile/avatar",
				json={"avatar": "INVALID_AVATAR"},
			)

			assert resp.status_code == 400


@pytest.mark.asyncio
class TestProfileLogout:
	"""Profile logout tests."""

	async def test_logout_success(self, authed_client, redis_client, mock_frappe):
		"""Authenticated player successfully logs out."""
		client, token, player_id, family_id = authed_client

		with patch.object(ProfilePageService, "logout", new_callable=AsyncMock) as mock_logout:
			mock_logout.return_value = {
				"success": True,
			}

			resp = await client.post("/api/v1/profile/logout")

			assert resp.status_code == 200
			data = resp.json()
			assert data["success"] is True


@pytest.mark.asyncio
class TestProfileAuth:
	"""Profile authentication tests."""

	async def test_profile_unauthenticated_401(self, app_client):
		"""Unauthenticated request returns 401."""
		resp = await app_client.get("/api/v1/profile")

		assert resp.status_code == 401
