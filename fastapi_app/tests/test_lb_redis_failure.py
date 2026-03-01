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

import pytest

from fastapi_app.core.redis_keys import (
	lb_daily_key,
	lb_daily_plan_key,
	lb_weekly_key,
	lb_weekly_plan_key,
)
from fastapi_app.services.leaderboard import LeaderboardService


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

		# Simulate flush: delete all lb keys
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:*", count=1000)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

		# After flush: empty
		top_after = await lb_svc.get_top("daily", limit=10, plan_id=self.PLAN)
		assert top_after == []

	async def test_get_my_rank_after_flush_returns_unranked(self, lb_svc, redis_client):
		"""After flush, get_my_rank returns unranked response."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-FL-ME", xp_amount=500, plan_id=self.PLAN
		)

		# Delete all lb keys
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:*", count=1000)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

		result = await lb_svc.get_my_rank("PLAYER-TEST-FL-ME", "daily", plan_id=self.PLAN)
		assert result["rank"] is None
		assert result["xp"] == 0
		assert result["neighbors"] == []
		assert result["total_players"] == 0

	async def test_recovery_after_flush(self, lb_svc, redis_client):
		"""New XP updates after flush rebuild rankings organically."""
		# Initial data
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-FL-REC", xp_amount=500, plan_id=self.PLAN
		)

		# Flush
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:*", count=1000)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

		# New updates rebuild
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-FL-REC", xp_amount=200, plan_id=self.PLAN
		)

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
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-PL-001", xp_amount=100, plan_id=self.PLAN
		)

		# Delete only daily keys
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:daily:*", count=100)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

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
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-PL-002", xp_amount=100, plan_id=self.PLAN
		)

		# Delete only weekly keys
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:weekly:*", count=100)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

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
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-PL-003", xp_amount=100, plan_id=self.PLAN
		)

		# Delete only plan-scoped keys
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:*:plan:*", count=100)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

		# Plan-scoped: empty
		plan_result = await lb_svc.get_my_rank(
			"PLAYER-TEST-PL-003", "daily", plan_id=self.PLAN
		)
		assert plan_result["rank"] is None

		# Global: intact
		global_result = await lb_svc.get_my_rank("PLAYER-TEST-PL-003", "daily")
		assert global_result is not None
		assert global_result["xp"] == 100

	async def test_global_lost_plan_intact(self, lb_svc, redis_client):
		"""Global key deleted but plan-scoped key survives."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-PL-004", xp_amount=100, plan_id=self.PLAN
		)

		# Delete only global keys (no :plan: in name)
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:daily:????-??-??", count=100)
			# Filter to only truly global keys (no :plan: suffix)
			global_keys = [k for k in keys if ":plan:" not in k and ":subject:" not in k]
			if global_keys:
				await redis_client.delete(*global_keys)
			if cursor == 0:
				break

		# Plan-scoped: still works
		plan_result = await lb_svc.get_my_rank(
			"PLAYER-TEST-PL-004", "daily", plan_id=self.PLAN
		)
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
			"PLAYER-TEST-SL-001", xp_amount=100,
			subject_id="SUBJ-TEST-001", plan_id=self.PLAN,
		)

		# Delete only subject-scoped keys
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:*:subject:*", count=100)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

		# Subject query: empty
		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-SL-001", "daily",
			subject_id="SUBJ-TEST-001", plan_id=self.PLAN,
		)
		assert result["rank"] is None

		# Non-subject query: intact
		result_global = await lb_svc.get_my_rank(
			"PLAYER-TEST-SL-001", "daily", plan_id=self.PLAN,
		)
		assert result_global["rank"] == 1
		assert result_global["xp"] == 100


class TestNewUpdatesAfterPartialLoss:
	"""Verify system recovers organically after partial data loss."""

	PLAN = "PLAN-TEST-RECOVER"

	async def test_new_xp_after_daily_loss_rebuilds_daily(self, lb_svc, redis_client):
		"""New XP updates after daily key loss rebuild the daily leaderboard."""
		# Initial: 100 XP
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-RCV-001", xp_amount=100, plan_id=self.PLAN
		)

		# Lose daily keys
		cursor = 0
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:daily:*", count=100)
			if keys:
				await redis_client.delete(*keys)
			if cursor == 0:
				break

		# New update: 50 XP
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-RCV-001", xp_amount=50, plan_id=self.PLAN
		)

		# Daily shows only 50 (the 100 is lost)
		daily = await lb_svc.get_my_rank("PLAYER-TEST-RCV-001", "daily", plan_id=self.PLAN)
		assert daily["xp"] == 50

		# Weekly still shows 150 (100 + 50 — weekly was not lost)
		weekly = await lb_svc.get_my_rank("PLAYER-TEST-RCV-001", "weekly", plan_id=self.PLAN)
		assert weekly["xp"] == 150
