"""Tests for ProfilePageService aggregation methods."""

from datetime import datetime, timedelta

import pytest

from fastapi_app.core.redis_keys import daily_xp_key, lb_archive_daily_key
from fastapi_app.services.profile_page import AMMAN_TZ, ProfilePageService

pytestmark = pytest.mark.asyncio


class TestWeeklyActivity:
	"""Weekly activity service tests."""

	async def test_get_weekly_activity_empty_cache_returns_zeroed_week(self, redis_client, mock_frappe):
		"""Empty Redis state should still return a complete 7-day response."""
		service = ProfilePageService(redis_client, mock_frappe)

		result = await service.get_weekly_activity("PLAYER-TEST-001")

		assert result["subject"] is None
		assert len(result["days"]) == 7
		assert result["total_xp"] == 0
		assert all(day["xp"] == 0 for day in result["days"])

	async def test_get_weekly_activity_subject_does_not_backfill_today_from_daily_xp(
		self, redis_client, mock_frappe
	):
		"""Today's subject activity must not use global daily_xp fallback data."""
		service = ProfilePageService(redis_client, mock_frappe)
		player_id = "PLAYER-TEST-002"
		subject_id = "SUB-TEST-001"
		today_str = datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")

		await redis_client.hset(daily_xp_key(player_id), today_str, 123)

		result = await service.get_weekly_activity(player_id, subject_id=subject_id)
		today_entry = next(day for day in result["days"] if day["date"] == today_str)

		assert today_entry["xp"] == 0
		assert result["total_xp"] == 0
		mock_frappe.call.assert_not_called()

	async def test_get_weekly_activity_uses_archive_for_missing_past_day(self, redis_client, mock_frappe):
		"""Past-day archive scores should populate when the primary daily key is missing."""
		service = ProfilePageService(redis_client, mock_frappe)
		player_id = "PLAYER-TEST-003"
		yesterday_str = (datetime.now(AMMAN_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")

		await redis_client.zadd(lb_archive_daily_key(yesterday_str, None), {player_id: 42})

		result = await service.get_weekly_activity(player_id)
		yesterday_entry = next(day for day in result["days"] if day["date"] == yesterday_str)

		assert yesterday_entry["xp"] == 42
		assert result["total_xp"] == 42
