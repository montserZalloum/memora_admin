"""Tests for LeaderboardService - plan-scoped XP leaderboard rankings."""

import pytest

from fastapi_app.core.redis_keys import lb_daily_plan_key, lb_weekly_plan_key
from fastapi_app.services.leaderboard import LeaderboardService

# Test constants
TEST_PLAYER_1 = "PLAYER-TEST-LB-001"
TEST_PLAYER_2 = "PLAYER-TEST-LB-002"
TEST_PLAYER_3 = "PLAYER-TEST-LB-003"
TEST_PLAYER_4 = "PLAYER-TEST-LB-004"
TEST_PLAN_A = "PLAN-TEST-A"
TEST_PLAN_B = "PLAN-TEST-B"
TEST_SUBJECT = "SUBJ-TEST-001"


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
	# Also clean daily_xp keys
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match="memora:daily_xp:*", count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


class TestUpdateLeaderboardsGlobal:
	"""update_leaderboards populates global sorted sets."""

	async def test_update_populates_global_sets(self, lb_svc, redis_client):
		"""update_leaderboards adds to daily/weekly global keys."""
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=50)

		# daily set exists
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match="memora:lb:daily:????-??-??", count=10)
			keys.extend(found)
			if cursor == 0:
				break
		# Filter out plan-scoped keys
		global_daily = [k for k in keys if ":plan:" not in k]
		assert len(global_daily) >= 1

		# weekly set exists
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match="memora:lb:weekly:????-??-??", count=10)
			keys.extend(found)
			if cursor == 0:
				break
		global_weekly = [k for k in keys if ":plan:" not in k]
		assert len(global_weekly) >= 1


class TestUpdateLeaderboardsPlanScoped:
	"""T011: update_leaderboards dual-writes to plan-scoped ZSETs."""

	async def test_plan_scoped_keys_created_with_plan_id(self, lb_svc, redis_client):
		"""With plan_id, plan-scoped ZINCRBY keys are created."""
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1, xp_amount=100, plan_id=TEST_PLAN_A
		)

		# Find plan-scoped daily key
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match=f"memora:lb:daily:*:plan:{TEST_PLAN_A}", count=10)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 1
		score = await redis_client.zscore(keys[0], TEST_PLAYER_1)
		assert int(score) == 100

		# Find plan-scoped weekly key
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match=f"memora:lb:weekly:*:plan:{TEST_PLAN_A}", count=10)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 1
		score = await redis_client.zscore(keys[0], TEST_PLAYER_1)
		assert int(score) == 100

	async def test_plan_subject_keys_created(self, lb_svc, redis_client):
		"""With plan_id + subject_id, plan+subject keys are created."""
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1,
			xp_amount=50,
			subject_id=TEST_SUBJECT,
			plan_id=TEST_PLAN_A,
		)

		# plan+subject daily
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(
				cursor, match=f"memora:lb:daily:*:plan:{TEST_PLAN_A}:subject:{TEST_SUBJECT}", count=10
			)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 1

		# plan+subject weekly
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(
				cursor, match=f"memora:lb:weekly:*:plan:{TEST_PLAN_A}:subject:{TEST_SUBJECT}", count=10
			)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 1

	async def test_global_daily_weekly_still_written_with_plan_id(self, lb_svc, redis_client):
		"""Global daily/weekly keys are still written when plan_id is provided."""
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1, xp_amount=75, plan_id=TEST_PLAN_A
		)

		# Global daily key still written
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match="memora:lb:daily:????-??-??", count=10)
			keys.extend(found)
			if cursor == 0:
				break
		global_daily = [k for k in keys if ":plan:" not in k]
		assert len(global_daily) >= 1
		score = await redis_client.zscore(global_daily[0], TEST_PLAYER_1)
		assert score is not None
		assert int(score) == 75

	async def test_plan_daily_ttl(self, lb_svc, redis_client):
		"""Plan daily keys have 48h TTL."""
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1, xp_amount=10, plan_id=TEST_PLAN_A
		)

		# Find the plan daily key
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match=f"memora:lb:daily:*:plan:{TEST_PLAN_A}", count=10)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 1
		ttl = await redis_client.ttl(keys[0])
		# TTL should be around 48h = 172800s (allow some margin)
		assert 172700 < ttl <= 172800

	async def test_plan_weekly_ttl(self, lb_svc, redis_client):
		"""Plan weekly keys have 8d TTL."""
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1, xp_amount=10, plan_id=TEST_PLAN_A
		)

		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match=f"memora:lb:weekly:*:plan:{TEST_PLAN_A}", count=10)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 1
		ttl = await redis_client.ttl(keys[0])
		# TTL should be around 8d = 691200s
		assert 691100 < ttl <= 691200

	async def test_no_plan_keys_without_plan_id(self, lb_svc, redis_client):
		"""With plan_id=None, no plan-scoped keys are created (backward compat)."""
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=50)

		# No plan-scoped keys
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match="memora:lb:*:plan:*", count=100)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 0

	async def test_zero_xp_skipped(self, lb_svc, redis_client):
		"""update_leaderboards with xp_amount=0 must NOT create ZSET members."""
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1, xp_amount=0, plan_id=TEST_PLAN_A
		)

		# No keys should exist — 0 XP is a no-op
		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match="memora:lb:*", count=100)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 0, f"0-XP update should create no ZSET keys, found: {keys}"

	async def test_negative_xp_skipped(self, lb_svc, redis_client):
		"""update_leaderboards with negative xp_amount must NOT write."""
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1, xp_amount=-5, plan_id=TEST_PLAN_A
		)

		cursor, keys = 0, []
		while True:
			cursor, found = await redis_client.scan(cursor, match="memora:lb:*", count=100)
			keys.extend(found)
			if cursor == 0:
				break
		assert len(keys) == 0, f"Negative XP should create no keys, found: {keys}"


class TestGetTopPlanScoped:
	"""T012: get_top reads from plan-scoped keys."""

	async def test_get_top_with_plan_id(self, lb_svc, redis_client):
		"""get_top with plan_id reads from plan-scoped key, not global."""
		# Seed plan-scoped data
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1, xp_amount=100, plan_id=TEST_PLAN_A
		)
		await lb_svc.update_leaderboards(
			TEST_PLAYER_2, xp_amount=80, plan_id=TEST_PLAN_A
		)

		result = await lb_svc.get_top("daily", limit=20, plan_id=TEST_PLAN_A)

		assert len(result) == 2
		assert result[0]["player_id"] == TEST_PLAYER_1
		assert result[0]["xp"] == 100
		assert result[0]["rank"] == 1
		assert result[1]["player_id"] == TEST_PLAYER_2
		assert result[1]["xp"] == 80
		assert result[1]["rank"] == 2

	async def test_get_top_with_plan_and_subject(self, lb_svc, redis_client):
		"""get_top with plan_id + subject_id reads from plan+subject key."""
		await lb_svc.update_leaderboards(
			TEST_PLAYER_1,
			xp_amount=60,
			subject_id=TEST_SUBJECT,
			plan_id=TEST_PLAN_A,
		)

		result = await lb_svc.get_top("daily", limit=20, subject_id=TEST_SUBJECT, plan_id=TEST_PLAN_A)

		assert len(result) == 1
		assert result[0]["player_id"] == TEST_PLAYER_1
		assert result[0]["xp"] == 60

	async def test_get_top_empty_plan(self, lb_svc):
		"""get_top on empty plan leaderboard returns []."""
		result = await lb_svc.get_top("daily", limit=20, plan_id="PLAN-NONEXISTENT")
		assert result == []


class TestGetMyRankPlanScoped:
	"""T012: get_my_rank reads from plan-scoped keys."""

	async def test_get_my_rank_with_plan_id(self, lb_svc, redis_client):
		"""get_my_rank with plan_id returns rank within plan scope."""
		# Seed data: 3 players in same plan
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=100, plan_id=TEST_PLAN_A)
		await lb_svc.update_leaderboards(TEST_PLAYER_2, xp_amount=80, plan_id=TEST_PLAN_A)
		await lb_svc.update_leaderboards(TEST_PLAYER_3, xp_amount=60, plan_id=TEST_PLAN_A)

		result = await lb_svc.get_my_rank(TEST_PLAYER_2, "daily", plan_id=TEST_PLAN_A)

		assert result["rank"] == 2
		assert result["xp"] == 80
		assert result["total_players"] == 3
		assert result["xp_to_next"] == 20  # 100 - 80

	async def test_unranked_player_returns_null_rank(self, lb_svc, redis_client):
		"""Unranked player in plan-scoped leaderboard gets rank: None."""
		# Add some players but not TEST_PLAYER_3
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=50, plan_id=TEST_PLAN_A)

		result = await lb_svc.get_my_rank(TEST_PLAYER_3, "daily", plan_id=TEST_PLAN_A)

		assert result["rank"] is None
		assert result["xp"] == 0
		assert result["neighbors"] == []
		assert result["total_players"] == 1


class TestPlanIsolation:
	"""T013: Plan isolation — players in different plans don't mix."""

	async def test_plan_a_only_sees_plan_a_players(self, lb_svc, redis_client):
		"""get_top(plan_id=PLAN-A) returns only PLAN-A players."""
		# PLAN-A players
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=100, plan_id=TEST_PLAN_A)
		await lb_svc.update_leaderboards(TEST_PLAYER_2, xp_amount=80, plan_id=TEST_PLAN_A)

		# PLAN-B players
		await lb_svc.update_leaderboards(TEST_PLAYER_3, xp_amount=200, plan_id=TEST_PLAN_B)
		await lb_svc.update_leaderboards(TEST_PLAYER_4, xp_amount=150, plan_id=TEST_PLAN_B)

		# Query PLAN-A
		result_a = await lb_svc.get_top("daily", limit=20, plan_id=TEST_PLAN_A)
		player_ids_a = {e["player_id"] for e in result_a}

		assert len(result_a) == 2
		assert TEST_PLAYER_1 in player_ids_a
		assert TEST_PLAYER_2 in player_ids_a
		assert TEST_PLAYER_3 not in player_ids_a
		assert TEST_PLAYER_4 not in player_ids_a

	async def test_plan_b_only_sees_plan_b_players(self, lb_svc, redis_client):
		"""get_top(plan_id=PLAN-B) returns only PLAN-B players."""
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=100, plan_id=TEST_PLAN_A)
		await lb_svc.update_leaderboards(TEST_PLAYER_3, xp_amount=200, plan_id=TEST_PLAN_B)
		await lb_svc.update_leaderboards(TEST_PLAYER_4, xp_amount=150, plan_id=TEST_PLAN_B)

		result_b = await lb_svc.get_top("daily", limit=20, plan_id=TEST_PLAN_B)
		player_ids_b = {e["player_id"] for e in result_b}

		assert len(result_b) == 2
		assert TEST_PLAYER_3 in player_ids_b
		assert TEST_PLAYER_4 in player_ids_b
		assert TEST_PLAYER_1 not in player_ids_b

	async def test_global_keys_contain_all_players(self, lb_svc, redis_client):
		"""Global daily/weekly keys contain players from BOTH plans."""
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=100, plan_id=TEST_PLAN_A)
		await lb_svc.update_leaderboards(TEST_PLAYER_3, xp_amount=200, plan_id=TEST_PLAN_B)

		# Global daily should have both
		result = await lb_svc.get_top("daily", limit=20)
		player_ids = {e["player_id"] for e in result}
		assert TEST_PLAYER_1 in player_ids
		assert TEST_PLAYER_3 in player_ids

	async def test_my_rank_scoped_to_plan(self, lb_svc, redis_client):
		"""get_my_rank with plan_id scopes rank to plan peers only."""
		# PLAN-A: Player 1 has 100xp, Player 2 has 80xp
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=100, plan_id=TEST_PLAN_A)
		await lb_svc.update_leaderboards(TEST_PLAYER_2, xp_amount=80, plan_id=TEST_PLAN_A)

		# PLAN-B: Player 3 has 200xp (much higher, but different plan)
		await lb_svc.update_leaderboards(TEST_PLAYER_3, xp_amount=200, plan_id=TEST_PLAN_B)

		# Player 1's rank in PLAN-A should be 1 (not affected by PLAN-B)
		result = await lb_svc.get_my_rank(TEST_PLAYER_1, "daily", plan_id=TEST_PLAN_A)
		assert result["rank"] == 1
		assert result["total_players"] == 2


class TestDenseRanking:
	"""Dense ranking with plan-scoped leaderboards."""

	async def test_dense_ranking_ties(self, lb_svc, redis_client):
		"""Tied XP players share same rank in plan-scoped leaderboard."""
		# Two players with same XP in same plan
		await lb_svc.update_leaderboards(TEST_PLAYER_1, xp_amount=100, plan_id=TEST_PLAN_A)
		await lb_svc.update_leaderboards(TEST_PLAYER_2, xp_amount=100, plan_id=TEST_PLAN_A)
		await lb_svc.update_leaderboards(TEST_PLAYER_3, xp_amount=50, plan_id=TEST_PLAN_A)

		result = await lb_svc.get_top("daily", limit=20, plan_id=TEST_PLAN_A)

		assert len(result) == 3
		# First two share rank 1 (same XP)
		assert result[0]["xp"] == 100
		assert result[1]["xp"] == 100
		assert result[0]["rank"] == 1
		assert result[1]["rank"] == 1
		# Third gets rank 2 (dense ranking: 1,1,2 — no gap)
		assert result[2]["xp"] == 50
		assert result[2]["rank"] == 2
