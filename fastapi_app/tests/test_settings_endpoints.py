"""Tests for settings endpoints.

Tests verify settings endpoint:
- GET /api/v1/settings/gamification - Get gamification settings (public endpoint)

Reference: contracts/endpoint-test-contracts.md §7
"""

import pytest


@pytest.mark.asyncio
class TestSettingsEndpoints:
	"""Settings retrieval tests (public endpoint)."""

	async def test_settings_gamification_success(self, app_client, mock_frappe):
		"""Public request returns gamification settings."""
		try:
			# Mock SettingsService.get_gamification_settings()
			mock_frappe.call.return_value = {
				"base_lesson_xp": 100,
				"replay_xp": 25,
				"achievement_threshold": 1000,
			}

			resp = await app_client.get("/api/v1/settings/gamification")

			assert resp.status_code == 200
			data = resp.json()
			assert data["base_lesson_xp"] == 100
			assert data["replay_xp"] == 25
		finally:
			pass

	async def test_settings_gamification_public_no_auth(self, app_client, mock_frappe):
		"""Settings endpoint is public - no auth required."""
		try:
			# Mock settings response
			mock_frappe.call.return_value = {
				"base_lesson_xp": 100,
				"replay_xp": 25,
			}

			# No Authorization header - should succeed because endpoint is public
			resp = await app_client.get("/api/v1/settings/gamification")

			assert resp.status_code == 200  # Not 401
			data = resp.json()
			assert "base_lesson_xp" in data
		finally:
			pass
