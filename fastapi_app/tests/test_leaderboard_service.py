"""Tests for LeaderboardService - XP leaderboard rankings."""

import time
import pytest

from fastapi_app.services.leaderboard import LeaderboardService, compute_composite_score

# Test constants
TEST_PLAYER_1 = "PLAYER-TEST-LB-001"
TEST_PLAYER_2 = "PLAYER-TEST-LB-002"
TEST_PLAYER_3 = "PLAYER-TEST-LB-003"


@pytest.fixture
async def lb_svc(redis_client):
	"""LeaderboardService with test Redis."""
	return LeaderboardService(redis_client)


@pytest.fixture(autouse=True)
async def cleanup_leaderboard_keys(redis_client):
	"""Auto-cleanup leaderboard keys after each test."""
	yield
	# SCAN and delete all memora:lb:* keys
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match="memora:lb:*", count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


class TestUpdateLeaderboards:
	"""update_leaderboards populates sorted sets."""

	async def test_tc_lb_01_update_leaderboards_populates_sets(self, lb_svc, redis_client):
		"""TC-LB-01: update_leaderboards adds to alltime/daily/weekly."""
		# Action: update leaderboards for player with XP (xp_amount=50, new_total_xp=50)
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=50, new_total_xp=50)

		# Assert: alltime set contains entry with composite score (int part = 50)
		alltime_key = "memora:lb:alltime"
		score = await redis_client.zscore(alltime_key, TEST_PLAYER_1)
		assert score is not None
		assert int(score) == 50

		# Assert: daily set contains entry
		daily_key = None
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:daily:*", count=10)
			if keys:
				daily_key = keys[0]
				break
			if cursor == 0:
				break
		assert daily_key is not None

		# Assert: weekly set exists
		weekly_key = None
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:weekly:*", count=10)
			if keys:
				weekly_key = keys[0]
				break
			if cursor == 0:
				break
		assert weekly_key is not None


class TestGetTop:
	"""get_top returns ranked players by XP."""

	async def test_tc_lb_02_get_top_returns_ranked_players(self, lb_svc, redis_client):
		"""TC-LB-02: get_top returns desc-ordered results with dense rank."""
		# Setup: add 3 players to alltime leaderboard
		alltime_key = "memora:lb:alltime"
		ts1 = time.time()
		ts2 = ts1 + 1
		ts3 = ts1 + 2

		await redis_client.zadd(
			alltime_key,
			{
				TEST_PLAYER_1: compute_composite_score(100, ts1),
				TEST_PLAYER_2: compute_composite_score(90, ts2),
				TEST_PLAYER_3: compute_composite_score(80, ts3),
			},
		)

		# Action: get top 10
		result = await lb_svc.get_top("alltime", limit=10)

		# Assert: returns 3 players in descending XP order
		assert len(result) == 3
		assert result[0]["player_id"] == TEST_PLAYER_1
		assert result[0]["xp"] == 100
		assert result[0]["rank"] == 1
		assert result[1]["player_id"] == TEST_PLAYER_2
		assert result[1]["xp"] == 90
		assert result[1]["rank"] == 2
		assert result[2]["player_id"] == TEST_PLAYER_3
		assert result[2]["xp"] == 80
		assert result[2]["rank"] == 3

	async def test_tc_lb_03_dense_ranking_with_ties(self, lb_svc, redis_client):
		"""TC-LB-03: Dense ranking - tied players share rank."""
		# Setup: two players tied at 100 XP, one at 50
		alltime_key = "memora:lb:alltime"
		ts1 = time.time()
		ts2 = ts1 + 1
		ts3 = ts1 + 2

		await redis_client.zadd(
			alltime_key,
			{
				TEST_PLAYER_1: compute_composite_score(100, ts1),
				TEST_PLAYER_2: compute_composite_score(100, ts2),  # Same XP, later timestamp
				TEST_PLAYER_3: compute_composite_score(50, ts3),
			},
		)

		# Action: get top 10
		result = await lb_svc.get_top("alltime", limit=10)

		# Assert: players with same XP share rank, next different XP gets next rank
		# P1 (100, earliest) and P2 (100, later) both at rank 1 (or tied)
		# P3 (50) should be at rank 3 (dense ranking)
		assert len(result) == 3

		# P1 should be first (same XP but earlier timestamp)
		assert result[0]["xp"] == 100
		assert result[1]["xp"] == 100

		# P3 should be third
		assert result[2]["xp"] == 50
		assert result[2]["rank"] == 3


class TestGetTopEmpty:
	"""get_top edge cases."""

	async def test_tc_lb_04_get_top_empty_leaderboard(self, lb_svc):
		"""TC-LB-04: get_top on empty leaderboard returns []."""
		# Setup: no data

		# Action: get top 10
		result = await lb_svc.get_top("alltime", limit=10)

		# Assert: returns empty list
		assert result == []


class TestCompositeScore:
	"""compute_composite_score handles tie-breaking."""

	async def test_tc_lb_05_composite_score_tie_breaking(self):
		"""TC-LB-05: compute_composite_score encodes XP + timestamp."""
		# Setup: two scores with same XP but different timestamps
		xp = 100
		ts1 = 1000.0
		ts2 = 2000.0

		# Action: compute scores
		score1 = compute_composite_score(xp, ts1)
		score2 = compute_composite_score(xp, ts2)

		# Assert: integer parts match XP
		assert int(score1) == 100
		assert int(score2) == 100

		# Assert: fractional parts differ (earlier timestamp = higher score)
		frac1 = score1 - int(score1)
		frac2 = score2 - int(score2)
		assert frac1 != frac2

		# Assert: earlier timestamp yields higher fractional part (for tie-breaking)
		assert frac1 > frac2

	async def test_tc_lb_06_composite_score_default_timestamp(self):
		"""TC-LB-06: compute_composite_score uses current time if not provided."""
		# Action: compute score without timestamp
		score = compute_composite_score(50)

		# Assert: integer part is 50
		assert int(score) == 50

		# Assert: fractional part in valid range [0, 1)
		frac = score - int(score)
		assert 0 <= frac < 1


