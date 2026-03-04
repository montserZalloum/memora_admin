"""Redis failure simulation tests for leaderboard system.

Simulates:
- Redis key loss (FLUSHDB-like scenario via selective DEL)
- Partial key loss (daily exists but weekly missing, or vice versa)
- Missing plan-scoped key but global exists
- Behavior after key expiry

FINDINGS:
┌─────────────────────────────────────────────────────────────────────────┐
│ FINDING-13: Leaderboard data has NO self-healing mechanism. Unlike     │
│ access/progress/wallet (which have ensure_hydrated() that restores     │
│ from MariaDB), leaderboard data is Redis-only. If Redis is flushed,   │
│ all leaderboard rankings are permanently lost. The system degrades     │
│ gracefully (returns empty/unranked) but data is gone.                  │
│                                                                         │
│ RISK ANALYSIS:                                                          │
│ - Memora Redis (13001) is isolated from Frappe Redis (13000)           │
│ - AOF persistence with everysec — max 1s data loss on crash            │
│ - TTL-based expiry: daily=30d, weekly=90d, plan daily=48h              │
│ - Worst case: Redis restart without AOF → all rankings lost            │
│ - Impact: Players see empty leaderboards, rankings rebuild organically │
│   as players earn XP. Historical rankings are lost.                    │
│                                                                         │
│ FINDING-14: Missing daily key but weekly exists is a normal state.     │
│ Daily keys have 30d TTL while weekly has 90d. After 30 days, daily    │
│ keys expire but weekly survives. This is by design — old daily data    │
│ is irrelevant. The system handles this correctly.                      │
│                                                                         │
│ FINDING-15: Plan-scoped keys have shorter TTL (48h daily, 8d weekly)   │
│ than global keys (30d/90d). A player querying plan-scoped leaderboard  │
│ after 49 hours would see empty results even though global data exists. │
│ This is by design — plan leaderboards reset faster.                    │
└─────────────────────────────────────────────────────────────────────────┘
"""

import asyncio

import pytest
from redis.asyncio.client import Pipeline

from fastapi_app.core.redis_keys import (
	lb_daily_key,
	lb_daily_plan_key,
	lb_weekly_key,
	lb_weekly_plan_key,
	lbmeta_keys_from_lb_key,
)
from fastapi_app.services.leaderboard import LeaderboardService


@pytest.fixture
async def lb_svc(redis_client):
	return LeaderboardService(redis_client)


async def _scan_delete(redis_client, pattern):
	"""Helper to SCAN+DELETE all keys matching a pattern."""
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


class TestFullKeyLoss:
	"""Simulate complete leaderboard data loss (FLUSHDB-like)."""

	PLAN = "PLAN-TEST-FLUSH"

	async def test_get_top_after_flush_returns_empty(self, lb_svc, redis_client):
		"""After all keys deleted, get_top returns empty list."""
		# Seed data
		for i in range(10):
			await lb_svc.update_leaderboards(
				f"PLAYER-TEST-FL-{i:03d}", xp_amount=100 * (i + 1), plan_id=self.PLAN
			)

		# Verify data exists
		top = await lb_svc.get_top("daily", limit=10, plan_id=self.PLAN)
		assert len(top) == 10

		# Simulate flush: delete all lb + lbmeta keys
		for pattern in ("memora:lb:*", "memora:lbmeta:*"):
			await _scan_delete(redis_client, pattern)

		# After flush: empty
		top_after = await lb_svc.get_top("daily", limit=10, plan_id=self.PLAN)
		assert top_after == []

	async def test_get_my_rank_after_flush_returns_unranked(self, lb_svc, redis_client):
		"""After flush, get_my_rank returns unranked response."""
		await lb_svc.update_leaderboards("PLAYER-TEST-FL-ME", xp_amount=500, plan_id=self.PLAN)

		# Delete all lb + lbmeta keys
		for pattern in ("memora:lb:*", "memora:lbmeta:*"):
			await _scan_delete(redis_client, pattern)

		result = await lb_svc.get_my_rank("PLAYER-TEST-FL-ME", "daily", plan_id=self.PLAN)
		assert result["rank"] is None
		assert result["xp"] == 0
		assert result["neighbors"] == []
		assert result["total_players"] == 0

	async def test_recovery_after_flush(self, lb_svc, redis_client):
		"""New XP updates after flush rebuild rankings organically."""
		# Initial data
		await lb_svc.update_leaderboards("PLAYER-TEST-FL-REC", xp_amount=500, plan_id=self.PLAN)

		# Flush all lb + lbmeta keys
		for pattern in ("memora:lb:*", "memora:lbmeta:*"):
			await _scan_delete(redis_client, pattern)

		# New updates rebuild
		await lb_svc.update_leaderboards("PLAYER-TEST-FL-REC", xp_amount=200, plan_id=self.PLAN)

		result = await lb_svc.get_my_rank("PLAYER-TEST-FL-REC", "daily", plan_id=self.PLAN)
		# Only 200 XP (not 700) — historical 500 is lost. See FINDING-13.
		assert result["xp"] == 200
		assert result["rank"] == 1


class TestPartialKeyLoss:
	"""Simulate selective key deletion (daily lost, weekly intact, or vice versa)."""

	PLAN = "PLAN-TEST-PARTIAL"

	async def test_daily_lost_weekly_intact(self, lb_svc, redis_client):
		"""Daily key deleted but weekly survives — daily returns empty, weekly returns data.

		See FINDING-14: This is a normal state after daily TTL expires.
		"""
		await lb_svc.update_leaderboards("PLAYER-TEST-PL-001", xp_amount=100, plan_id=self.PLAN)

		# Delete only daily keys (lb + lbmeta)
		for pattern in ("memora:lb:daily:*", "memora:lbmeta:daily:*"):
			await _scan_delete(redis_client, pattern)

		# Daily: empty
		daily_result = await lb_svc.get_my_rank("PLAYER-TEST-PL-001", "daily", plan_id=self.PLAN)
		assert daily_result["rank"] is None
		assert daily_result["xp"] == 0

		# Weekly: intact
		weekly_result = await lb_svc.get_my_rank("PLAYER-TEST-PL-001", "weekly", plan_id=self.PLAN)
		assert weekly_result["rank"] == 1
		assert weekly_result["xp"] == 100

	async def test_weekly_lost_daily_intact(self, lb_svc, redis_client):
		"""Weekly key deleted but daily survives."""
		await lb_svc.update_leaderboards("PLAYER-TEST-PL-002", xp_amount=100, plan_id=self.PLAN)

		# Delete only weekly keys (lb + lbmeta)
		for pattern in ("memora:lb:weekly:*", "memora:lbmeta:weekly:*"):
			await _scan_delete(redis_client, pattern)

		daily_result = await lb_svc.get_my_rank("PLAYER-TEST-PL-002", "daily", plan_id=self.PLAN)
		assert daily_result["rank"] == 1
		assert daily_result["xp"] == 100

		weekly_result = await lb_svc.get_my_rank("PLAYER-TEST-PL-002", "weekly", plan_id=self.PLAN)
		assert weekly_result["rank"] is None

	async def test_plan_key_lost_global_intact(self, lb_svc, redis_client):
		"""Plan-scoped key deleted but global key survives.

		See FINDING-15: Plan keys have shorter TTL. This simulates
		plan key expiry while global key is still alive.
		"""
		await lb_svc.update_leaderboards("PLAYER-TEST-PL-003", xp_amount=100, plan_id=self.PLAN)

		# Delete only plan-scoped keys (lb + lbmeta)
		for pattern in ("memora:lb:*:plan:*", "memora:lbmeta:*:plan:*"):
			await _scan_delete(redis_client, pattern)

		# Plan-scoped: empty
		plan_result = await lb_svc.get_my_rank("PLAYER-TEST-PL-003", "daily", plan_id=self.PLAN)
		assert plan_result["rank"] is None

		# Global: intact
		global_result = await lb_svc.get_my_rank("PLAYER-TEST-PL-003", "daily")
		assert global_result is not None
		assert global_result["xp"] == 100

	async def test_global_lost_plan_intact(self, lb_svc, redis_client):
		"""Global key deleted but plan-scoped key survives."""
		await lb_svc.update_leaderboards("PLAYER-TEST-PL-004", xp_amount=100, plan_id=self.PLAN)

		# Delete only global keys (no :plan: in name) — lb + lbmeta
		for prefix in ("memora:lb:daily:????-??-??", "memora:lbmeta:daily:????-??-??"):
			cursor = 0
			while True:
				cursor, keys = await redis_client.scan(cursor, match=prefix, count=100)
				# Filter to only truly global keys (no :plan: suffix)
				global_keys = [k for k in keys if ":plan:" not in k and ":subject:" not in k]
				if global_keys:
					await redis_client.delete(*global_keys)
				if cursor == 0:
					break

		# Plan-scoped: still works
		plan_result = await lb_svc.get_my_rank("PLAYER-TEST-PL-004", "daily", plan_id=self.PLAN)
		assert plan_result["rank"] == 1
		assert plan_result["xp"] == 100


class TestSingleMemberRemoval:
	"""Simulate individual player removal from ZSET."""

	PLAN = "PLAN-TEST-REM"

	async def test_removed_player_ranks_update(self, lb_svc, redis_client):
		"""After ZREM of a player, remaining players' ranks adjust correctly."""
		players = [
			("PLAYER-TEST-REM-A", 500),
			("PLAYER-TEST-REM-B", 300),
			("PLAYER-TEST-REM-C", 100),
		]
		for pid, xp in players:
			await lb_svc.update_leaderboards(pid, xp_amount=xp, plan_id=self.PLAN)

		# Remove middle player
		key = lb_svc._get_plan_key("daily", self.PLAN)
		await redis_client.zrem(key, "PLAYER-TEST-REM-B")

		# Remaining: A=500 (rank 1), C=100 (rank 2)
		top = await lb_svc.get_top("daily", limit=10, plan_id=self.PLAN)
		assert len(top) == 2
		assert top[0]["player_id"] == "PLAYER-TEST-REM-A"
		assert top[0]["rank"] == 1
		assert top[1]["player_id"] == "PLAYER-TEST-REM-C"
		assert top[1]["rank"] == 2

	async def test_removed_player_total_updates(self, lb_svc, redis_client):
		"""After ZREM, total_players count decreases."""
		for i in range(10):
			await lb_svc.update_leaderboards(
				f"PLAYER-TEST-REM-{i:03d}", xp_amount=10 * (i + 1), plan_id=self.PLAN
			)

		key = lb_svc._get_plan_key("daily", self.PLAN)
		await redis_client.zrem(key, "PLAYER-TEST-REM-005")

		result = await lb_svc.get_my_rank("PLAYER-TEST-REM-000", "daily", plan_id=self.PLAN)
		assert result["total_players"] == 9


class TestSubjectKeyLoss:
	"""Subject-scoped key loss while global subject-less key is intact."""

	PLAN = "PLAN-TEST-SUBJ-LOSS"

	async def test_subject_key_lost(self, lb_svc, redis_client):
		"""Subject-filtered query returns empty when subject key is lost."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-SL-001",
			xp_amount=100,
			subject_id="SUBJ-TEST-001",
			plan_id=self.PLAN,
		)

		# Delete only subject-scoped keys (lb + lbmeta)
		for pattern in ("memora:lb:*:subject:*", "memora:lbmeta:*:subject:*"):
			await _scan_delete(redis_client, pattern)

		# Subject query: empty
		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-SL-001",
			"daily",
			subject_id="SUBJ-TEST-001",
			plan_id=self.PLAN,
		)
		assert result["rank"] is None

		# Non-subject query: intact
		result_global = await lb_svc.get_my_rank(
			"PLAYER-TEST-SL-001",
			"daily",
			plan_id=self.PLAN,
		)
		assert result_global["rank"] == 1
		assert result_global["xp"] == 100


class TestNewUpdatesAfterPartialLoss:
	"""Verify system recovers organically after partial data loss."""

	PLAN = "PLAN-TEST-RECOVER"

	async def test_new_xp_after_daily_loss_rebuilds_daily(self, lb_svc, redis_client):
		"""New XP updates after daily key loss rebuild the daily leaderboard."""
		# Initial: 100 XP
		await lb_svc.update_leaderboards("PLAYER-TEST-RCV-001", xp_amount=100, plan_id=self.PLAN)

		# Lose daily keys (lb + lbmeta)
		for pattern in ("memora:lb:daily:*", "memora:lbmeta:daily:*"):
			await _scan_delete(redis_client, pattern)

		# New update: 50 XP
		await lb_svc.update_leaderboards("PLAYER-TEST-RCV-001", xp_amount=50, plan_id=self.PLAN)

		# Daily shows only 50 (the 100 is lost)
		daily = await lb_svc.get_my_rank("PLAYER-TEST-RCV-001", "daily", plan_id=self.PLAN)
		assert daily["xp"] == 50

		# Weekly still shows 150 (100 + 50 — weekly was not lost)
		weekly = await lb_svc.get_my_rank("PLAYER-TEST-RCV-001", "weekly", plan_id=self.PLAN)
		assert weekly["xp"] == 150


class TestPartialMetadataRollout:
	"""Reproduce QA finding: partial tier index on pre-existing leaderboards.

	Scenario: A leaderboard exists from before the tier-index deploy (no metadata).
	The first post-deploy write must NOT create a partial tieridx, because the
	read path now repairs metadata on demand instead of treating missing metadata
	as a steady-state fallback condition.
	"""

	PLAN = "PLAN-TEST-ROLLOUT"

	async def test_write_to_existing_board_does_not_create_partial_index(self, lb_svc, redis_client):
		"""Write path stays conservative; first read repairs the missing metadata."""
		# Simulate pre-deploy board: 3 players seeded directly (no tier metadata)
		key = lb_svc._get_plan_key("daily", self.PLAN)
		await redis_client.zadd(key, {"REVIEW-A": 100, "REVIEW-B": 50, "REVIEW-C": 10})
		await redis_client.expire(key, 3600)

		# Verify pre-state: no metadata exists
		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)
		assert await redis_client.exists(tieridx_key) == 0
		assert await redis_client.exists(tiercnt_key) == 0

		# Post-deploy write: award XP to REVIEW-C (existing member)
		await lb_svc.update_leaderboards("REVIEW-C", xp_amount=5, plan_id=self.PLAN)

		# Critical assertion: write path still does NOT create a partial index.
		assert (
			await redis_client.exists(tieridx_key) == 0
		), "tieridx was created on write to pre-existing board — partial index bug!"
		assert await redis_client.exists(tiercnt_key) == 0

		# First read should repair metadata and still return the correct rank.
		result_after = await lb_svc.get_my_rank("REVIEW-B", "daily", plan_id=self.PLAN)
		assert result_after["rank"] == 2
		assert result_after["xp"] == 50
		assert await redis_client.exists(tieridx_key) == 1
		assert await redis_client.exists(tiercnt_key) == 1

	async def test_new_player_on_existing_board_no_partial_index(self, lb_svc, redis_client):
		"""A new member on an unindexed board still repairs on read, not write."""
		key = lb_svc._get_plan_key("daily", self.PLAN)
		await redis_client.zadd(key, {"EXISTING-A": 200, "EXISTING-B": 100})
		await redis_client.expire(key, 3600)

		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)

		# New player joins via service
		await lb_svc.update_leaderboards("NEW-PLAYER", xp_amount=50, plan_id=self.PLAN)

		# Should NOT create metadata (board had pre-existing members)
		assert await redis_client.exists(tieridx_key) == 0

		# First read repairs metadata and returns the correct rank.
		result = await lb_svc.get_my_rank("NEW-PLAYER", "daily", plan_id=self.PLAN)
		assert result["rank"] == 3  # Below 200 and 100
		assert await redis_client.exists(tieridx_key) == 1
		assert await redis_client.exists(tiercnt_key) == 1

	async def test_brand_new_board_bootstraps_metadata(self, lb_svc, redis_client):
		"""First player on a brand-new board SHOULD create metadata (not partial)."""
		# No pre-existing board — service creates it from scratch
		await lb_svc.update_leaderboards("FIRST-PLAYER", xp_amount=100, plan_id=self.PLAN)

		key = lb_svc._get_plan_key("daily", self.PLAN)
		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)

		# Metadata should exist (complete — only 1 player = 1 tier)
		assert await redis_client.exists(tieridx_key) == 1
		assert await redis_client.exists(tiercnt_key) == 1

		# Second player joins — metadata still maintained correctly
		await lb_svc.update_leaderboards("SECOND-PLAYER", xp_amount=50, plan_id=self.PLAN)

		# Verify correct ranks via indexed path
		r1 = await lb_svc.get_my_rank("FIRST-PLAYER", "daily", plan_id=self.PLAN)
		r2 = await lb_svc.get_my_rank("SECOND-PLAYER", "daily", plan_id=self.PLAN)
		assert r1["rank"] == 1
		assert r2["rank"] == 2

	async def test_backfill_then_write_uses_indexed_path(self, lb_svc, redis_client):
		"""After backfill populates metadata, writes maintain it and reads use indexed path."""
		# Pre-deploy board (no metadata)
		key = lb_svc._get_plan_key("daily", self.PLAN)
		await redis_client.zadd(key, {"BF-A": 100, "BF-B": 50, "BF-C": 10})
		await redis_client.expire(key, 3600)

		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)

		# Simulate backfill: manually create complete tier metadata
		await redis_client.zadd(tieridx_key, {"100": 100, "50": 50, "10": 10})
		await redis_client.hset(tiercnt_key, mapping={"100": "1", "50": "1", "10": "1"})
		await redis_client.expire(tieridx_key, 3600)
		await redis_client.expire(tiercnt_key, 3600)

		# Post-backfill write: should maintain metadata (tieridx exists)
		await lb_svc.update_leaderboards("BF-C", xp_amount=5, plan_id=self.PLAN)

		# tieridx should now reflect the tier change (10→15)
		tiers = await redis_client.zrange(tieridx_key, 0, -1, withscores=True)
		tier_scores = {int(s) for _, s in tiers}
		assert 15 in tier_scores, "New tier 15 should exist after write"
		assert 10 not in tier_scores, "Old tier 10 should be removed (last player left)"

		# Ranks via indexed path should be correct
		r_a = await lb_svc.get_my_rank("BF-A", "daily", plan_id=self.PLAN)
		r_b = await lb_svc.get_my_rank("BF-B", "daily", plan_id=self.PLAN)
		r_c = await lb_svc.get_my_rank("BF-C", "daily", plan_id=self.PLAN)
		assert r_a["rank"] == 1  # 100 XP
		assert r_b["rank"] == 2  # 50 XP
		assert r_c["rank"] == 3  # 15 XP

	async def test_tiercnt_loss_repairs_on_read(self, lb_svc, redis_client):
		"""Single-key loss leaves writes conservative and repairs the pair on read."""
		await lb_svc.update_leaderboards("CNT-A", xp_amount=100, plan_id=self.PLAN)
		await lb_svc.update_leaderboards("CNT-B", xp_amount=50, plan_id=self.PLAN)
		await lb_svc.update_leaderboards("CNT-C", xp_amount=50, plan_id=self.PLAN)
		await lb_svc.update_leaderboards("CNT-D", xp_amount=10, plan_id=self.PLAN)

		key = lb_svc._get_plan_key("daily", self.PLAN)
		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)

		# Simulate single-key loss: tieridx survives, tiercnt is missing.
		await redis_client.delete(tiercnt_key)

		tiers_before = await redis_client.zrange(tieridx_key, 0, -1, withscores=True)

		# A write should NOT mutate stale metadata while the pair is incomplete.
		await lb_svc.update_leaderboards("CNT-B", xp_amount=5, plan_id=self.PLAN)

		tiers_after = await redis_client.zrange(tieridx_key, 0, -1, withscores=True)
		assert tiers_after == tiers_before

		# Read path should rebuild the metadata pair from the authoritative ZSET.
		result = await lb_svc.get_my_rank("CNT-D", "daily", plan_id=self.PLAN)
		assert result["rank"] == 4  # 100, 55, 50 are all above 10
		assert result["xp_to_next"] == 40
		assert await redis_client.exists(tiercnt_key) == 1

		tiers_repaired = await redis_client.zrange(tieridx_key, 0, -1, withscores=True)
		tier_scores = {int(score) for _, score in tiers_repaired}
		assert tier_scores == {10, 50, 55, 100}

	async def test_concurrent_readers_share_single_repair(self, lb_svc, redis_client, monkeypatch):
		"""One reader repairs; the second waits and uses the repaired metadata."""
		key = lb_svc._get_plan_key("daily", self.PLAN)
		await redis_client.zadd(key, {"CON-A": 100, "CON-B": 50, "CON-C": 10})
		await redis_client.expire(key, 3600)

		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)
		rebuild_started = asyncio.Event()
		allow_rebuild = asyncio.Event()
		original_rebuild = lb_svc._rebuild_tier_metadata_locked

		async def delayed_rebuild(lb_key, idx_key, cnt_key):
			rebuild_started.set()
			await allow_rebuild.wait()
			return await original_rebuild(lb_key, idx_key, cnt_key)

		async def fail_legacy(*args, **kwargs):
			raise AssertionError("legacy fallback should not run when repair succeeds")

		monkeypatch.setattr(lb_svc, "_rebuild_tier_metadata_locked", delayed_rebuild)
		monkeypatch.setattr(lb_svc, "_legacy_script", fail_legacy)

		first = asyncio.create_task(lb_svc.get_my_rank("CON-A", "daily", plan_id=self.PLAN))
		await rebuild_started.wait()
		second = asyncio.create_task(lb_svc.get_my_rank("CON-B", "daily", plan_id=self.PLAN))
		await asyncio.sleep(0.01)
		allow_rebuild.set()

		result_a, result_b = await asyncio.gather(first, second)
		assert result_a["rank"] == 1
		assert result_b["rank"] == 2
		assert await redis_client.exists(tieridx_key) == 1
		assert await redis_client.exists(tiercnt_key) == 1

	async def test_repair_retries_when_write_lands_before_publish(self, lb_svc, redis_client, monkeypatch):
		"""A write during repair must not let stale metadata overwrite the board."""
		key = lb_svc._get_plan_key("daily", self.PLAN)
		await redis_client.zadd(key, {"RACE-A": 100, "RACE-B": 50, "RACE-C": 10})
		await redis_client.expire(key, 3600)

		tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)
		rebuild_ready = asyncio.Event()
		allow_publish = asyncio.Event()
		original_execute = Pipeline.execute
		delayed = {"done": False}

		async def delayed_execute(self, *args, **kwargs):
			stack_repr = str(self.command_stack).upper()
			if (
				not delayed["done"]
				and ":TIERIDX" in stack_repr
				and ":TIERCNT" in stack_repr
				and ("DEL" in stack_repr or "DELETE" in stack_repr)
			):
				delayed["done"] = True
				rebuild_ready.set()
				await allow_publish.wait()
			return await original_execute(self, *args, **kwargs)

		monkeypatch.setattr(Pipeline, "execute", delayed_execute)

		reader = asyncio.create_task(lb_svc.get_my_rank("RACE-A", "daily", plan_id=self.PLAN))
		await asyncio.wait_for(rebuild_ready.wait(), timeout=1.0)

		await lb_svc.update_leaderboards("RACE-D", xp_amount=55, plan_id=self.PLAN)
		allow_publish.set()

		result_a = await reader
		assert result_a["rank"] == 1

		result_b = await lb_svc.get_my_rank("RACE-B", "daily", plan_id=self.PLAN)
		assert result_b["rank"] == 3

		tiers = await redis_client.zrange(tieridx_key, 0, -1, withscores=True)
		tier_scores = {int(score) for _, score in tiers}
		assert tier_scores == {10, 50, 55, 100}
