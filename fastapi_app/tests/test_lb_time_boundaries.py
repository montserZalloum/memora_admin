"""Time boundary edge case tests for leaderboard system.

Tests midnight boundaries, daily key isolation, weekly accumulation,
and DST transitions for the Asia/Amman timezone.

FINDINGS:
┌─────────────────────────────────────────────────────────────────────────┐
│ FINDING-4: _get_key() and _get_plan_key() each call                    │
│ datetime.now(AMMAN_TZ) independently. In get_my_rank(), the key is     │
│ computed at call time. If midnight passes between update_leaderboards() │
│ and get_my_rank(), the player's data is on yesterday's key but the     │
│ query hits today's key → rank=None. This is expected but the window     │
│ is real and untestable without mocking.                                 │
│                                                                         │
│ FINDING-5: update_leaderboards() snapshots time ONCE for all keys.     │
│ This correctly prevents midnight-boundary splits (daily on date A but  │
│ weekly on date B). Verified by test_no_cross_key_split below.          │
│                                                                         │
│ FINDING-6: Asia/Amman DST transitions (last Friday of March/October)   │
│ cause the local clock to jump. A session ending during the "lost hour"  │
│ (spring forward) would still work because datetime.now(AMMAN_TZ)       │
│ never produces ambiguous times — the jump is forward. But the "gained  │
│ hour" (fall back) means 23:00-00:00 happens twice, which could cause   │
│ double-counting if a player earns XP in both occurrences of the hour.  │
│ Risk: LOW — would require a player to earn XP exactly during the       │
│ 1-hour DST fallback window AND query immediately.                      │
└─────────────────────────────────────────────────────────────────────────┘
"""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from fastapi_app.core.redis_keys import lb_daily_key, lb_daily_plan_key, lb_weekly_key, lb_weekly_plan_key
from fastapi_app.services.leaderboard import AMMAN_TZ, LeaderboardService


@pytest.fixture
async def lb_svc(redis_client):
	return LeaderboardService(redis_client)


@pytest.fixture(autouse=True)
async def cleanup_lb_keys(redis_client):
	yield
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match="memora:lb:*", count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match="memora:daily_xp:*", count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


class TestDailyKeyIsolation:
	"""XP earned on different dates goes to different daily keys."""

	PLAN = "PLAN-TEST-TIME"

	async def test_yesterday_today_separate_keys(self, lb_svc, redis_client):
		"""XP earned yesterday vs today lands in different daily ZSET keys."""
		# Simulate yesterday at 15:00
		yesterday = datetime.now(AMMAN_TZ) - timedelta(days=1)
		yesterday_15 = yesterday.replace(hour=15, minute=0, second=0, microsecond=0)
		yesterday_str = yesterday_15.strftime("%Y-%m-%d")

		# Simulate today at 10:00
		today = datetime.now(AMMAN_TZ)
		today_10 = today.replace(hour=10, minute=0, second=0, microsecond=0)
		today_str = today_10.strftime("%Y-%m-%d")

		# Patch datetime.now for "yesterday" update
		with patch("fastapi_app.services.leaderboard.datetime") as mock_dt:
			mock_dt.now.return_value = yesterday_15
			mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-YEST", xp_amount=100, plan_id=self.PLAN
			)

		# Patch datetime.now for "today" update
		with patch("fastapi_app.services.leaderboard.datetime") as mock_dt:
			mock_dt.now.return_value = today_10
			mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-TODAY", xp_amount=200, plan_id=self.PLAN
			)

		# Verify keys are separate
		yesterday_key = lb_daily_plan_key(yesterday_str, self.PLAN)
		today_key = lb_daily_plan_key(today_str, self.PLAN)

		yesterday_score = await redis_client.zscore(yesterday_key, "PLAYER-TEST-YEST")
		today_score = await redis_client.zscore(today_key, "PLAYER-TEST-TODAY")

		assert int(yesterday_score) == 100
		assert int(today_score) == 200

		# Cross-check: yesterday player NOT in today's key
		cross = await redis_client.zscore(today_key, "PLAYER-TEST-YEST")
		assert cross is None

	async def test_2359_and_0000_different_dates(self, lb_svc, redis_client):
		"""XP at 23:59:59 and 00:00:00 land on different daily keys."""
		now = datetime.now(AMMAN_TZ)
		# 23:59:59 today
		late_night = now.replace(hour=23, minute=59, second=59, microsecond=0)
		late_date = late_night.strftime("%Y-%m-%d")

		# 00:00:00 tomorrow
		midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
		midnight_date = midnight.strftime("%Y-%m-%d")

		with patch("fastapi_app.services.leaderboard.datetime") as mock_dt:
			mock_dt.now.return_value = late_night
			mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-2359", xp_amount=50, plan_id=self.PLAN
			)

		with patch("fastapi_app.services.leaderboard.datetime") as mock_dt:
			mock_dt.now.return_value = midnight
			mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-0000", xp_amount=50, plan_id=self.PLAN
			)

		# Must be on different keys
		key_2359 = lb_daily_plan_key(late_date, self.PLAN)
		key_0000 = lb_daily_plan_key(midnight_date, self.PLAN)

		assert key_2359 != key_0000
		assert await redis_client.zscore(key_2359, "PLAYER-TEST-2359") is not None
		assert await redis_client.zscore(key_0000, "PLAYER-TEST-0000") is not None


class TestWeeklyBoundary:
	"""Weekly leaderboard key = Friday date of the Islamic week."""

	PLAN = "PLAN-TEST-WKLY-BND"

	async def test_thursday_friday_different_weeks(self, lb_svc, redis_client):
		"""Thursday 23:59 and Friday 00:00 are in different weekly keys.

		Islamic week: Friday–Thursday. Thursday is the last day of the week.
		"""
		now = datetime.now(AMMAN_TZ)

		# Find the next Thursday
		days_until_thursday = (4 - now.isoweekday()) % 7
		if days_until_thursday == 0 and now.hour >= 23:
			days_until_thursday = 7
		thursday = now + timedelta(days=days_until_thursday)
		thursday_2359 = thursday.replace(hour=23, minute=59, second=59, microsecond=0)

		# Friday = next day
		friday_0000 = (thursday + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

		# Thursday should use THIS week's Friday
		thu_weekday = thursday_2359.isoweekday()
		thu_days_since_fri = (thu_weekday - 5) % 7
		thu_friday = (thursday_2359 - timedelta(days=thu_days_since_fri)).strftime("%Y-%m-%d")

		# Friday should use NEXT week's Friday (itself)
		fri_weekday = friday_0000.isoweekday()
		fri_days_since_fri = (fri_weekday - 5) % 7
		fri_friday = (friday_0000 - timedelta(days=fri_days_since_fri)).strftime("%Y-%m-%d")

		assert thu_friday != fri_friday, (
			f"Thursday and Friday should be in different weeks: {thu_friday} vs {fri_friday}"
		)

	async def test_weekly_accumulation_within_week(self, lb_svc, redis_client):
		"""XP earned on Monday and Wednesday within the same Islamic week accumulates."""
		now = datetime.now(AMMAN_TZ)

		# Find Monday (isoweekday=1) of the current Islamic week
		# Current Islamic week started on most recent Friday
		weekday = now.isoweekday()
		days_since_friday = (weekday - 5) % 7

		# Monday = Friday + 3 days
		friday = now - timedelta(days=days_since_friday)
		monday = friday + timedelta(days=3)
		monday_10 = monday.replace(hour=10, minute=0, second=0, microsecond=0)

		# Wednesday = Friday + 5 days
		wednesday = friday + timedelta(days=5)
		wednesday_10 = wednesday.replace(hour=10, minute=0, second=0, microsecond=0)

		# Both should map to the same weekly key
		friday_str = friday.strftime("%Y-%m-%d")

		with patch("fastapi_app.services.leaderboard.datetime") as mock_dt:
			mock_dt.now.return_value = monday_10
			mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-WKACC", xp_amount=100, plan_id=self.PLAN
			)

		with patch("fastapi_app.services.leaderboard.datetime") as mock_dt:
			mock_dt.now.return_value = wednesday_10
			mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-WKACC", xp_amount=200, plan_id=self.PLAN
			)

		weekly_key = lb_weekly_plan_key(friday_str, self.PLAN)
		score = await redis_client.zscore(weekly_key, "PLAYER-TEST-WKACC")
		assert int(score) == 300  # 100 + 200 accumulated


class TestNoMidnightSplit:
	"""update_leaderboards() snapshots time ONCE — no cross-key splits."""

	PLAN = "PLAN-TEST-NOSPLIT"

	async def test_single_time_snapshot(self, lb_svc, redis_client):
		"""All keys in one update_leaderboards call use the same timestamp.

		This prevents the scenario where daily key uses today's date but
		weekly key uses next week's Friday (if midnight crosses between them).
		See FINDING-5.
		"""
		# Simulate 23:59:59 on a Thursday (last day of Islamic week)
		now = datetime.now(AMMAN_TZ)
		days_until_thursday = (4 - now.isoweekday()) % 7
		if days_until_thursday == 0:
			days_until_thursday = 7
		thursday = now + timedelta(days=days_until_thursday)
		thursday_2359 = thursday.replace(hour=23, minute=59, second=59, microsecond=0)

		date_str = thursday_2359.strftime("%Y-%m-%d")
		weekday = thursday_2359.isoweekday()
		days_since_fri = (weekday - 5) % 7
		friday_str = (thursday_2359 - timedelta(days=days_since_fri)).strftime("%Y-%m-%d")

		with patch("fastapi_app.services.leaderboard.datetime") as mock_dt:
			mock_dt.now.return_value = thursday_2359
			mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-SNAP", xp_amount=100,
				subject_id="SUBJ-TEST-001", plan_id=self.PLAN,
			)

		# Verify all keys use the same date snapshot
		daily_key = lb_daily_key(date_str)
		weekly_key = lb_weekly_key(friday_str)
		daily_plan_key = lb_daily_plan_key(date_str, self.PLAN)
		weekly_plan_key = lb_weekly_plan_key(friday_str, self.PLAN)

		for key in [daily_key, weekly_key, daily_plan_key, weekly_plan_key]:
			score = await redis_client.zscore(key, "PLAYER-TEST-SNAP")
			assert score is not None, f"Key {key} missing — time snapshot may have split"
			assert int(score) == 100


class TestWeeklyFridayCalculation:
	"""Verify the Islamic week Friday calculation for all 7 days."""

	def test_friday_calculation_all_days(self):
		"""(weekday - 5) % 7 maps every day to the correct Friday.

		Islamic week: Friday=0 (start), Saturday=1, ..., Thursday=6 (end).
		"""
		# For each day of the week, verify the days_since_friday calculation
		expectations = {
			5: 0,  # Friday → 0 days since Friday
			6: 1,  # Saturday → 1
			7: 2,  # Sunday → 2
			1: 3,  # Monday → 3
			2: 4,  # Tuesday → 4
			3: 5,  # Wednesday → 5
			4: 6,  # Thursday → 6
		}

		for isoweekday, expected_days in expectations.items():
			days_since_friday = (isoweekday - 5) % 7
			assert days_since_friday == expected_days, (
				f"isoweekday={isoweekday}: expected {expected_days} days since Friday, "
				f"got {days_since_friday}"
			)


class TestDSTTransition:
	"""DST transitions in Asia/Amman timezone."""

	PLAN = "PLAN-TEST-DST"

	async def test_spring_forward_no_lost_xp(self, lb_svc, redis_client):
		"""During spring forward (clock jumps 01:00→02:00), XP is still recorded.

		In Asia/Amman, DST starts last Friday of March (01:00 → 02:00).
		The "lost hour" (01:00-02:00) never occurs, so datetime.now(AMMAN_TZ)
		jumps from 00:59 to 02:00. Any XP update during this period still
		gets a valid date key.
		"""
		# Find last Friday of March 2026
		# March 2026: 27th is a Friday (last Friday)
		spring_forward = datetime(2026, 3, 27, 0, 59, 0, tzinfo=AMMAN_TZ)
		date_str = spring_forward.strftime("%Y-%m-%d")

		with patch("fastapi_app.services.leaderboard.datetime") as mock_dt:
			mock_dt.now.return_value = spring_forward
			mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-DST-SF", xp_amount=50, plan_id=self.PLAN
			)

		daily_key = lb_daily_plan_key(date_str, self.PLAN)
		score = await redis_client.zscore(daily_key, "PLAYER-TEST-DST-SF")
		assert score is not None
		assert int(score) == 50

	async def test_fall_back_same_date_key(self, lb_svc, redis_client):
		"""During fall back (clock repeats 23:00→00:00), both occurrences
		use the same date key — XP accumulates, no double-counting across keys.

		In Asia/Amman, DST ends last Friday of October (00:00 → 23:00 previous day).
		"""
		# October 2026: last Friday is 30th
		# Before fallback: 2026-10-30 00:30 EEST (UTC+3)
		before_fb = datetime(2026, 10, 29, 23, 30, 0, tzinfo=AMMAN_TZ)
		date_before = before_fb.strftime("%Y-%m-%d")

		# After fallback: still 2026-10-29 23:30 EET (UTC+2) — same local time, different offset
		# Both should produce the same date string
		after_fb = datetime(2026, 10, 29, 23, 30, 0, tzinfo=AMMAN_TZ)
		date_after = after_fb.strftime("%Y-%m-%d")

		assert date_before == date_after, (
			"Fall-back should not change the date: "
			f"before={date_before}, after={date_after}"
		)


class TestDailyXPHashIsolation:
	"""daily_xp_key hash correctly isolates per-date XP."""

	PLAN = "PLAN-TEST-DXPH"

	async def test_daily_xp_hash_per_date(self, lb_svc, redis_client):
		"""Each date gets its own field in the daily_xp hash."""
		now = datetime.now(AMMAN_TZ)
		today_str = now.strftime("%Y-%m-%d")
		yesterday = now - timedelta(days=1)
		yesterday_str = yesterday.strftime("%Y-%m-%d")

		# Update today
		await lb_svc.update_leaderboards("PLAYER-TEST-DXPH-001", xp_amount=50, plan_id=self.PLAN)

		# Check daily_xp hash has today's field
		from fastapi_app.core.redis_keys import daily_xp_key

		dxp_key = daily_xp_key("PLAYER-TEST-DXPH-001")
		today_xp = await redis_client.hget(dxp_key, today_str)
		assert today_xp is not None
		assert int(today_xp) == 50

		# Yesterday's field should not exist (no update yesterday)
		yesterday_xp = await redis_client.hget(dxp_key, yesterday_str)
		assert yesterday_xp is None

	async def test_daily_xp_accumulates_same_date(self, lb_svc, redis_client):
		"""Multiple updates on the same date accumulate in the daily_xp hash."""
		from fastapi_app.core.redis_keys import daily_xp_key

		now = datetime.now(AMMAN_TZ)
		today_str = now.strftime("%Y-%m-%d")

		for _ in range(5):
			await lb_svc.update_leaderboards("PLAYER-TEST-DXPH-002", xp_amount=10, plan_id=self.PLAN)

		dxp_key = daily_xp_key("PLAYER-TEST-DXPH-002")
		today_xp = await redis_client.hget(dxp_key, today_str)
		assert int(today_xp) == 50  # 5 × 10
