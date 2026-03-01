"""Concurrency simulation tests for leaderboard system.

Simulates concurrent XP updates to validate:
- No lost updates (ZINCRBY is atomic)
- Correct final XP after concurrent writes
- Rank consistency after concurrent updates

FINDINGS:
┌─────────────────────────────────────────────────────────────────────────┐
│ FINDING-10: ZINCRBY is atomic in Redis — individual increments cannot  │
│ be lost. However, update_leaderboards() uses a pipeline with multiple  │
│ ZINCRBY commands. If the pipeline is interrupted (client disconnect,   │
│ Redis crash mid-pipeline), some keys may be updated while others are   │
│ not. This creates a divergence between global and plan-scoped ZSETs.  │
│ Risk: LOW — Redis pipelines are typically all-or-nothing on the wire, │
│ and ZINCRBY is idempotent in the sense that a retry would just add     │
│ more XP (not corrupt). A true partial failure would require the Redis  │
│ server to crash between individual commands in the pipeline.           │
│                                                                         │
│ FINDING-11: get_my_rank() uses two pipeline RTTs. Between RTT1 (get    │
│ position/total/score) and RTT2 (neighbor window + Lua), another        │
│ player's XP update could change the ZSET. This means the neighbor      │
│ window might be stale relative to the player's position. Risk: LOW —   │
│ leaderboards are best-effort; momentary inconsistency is acceptable.   │
│                                                                         │
│ FINDING-12: Concurrent update_leaderboards + get_my_rank for the       │
│ SAME player could see partial state: RTT1 gets old score, then ZINCRBY │
│ fires, then RTT2 gets new neighbor window. The Lua script would count  │
│ tiers above the OLD score, but neighbors reflect the NEW state.        │
│ Risk: LOW — self-corrects on next query.                               │
└─────────────────────────────────────────────────────────────────────────┘
"""

import asyncio
import random

import pytest

from fastapi_app.services.leaderboard import LeaderboardService


@pytest.fixture
async def lb_svc(redis_client):
	return LeaderboardService(redis_client)


async def _scan_delete(redis_client, pattern):
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match=pattern, count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


@pytest.fixture(autouse=True)
async def cleanup_lb_keys(redis_client):
	yield
	for pattern in ("memora:lb:*", "memora:lbmeta:*", "memora:daily_xp:*"):
		await _scan_delete(redis_client, pattern)


class TestConcurrentSamePlayer:
	"""Concurrent XP updates for the same player."""

	PLAN = "PLAN-TEST-CONC"

	async def test_100_concurrent_updates_same_player(self, lb_svc, redis_client):
		"""100 concurrent ZINCRBY(10) = exactly 1000 XP. No lost updates.

		Redis ZINCRBY is atomic — each increment is guaranteed to apply.
		This test verifies no application-level race conditions lose updates.
		"""
		player = "PLAYER-TEST-CONC-001"

		tasks = [
			lb_svc.update_leaderboards(player, xp_amount=10, plan_id=self.PLAN)
			for _ in range(100)
		]
		await asyncio.gather(*tasks)

		result = await lb_svc.get_my_rank(player, "daily", plan_id=self.PLAN)
		assert result["xp"] == 1000, f"Expected 1000 XP, got {result['xp']} (lost updates?)"

	async def test_varying_xp_concurrent(self, lb_svc, redis_client):
		"""Concurrent updates with varying XP amounts sum correctly."""
		player = "PLAYER-TEST-CONC-002"
		xp_values = [random.randint(1, 100) for _ in range(200)]
		expected_total = sum(xp_values)

		tasks = [
			lb_svc.update_leaderboards(player, xp_amount=xp, plan_id=self.PLAN)
			for xp in xp_values
		]
		await asyncio.gather(*tasks)

		result = await lb_svc.get_my_rank(player, "daily", plan_id=self.PLAN)
		assert result["xp"] == expected_total, (
			f"Expected {expected_total} XP, got {result['xp']}"
		)

	async def test_concurrent_with_subject_id(self, lb_svc, redis_client):
		"""Concurrent updates with subject_id don't lose subject-scoped XP."""
		player = "PLAYER-TEST-CONC-003"
		subject = "SUBJ-TEST-001"

		tasks = [
			lb_svc.update_leaderboards(
				player, xp_amount=5, subject_id=subject, plan_id=self.PLAN
			)
			for _ in range(100)
		]
		await asyncio.gather(*tasks)

		# Check both global and subject-scoped
		result_global = await lb_svc.get_my_rank(player, "daily", plan_id=self.PLAN)
		result_subject = await lb_svc.get_my_rank(
			player, "daily", subject_id=subject, plan_id=self.PLAN
		)

		assert result_global["xp"] == 500
		assert result_subject["xp"] == 500


class TestConcurrentMultiplePlayers:
	"""Concurrent XP updates across multiple players."""

	PLAN = "PLAN-TEST-MULTI"

	async def test_100_players_concurrent(self, lb_svc, redis_client):
		"""100 different players update concurrently — each gets correct XP."""
		players = [(f"PLAYER-TEST-MULTI-{i:04d}", 10 * (i + 1)) for i in range(100)]

		tasks = [
			lb_svc.update_leaderboards(pid, xp_amount=xp, plan_id=self.PLAN)
			for pid, xp in players
		]
		await asyncio.gather(*tasks)

		# Verify each player's score
		key = lb_svc._get_plan_key("daily", self.PLAN)
		for pid, expected_xp in players:
			score = await redis_client.zscore(key, pid)
			assert int(score) == expected_xp, (
				f"{pid}: expected {expected_xp}, got {int(score)}"
			)

		# Verify total count
		zcard = await redis_client.zcard(key)
		assert zcard == 100

	async def test_ranking_after_concurrent_updates(self, lb_svc, redis_client):
		"""Dense ranking is correct after concurrent multi-player updates."""
		players = [
			(f"PLAYER-TEST-RANK-{i:04d}", (50 - i) * 10)
			for i in range(50)
		]

		tasks = [
			lb_svc.update_leaderboards(pid, xp_amount=xp, plan_id=self.PLAN)
			for pid, xp in players
		]
		await asyncio.gather(*tasks)

		top = await lb_svc.get_top("daily", limit=50, plan_id=self.PLAN)

		# 50 unique XP values → 50 unique ranks
		ranks = [e["rank"] for e in top]
		assert ranks == list(range(1, 51))

	async def test_concurrent_mixed_operations(self, lb_svc, redis_client):
		"""Concurrent updates + reads don't cause errors. See FINDING-11."""
		players = [
			(f"PLAYER-TEST-MIX-{i:04d}", 10 * (i + 1))
			for i in range(50)
		]

		# First seed all players
		for pid, xp in players:
			await lb_svc.update_leaderboards(pid, xp_amount=xp, plan_id=self.PLAN)

		# Then do concurrent reads + writes
		async def update_and_read(pid, xp):
			await lb_svc.update_leaderboards(pid, xp_amount=5, plan_id=self.PLAN)
			result = await lb_svc.get_my_rank(pid, "daily", plan_id=self.PLAN)
			return result

		tasks = [update_and_read(pid, xp) for pid, xp in players]
		results = await asyncio.gather(*tasks)

		# All results should be valid (no None, no exceptions)
		for i, result in enumerate(results):
			assert result is not None, f"Player {players[i][0]} got None result"
			assert result["rank"] is not None, f"Player {players[i][0]} got None rank"
			assert result["total_players"] == 50


class TestConcurrentTieFormation:
	"""Concurrent updates that create tie groups."""

	PLAN = "PLAN-TEST-TIES"

	async def test_concurrent_tie_creation(self, lb_svc, redis_client):
		"""50 players all getting 100 XP concurrently → all tied at rank 1."""
		players = [f"PLAYER-TEST-TIE-{i:04d}" for i in range(50)]

		tasks = [
			lb_svc.update_leaderboards(pid, xp_amount=100, plan_id=self.PLAN)
			for pid in players
		]
		await asyncio.gather(*tasks)

		top = await lb_svc.get_top("daily", limit=50, plan_id=self.PLAN)
		assert len(top) == 50
		assert all(e["rank"] == 1 for e in top), "All tied players should be rank 1"

	async def test_concurrent_updates_then_rank_check(self, lb_svc, redis_client):
		"""Concurrent updates followed by concurrent rank checks."""
		players_xp = {
			f"PLAYER-TEST-RC-{i:04d}": random.randint(1, 500)
			for i in range(100)
		}

		# Concurrent updates
		update_tasks = [
			lb_svc.update_leaderboards(pid, xp_amount=xp, plan_id=self.PLAN)
			for pid, xp in players_xp.items()
		]
		await asyncio.gather(*update_tasks)

		# Concurrent rank checks
		rank_tasks = [
			lb_svc.get_my_rank(pid, "daily", plan_id=self.PLAN)
			for pid in players_xp
		]
		results = await asyncio.gather(*rank_tasks)

		# Verify all results are valid
		for pid, result in zip(players_xp.keys(), results):
			assert result["xp"] == players_xp[pid], (
				f"{pid}: expected {players_xp[pid]} XP, got {result['xp']}"
			)
			assert result["rank"] is not None
			assert result["rank"] >= 1
			assert result["total_players"] == 100


class TestPipelineAtomicity:
	"""Verify pipeline behavior under concurrent load. See FINDING-10."""

	PLAN = "PLAN-TEST-PIPE"

	async def test_global_and_plan_keys_consistent(self, lb_svc, redis_client):
		"""After concurrent updates, global and plan-scoped keys have same scores.

		Each update_leaderboards call writes to both global and plan-scoped keys
		in the same pipeline. Under concurrent load, both should stay consistent.
		"""
		player = "PLAYER-TEST-PIPE-001"

		tasks = [
			lb_svc.update_leaderboards(player, xp_amount=10, plan_id=self.PLAN)
			for _ in range(50)
		]
		await asyncio.gather(*tasks)

		# Global key
		global_key = lb_svc._get_key("daily")
		global_score = await redis_client.zscore(global_key, player)

		# Plan key
		plan_key = lb_svc._get_plan_key("daily", self.PLAN)
		plan_score = await redis_client.zscore(plan_key, player)

		assert int(global_score) == 500
		assert int(plan_score) == 500
		assert global_score == plan_score, (
			f"Global ({global_score}) and plan ({plan_score}) scores diverged!"
		)

	async def test_daily_and_weekly_consistent(self, lb_svc, redis_client):
		"""Daily and weekly keys have same scores after concurrent updates."""
		player = "PLAYER-TEST-PIPE-002"

		tasks = [
			lb_svc.update_leaderboards(player, xp_amount=7, plan_id=self.PLAN)
			for _ in range(100)
		]
		await asyncio.gather(*tasks)

		daily_result = await lb_svc.get_my_rank(player, "daily", plan_id=self.PLAN)
		weekly_result = await lb_svc.get_my_rank(player, "weekly", plan_id=self.PLAN)

		assert daily_result["xp"] == 700
		assert weekly_result["xp"] == 700


class TestConcurrentBurstLoad:
	"""Burst load simulation — many operations in tight window."""

	PLAN = "PLAN-TEST-BURST"

	async def test_burst_200_updates_50_reads(self, lb_svc, redis_client):
		"""200 concurrent updates + 50 concurrent reads complete without error."""
		random.seed(42)

		# Seed some players first
		players = [f"PLAYER-TEST-BURST-{i:04d}" for i in range(50)]
		for pid in players:
			await lb_svc.update_leaderboards(pid, xp_amount=100, plan_id=self.PLAN)

		# Burst: 200 updates + 50 reads
		update_tasks = [
			lb_svc.update_leaderboards(
				random.choice(players), xp_amount=random.randint(1, 50), plan_id=self.PLAN
			)
			for _ in range(200)
		]
		read_tasks = [
			lb_svc.get_my_rank(random.choice(players), "daily", plan_id=self.PLAN)
			for _ in range(50)
		]

		all_tasks = update_tasks + read_tasks
		random.shuffle(all_tasks)

		results = await asyncio.gather(*all_tasks, return_exceptions=True)

		# No exceptions
		exceptions = [r for r in results if isinstance(r, Exception)]
		assert len(exceptions) == 0, f"Burst load produced exceptions: {exceptions}"
