"""Diagnostic tests for weekly leaderboard data integrity.

Verifies:
1. ZINCRBY accumulation produces correct scores
2. Dense ranking math under various scenarios
3. XP consistency: wallet XP vs leaderboard XP
4. get_top vs get_my_rank rank agreement
5. Edge cases: 0 XP, ties, large offsets, plan isolation
6. update_leaderboards writes to correct keys with correct values
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fastapi_app.core.redis_keys import (
	lb_daily_key,
	lb_daily_plan_key,
	lb_weekly_key,
	lb_weekly_plan_key,
	daily_xp_key,
)
from fastapi_app.services.leaderboard import LeaderboardService, AMMAN_TZ

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _current_friday() -> str:
	now = datetime.now(AMMAN_TZ)
	weekday = now.isoweekday()
	days_since_friday = (weekday - 5) % 7
	return (now - timedelta(days=days_since_friday)).strftime("%Y-%m-%d")


def _today() -> str:
	return datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")


PLAN_A = "PLAN-DIAG-A"
PLAN_B = "PLAN-DIAG-B"


async def _cleanup(r):
	"""Clean all diagnostic keys."""
	friday = _current_friday()
	today = _today()
	patterns = [
		f"memora:lb:weekly:{friday}:plan:{PLAN_A}*",
		f"memora:lb:weekly:{friday}:plan:{PLAN_B}*",
		f"memora:lb:daily:{today}:plan:{PLAN_A}*",
		f"memora:lb:daily:{today}:plan:{PLAN_B}*",
		f"memora:lb:weekly:{friday}",
		f"memora:lb:daily:{today}",
		f"memora:lb:daily:{today}:subject:*",
		f"memora:lb:weekly:{friday}:subject:*",
		"memora:daily_xp:DIAG-*",
	]
	for pat in patterns:
		async for key in r.scan_iter(pat):
			await r.delete(key)


# ------------------------------------------------------------------ #
# Test 1: ZINCRBY accumulation produces exact integer scores
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
class TestZincrbyAccumulation:
	"""Verify ZINCRBY produces exact scores after many increments."""

	async def test_many_small_increments_exact(self, redis_client):
		"""100 increments of 3 XP should produce exactly 300, not 299.999..."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		for i in range(100):
			await svc.update_leaderboards(
				player_id="DIAG-ACCUM-01",
				xp_amount=3,
				plan_id=PLAN_A,
			)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		score = await redis_client.zscore(key, "DIAG-ACCUM-01")

		assert score is not None, "Player not found in weekly ZSET"
		assert score == 300.0, f"Expected 300.0, got {score} — float drift!"
		assert int(score) == 300, f"int(score) = {int(score)}, expected 300"

		await _cleanup(redis_client)

	async def test_mixed_increments(self, redis_client):
		"""Different XP amounts accumulate correctly."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		amounts = [10, 5, 15, 3, 7, 20, 1, 8, 12, 19]
		for amt in amounts:
			await svc.update_leaderboards(
				player_id="DIAG-MIXED-01",
				xp_amount=amt,
				plan_id=PLAN_A,
			)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		score = await redis_client.zscore(key, "DIAG-MIXED-01")

		expected = sum(amounts)  # 100
		assert int(score) == expected, f"Expected {expected}, got {int(score)}"

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# Test 2: Dense ranking correctness
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
class TestDenseRankingCorrectness:
	"""Verify dense ranking produces correct ranks in all cases."""

	async def test_simple_dense_ranks(self, redis_client):
		"""Basic dense ranking: 1,1,2,3,3,4"""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		# Seed directly with ZADD for precise control
		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		await redis_client.zadd(key, {
			"DIAG-R-A": 500,
			"DIAG-R-B": 300,
			"DIAG-R-C": 300,
			"DIAG-R-D": 100,
			"DIAG-R-E": 100,
			"DIAG-R-F": 50,
		})
		await redis_client.expire(key, 3600)

		result = await svc.get_top("weekly", limit=10, plan_id=PLAN_A)

		ranks = {e["player_id"]: e["rank"] for e in result}
		assert ranks["DIAG-R-A"] == 1
		assert ranks["DIAG-R-B"] == 2
		assert ranks["DIAG-R-C"] == 2
		assert ranks["DIAG-R-D"] == 3
		assert ranks["DIAG-R-E"] == 3
		assert ranks["DIAG-R-F"] == 4

		await _cleanup(redis_client)

	async def test_all_tied(self, redis_client):
		"""All players tied: everyone gets rank 1."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		await redis_client.zadd(key, {
			"DIAG-TIE-A": 100,
			"DIAG-TIE-B": 100,
			"DIAG-TIE-C": 100,
		})
		await redis_client.expire(key, 3600)

		result = await svc.get_top("weekly", limit=10, plan_id=PLAN_A)
		ranks = [e["rank"] for e in result]
		assert ranks == [1, 1, 1], f"All tied should be rank 1, got {ranks}"

		await _cleanup(redis_client)

	async def test_no_ties(self, redis_client):
		"""No ties: sequential ranks 1,2,3,4,5."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		await redis_client.zadd(key, {
			"DIAG-NT-A": 500,
			"DIAG-NT-B": 400,
			"DIAG-NT-C": 300,
			"DIAG-NT-D": 200,
			"DIAG-NT-E": 100,
		})
		await redis_client.expire(key, 3600)

		result = await svc.get_top("weekly", limit=10, plan_id=PLAN_A)
		ranks = [e["rank"] for e in result]
		assert ranks == [1, 2, 3, 4, 5], f"Expected [1,2,3,4,5], got {ranks}"

		await _cleanup(redis_client)

	async def test_dense_ranks_with_offset_are_absolute(self, redis_client):
		"""Ranks after offset must be absolute (not restarted from 1)."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		await redis_client.zadd(key, {
			"DIAG-OFF-A": 500,  # rank 1
			"DIAG-OFF-B": 300,  # rank 2
			"DIAG-OFF-C": 300,  # rank 2
			"DIAG-OFF-D": 100,  # rank 3
			"DIAG-OFF-E": 50,   # rank 4
		})
		await redis_client.expire(key, 3600)

		# offset=2 should skip A and B, return C(rank 2), D(rank 3), E(rank 4)
		result = await svc.get_top("weekly", limit=3, offset=2, plan_id=PLAN_A)
		ranks = [e["rank"] for e in result]
		assert ranks == [2, 3, 4], f"Expected [2,3,4], got {ranks}"

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# Test 3: get_top vs get_my_rank rank agreement
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
class TestRankConsistency:
	"""Verify get_top and get_my_rank produce identical ranks."""

	async def test_rank_agreement_for_all_players(self, redis_client):
		"""Every player's rank from get_top must match get_my_rank."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		players = {
			"DIAG-AGR-A": 500,
			"DIAG-AGR-B": 300,
			"DIAG-AGR-C": 300,
			"DIAG-AGR-D": 150,
			"DIAG-AGR-E": 50,
		}
		await redis_client.zadd(key, players)
		await redis_client.expire(key, 3600)

		# Get ranks from get_top
		top_result = await svc.get_top("weekly", limit=10, plan_id=PLAN_A)
		top_ranks = {e["player_id"]: e["rank"] for e in top_result}

		# Get ranks from get_my_rank for each player
		mismatches = []
		for pid in players:
			my_result = await svc.get_my_rank(pid, "weekly", plan_id=PLAN_A)
			my_rank = my_result["rank"]
			if top_ranks[pid] != my_rank:
				mismatches.append(
					f"{pid}: get_top={top_ranks[pid]}, get_my_rank={my_rank}"
				)

		assert not mismatches, f"Rank mismatches:\n" + "\n".join(mismatches)

		await _cleanup(redis_client)

	async def test_xp_agreement(self, redis_client):
		"""XP from get_top and get_my_rank must match for all players."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		players = {
			"DIAG-XAG-A": 500,
			"DIAG-XAG-B": 300,
			"DIAG-XAG-C": 100,
		}
		await redis_client.zadd(key, players)
		await redis_client.expire(key, 3600)

		top_result = await svc.get_top("weekly", limit=10, plan_id=PLAN_A)
		top_xps = {e["player_id"]: e["xp"] for e in top_result}

		mismatches = []
		for pid, expected_xp in players.items():
			my_result = await svc.get_my_rank(pid, "weekly", plan_id=PLAN_A)
			if top_xps[pid] != my_result["xp"]:
				mismatches.append(
					f"{pid}: get_top.xp={top_xps[pid]}, get_my_rank.xp={my_result['xp']}"
				)
			if my_result["xp"] != expected_xp:
				mismatches.append(
					f"{pid}: expected xp={expected_xp}, got {my_result['xp']}"
				)

		assert not mismatches, f"XP mismatches:\n" + "\n".join(mismatches)

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# Test 4: update_leaderboards writes to ALL required keys
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
class TestUpdateLeaderboardsWrites:
	"""Verify update_leaderboards writes to all required Redis keys."""

	async def test_all_keys_populated(self, redis_client):
		"""Single update_leaderboards call should write to daily/weekly keys."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await svc.update_leaderboards(
			player_id="DIAG-WRITE-01",
			xp_amount=10,
			subject_id="SUBJ-DIAG-01",
			plan_id=PLAN_A,
		)

		friday = _current_friday()
		today = _today()

		# Check all expected keys exist
		checks = {
			"daily_global": lb_daily_key(today),
			"daily_subject": lb_daily_key(today, "SUBJ-DIAG-01"),
			"weekly_global": lb_weekly_key(friday),
			"weekly_subject": lb_weekly_key(friday, "SUBJ-DIAG-01"),
			"plan_daily": lb_daily_plan_key(today, PLAN_A),
			"plan_weekly": lb_weekly_plan_key(friday, PLAN_A),
			"plan_daily_subject": lb_daily_plan_key(today, PLAN_A, "SUBJ-DIAG-01"),
			"plan_weekly_subject": lb_weekly_plan_key(friday, PLAN_A, "SUBJ-DIAG-01"),
			"daily_xp": daily_xp_key("DIAG-WRITE-01"),
		}

		missing = []
		for name, key in checks.items():
			exists = await redis_client.exists(key)
			if not exists:
				missing.append(f"{name}: {key}")

		assert not missing, f"Missing keys after update:\n" + "\n".join(missing)

		await _cleanup(redis_client)

	async def test_plan_weekly_score_matches_xp_amount(self, redis_client):
		"""Plan-scoped weekly score should equal sum of xp_amount increments."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		amounts = [10, 5, 15, 20, 3]
		for amt in amounts:
			await svc.update_leaderboards(
				player_id="DIAG-SCORE-01",
				xp_amount=amt,
				plan_id=PLAN_A,
			)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		score = await redis_client.zscore(key, "DIAG-SCORE-01")

		expected = sum(amounts)  # 53
		assert int(score) == expected, (
			f"Weekly plan score mismatch: expected {expected}, got {int(score)}"
		)

		await _cleanup(redis_client)

	async def test_daily_xp_hash_matches_amounts(self, redis_client):
		"""daily_xp hash field should equal sum of xp_amounts for today."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		amounts = [10, 5, 15]
		for amt in amounts:
			await svc.update_leaderboards(
				player_id="DIAG-DXP-01",
				xp_amount=amt,
				plan_id=PLAN_A,
			)

		key = daily_xp_key("DIAG-DXP-01")
		today = _today()
		daily_xp = await redis_client.hget(key, today)

		expected = sum(amounts)
		assert daily_xp is not None, "daily_xp hash missing"
		assert int(daily_xp) == expected, (
			f"daily_xp for today: expected {expected}, got {daily_xp}"
		)

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# Test 5: Plan isolation — players in different plans don't mix
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
class TestPlanIsolation:
	"""Verify plan-scoped leaderboards don't leak between plans."""

	async def test_different_plans_isolated(self, redis_client):
		"""Players in PLAN_A should NOT appear in PLAN_B's leaderboard."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		# Player A in PLAN_A
		await svc.update_leaderboards(
			player_id="DIAG-ISO-A",
			xp_amount=100,
			plan_id=PLAN_A,
		)

		# Player B in PLAN_B
		await svc.update_leaderboards(
			player_id="DIAG-ISO-B",
			xp_amount=200,
			plan_id=PLAN_B,
		)

		# PLAN_A should only have player A
		result_a = await svc.get_top("weekly", limit=10, plan_id=PLAN_A)
		pids_a = [e["player_id"] for e in result_a]
		assert "DIAG-ISO-A" in pids_a
		assert "DIAG-ISO-B" not in pids_a

		# PLAN_B should only have player B
		result_b = await svc.get_top("weekly", limit=10, plan_id=PLAN_B)
		pids_b = [e["player_id"] for e in result_b]
		assert "DIAG-ISO-B" in pids_b
		assert "DIAG-ISO-A" not in pids_b

		# Global board should have both
		result_global = await svc.get_top("weekly", limit=10)
		pids_global = [e["player_id"] for e in result_global]
		assert "DIAG-ISO-A" in pids_global
		assert "DIAG-ISO-B" in pids_global

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# Test 6: Edge cases — 0 XP, single player, unranked
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
class TestEdgeCases:
	"""Test edge cases that could produce wrong data."""

	async def test_zero_xp_does_not_create_ghost_entry(self, redis_client):
		"""REGRESSION: update_leaderboards with xp_amount=0 must NOT
		add player to any ZSET. Previously ZINCRBY(key, 0, member)
		created the member with score 0, inflating total_players."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await svc.update_leaderboards(
			player_id="DIAG-ZERO-01",
			xp_amount=0,
			plan_id=PLAN_A,
		)

		friday = _current_friday()
		plan_weekly_key = lb_weekly_plan_key(friday, PLAN_A)
		score = await redis_client.zscore(plan_weekly_key, "DIAG-ZERO-01")
		assert score is None, (
			f"0-XP player should NOT exist in weekly ZSET, but has score={score}"
		)

		# Also verify no daily_xp hash was created
		dxp_key = daily_xp_key("DIAG-ZERO-01")
		exists = await redis_client.exists(dxp_key)
		assert not exists, "0-XP update should not create daily_xp hash"

		# Also verify no global keys were created
		today = _today()
		daily_score = await redis_client.zscore(lb_daily_key(today), "DIAG-ZERO-01")
		assert daily_score is None, "0-XP player should not be in global daily ZSET"

		await _cleanup(redis_client)

	async def test_negative_xp_does_not_create_entry(self, redis_client):
		"""Negative xp_amount must be a no-op."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await svc.update_leaderboards(
			player_id="DIAG-NEG-01",
			xp_amount=-10,
			plan_id=PLAN_A,
		)

		friday = _current_friday()
		plan_weekly_key = lb_weekly_plan_key(friday, PLAN_A)
		score = await redis_client.zscore(plan_weekly_key, "DIAG-NEG-01")
		assert score is None, "Negative XP should not create ZSET member"

		await _cleanup(redis_client)

	async def test_single_player_rank(self, redis_client):
		"""Single player should always be rank 1."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await svc.update_leaderboards(
			player_id="DIAG-SOLO-01",
			xp_amount=50,
			plan_id=PLAN_A,
		)

		# get_top
		top = await svc.get_top("weekly", limit=10, plan_id=PLAN_A)
		assert len(top) == 1
		assert top[0]["rank"] == 1
		assert top[0]["xp"] == 50

		# get_my_rank
		my = await svc.get_my_rank("DIAG-SOLO-01", "weekly", plan_id=PLAN_A)
		assert my["rank"] == 1
		assert my["xp"] == 50
		assert my["xp_to_next"] is None  # No one above

		await _cleanup(redis_client)

	async def test_unranked_player(self, redis_client):
		"""Player NOT in the ZSET should get rank=None (plan-scoped)."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		# Seed one player so the board isn't empty
		await svc.update_leaderboards(
			player_id="DIAG-RANKED-01",
			xp_amount=100,
			plan_id=PLAN_A,
		)

		# Query rank for a player NOT in the ZSET
		result = await svc.get_my_rank("DIAG-GHOST-01", "weekly", plan_id=PLAN_A)
		assert result["rank"] is None, f"Unranked player should have rank=None, got {result['rank']}"
		assert result["xp"] == 0

		await _cleanup(redis_client)

	async def test_xp_to_next_calculation(self, redis_client):
		"""xp_to_next should be the gap to the closest higher tier."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)
		await redis_client.zadd(key, {
			"DIAG-XTN-A": 500,   # rank 1
			"DIAG-XTN-B": 300,   # rank 2
			"DIAG-XTN-C": 150,   # rank 3
		})
		await redis_client.expire(key, 3600)

		# Player C (150 XP): next tier is 300, gap = 150
		result_c = await svc.get_my_rank("DIAG-XTN-C", "weekly", plan_id=PLAN_A)
		assert result_c["xp_to_next"] == 150, (
			f"Expected xp_to_next=150, got {result_c['xp_to_next']}"
		)

		# Player A (500 XP): no one above, xp_to_next = None
		result_a = await svc.get_my_rank("DIAG-XTN-A", "weekly", plan_id=PLAN_A)
		assert result_a["xp_to_next"] is None, (
			f"Top player should have xp_to_next=None, got {result_a['xp_to_next']}"
		)

		# Player B (300 XP): next tier is 500, gap = 200
		result_b = await svc.get_my_rank("DIAG-XTN-B", "weekly", plan_id=PLAN_A)
		assert result_b["xp_to_next"] == 200, (
			f"Expected xp_to_next=200, got {result_b['xp_to_next']}"
		)

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# Test 7: Large-scale stress test for ranking
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
class TestLargeScaleRanking:
	"""Test ranking with many players to catch drift/precision issues."""

	async def test_100_players_ranking(self, redis_client):
		"""100 players: verify rank ordering is strictly correct."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)

		# Create 100 players: 20 groups of 5 players each (ties within group)
		players = {}
		for group in range(20):
			xp = (20 - group) * 50  # 1000, 950, 900, ...
			for i in range(5):
				pid = f"DIAG-LARGE-{group:02d}-{i}"
				players[pid] = xp

		await redis_client.zadd(key, players)
		await redis_client.expire(key, 3600)

		# get_top with full fetch
		result = await svc.get_top("weekly", limit=100, plan_id=PLAN_A)

		# Verify: 20 groups = 20 distinct ranks
		ranks = [e["rank"] for e in result]
		xps = [e["xp"] for e in result]

		# Should have ranks 1 through 20 (dense)
		unique_ranks = sorted(set(ranks))
		assert unique_ranks == list(range(1, 21)), (
			f"Expected 20 dense ranks, got {unique_ranks}"
		)

		# XPs should be non-increasing
		for i in range(1, len(xps)):
			assert xps[i] <= xps[i - 1], (
				f"XP not non-increasing at position {i}: {xps[i-1]} then {xps[i]}"
			)

		# Each group of 5 should share the same rank
		rank_counts = {}
		for r in ranks:
			rank_counts[r] = rank_counts.get(r, 0) + 1
		for rank, count in rank_counts.items():
			assert count == 5, f"Rank {rank} has {count} players, expected 5"

		await _cleanup(redis_client)

	async def test_paginated_ranks_consistent(self, redis_client):
		"""Ranks across pages must be consistent (no gaps, no restarts)."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		friday = _current_friday()
		key = lb_weekly_plan_key(friday, PLAN_A)

		# 50 players with unique XP
		players = {f"DIAG-PAGE-{i:03d}": (50 - i) * 10 for i in range(50)}
		await redis_client.zadd(key, players)
		await redis_client.expire(key, 3600)

		# Fetch in pages of 10
		all_entries = []
		for offset in range(0, 50, 10):
			page = await svc.get_top("weekly", limit=10, offset=offset, plan_id=PLAN_A)
			all_entries.extend(page)

		# All 50 should be present
		assert len(all_entries) == 50, f"Expected 50, got {len(all_entries)}"

		# Ranks should be 1..50 sequential
		ranks = [e["rank"] for e in all_entries]
		assert ranks == list(range(1, 51)), (
			f"Paginated ranks not sequential: first mismatch at "
			f"position {next(i for i, (a, b) in enumerate(zip(ranks, range(1, 51))) if a != b)}"
		)

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# Test 8: Cross-validate wallet XP vs leaderboard XP
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
class TestWalletLeaderboardConsistency:
	"""Verify leaderboard XP reflects actual wallet awards."""

	async def test_multiple_sessions_accumulate(self, redis_client):
		"""Simulating 5 sessions: weekly XP should equal sum of all session XPs."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		session_xps = [10, 15, 5, 20, 10]

		for xp in session_xps:
			await svc.update_leaderboards(
				player_id="DIAG-SESS-01",
				xp_amount=xp,
				plan_id=PLAN_A,
			)

		# Weekly should be sum of session XPs
		friday = _current_friday()
		weekly_key = lb_weekly_plan_key(friday, PLAN_A)
		weekly_score = await redis_client.zscore(weekly_key, "DIAG-SESS-01")
		assert int(weekly_score) == sum(session_xps), (
			f"Weekly XP: expected {sum(session_xps)}, got {int(weekly_score)}"
		)

		# Daily should also be sum
		daily_key = lb_daily_plan_key(_today(), PLAN_A)
		daily_score = await redis_client.zscore(daily_key, "DIAG-SESS-01")
		assert int(daily_score) == sum(session_xps), (
			f"Daily XP: expected {sum(session_xps)}, got {int(daily_score)}"
		)

		await _cleanup(redis_client)
