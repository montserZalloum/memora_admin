"""Tests for ProfilePageService aggregation methods."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

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

		assert len(result["days"]) == 7
		assert result["total_xp"] == 0
		assert all(day["xp"] == 0 for day in result["days"])

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

	async def test_get_weekly_activity_partial_hash_hydrates_from_mariadb(self, redis_client, mock_frappe):
		"""Sparse daily_xp hash (e.g. after Redis eviction + new XP today) should backfill from MariaDB."""
		service = ProfilePageService(redis_client, mock_frappe)
		player_id = "PLAYER-TEST-004"
		today_str = datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")
		two_days_ago_str = (datetime.now(AMMAN_TZ) - timedelta(days=2)).strftime("%Y-%m-%d")
		three_days_ago_str = (datetime.now(AMMAN_TZ) - timedelta(days=3)).strftime("%Y-%m-%d")

		# Simulate partial hash: only today's entry exists (post-eviction + new XP)
		await redis_client.hset(daily_xp_key(player_id), today_str, 50)

		# MariaDB has historical data
		mariadb_json = json.dumps(
			{
				two_days_ago_str: 100,
				three_days_ago_str: 75,
				today_str: 30,  # stale — Redis value (50) should win
			}
		)
		mock_frappe.call = AsyncMock(return_value={"daily_xp_json": mariadb_json})

		result = await service.get_weekly_activity(player_id)

		two_days_ago_entry = next(d for d in result["days"] if d["date"] == two_days_ago_str)
		three_days_ago_entry = next(d for d in result["days"] if d["date"] == three_days_ago_str)

		# Historical days backfilled from MariaDB
		assert two_days_ago_entry["xp"] == 100
		assert three_days_ago_entry["xp"] == 75
		# Hash merge should preserve Redis value (50) over MariaDB stale (30)
		hash_val = await redis_client.hget(daily_xp_key(player_id), today_str)
		assert hash_val == "50"  # HSETNX preserved the Redis value

	async def test_get_weekly_activity_full_hash_skips_mariadb(self, redis_client, mock_frappe):
		"""When daily_xp hash has all needed dates, MariaDB should not be called."""
		service = ProfilePageService(redis_client, mock_frappe)
		player_id = "PLAYER-TEST-005"

		# Populate hash with all 6 past days (today is handled by daily ZSET, not hash)
		for i in range(1, 7):
			date_str = (datetime.now(AMMAN_TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
			await redis_client.hset(daily_xp_key(player_id), date_str, i * 10)

		result = await service.get_weekly_activity(player_id)

		# Should NOT call Frappe since hash has all dates
		mock_frappe.call.assert_not_called()
		# Verify past days have XP from hash
		assert result["total_xp"] == sum(i * 10 for i in range(1, 7))
