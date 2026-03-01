"""Zero, negative, and extreme XP edge case tests.

Validates:
- XP=0 does not create ghost ZSET members
- XP<0 is a no-op
- Large XP spikes don't cause float drift
- XP overflow behavior (IEEE 754 double precision)

FINDINGS:
┌─────────────────────────────────────────────────────────────────────────┐
│ FINDING-7: Redis ZINCRBY stores scores as IEEE 754 doubles. Integer    │
│ XP values are safe up to 2^53 (9,007,199,254,740,992). Beyond that,   │
│ precision is lost. For a system capping at 100k concurrent users,      │
│ even extreme XP (10M per user) stays well within safe range.           │
│                                                                         │
│ FINDING-8: The xp_amount <= 0 guard in update_leaderboards() means     │
│ there's NO way to decrement XP in the leaderboard. If a future feature │
│ requires XP deduction (penalty, refund), the guard must be modified.   │
│ Currently safe — XP is additive only.                                   │
│                                                                         │
│ FINDING-9: ZINCRBY with float XP (e.g., 3.7) would create a           │
│ fractional score. The Lua script uses math.floor(), so dense ranking   │
│ would treat 3.0 and 3.7 as the same tier (both floor to 3). Currently │
│ safe because xp_amount is typed as int, but no runtime check exists.   │
└─────────────────────────────────────────────────────────────────────────┘
"""

import pytest

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


class TestZeroXP:
	"""XP=0 must not create ghost ZSET members."""

	PLAN = "PLAN-TEST-ZERO"

	async def test_zero_xp_no_zset_member(self, lb_svc, redis_client):
		"""update_leaderboards(xp=0) must not add player to any ZSET."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-ZERO-001", xp_amount=0, plan_id=self.PLAN
		)

		# No leaderboard keys should exist
		cursor = 0
		all_keys = []
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:*", count=100)
			all_keys.extend(keys)
			if cursor == 0:
				break
		assert len(all_keys) == 0, f"0-XP created keys: {all_keys}"

	async def test_zero_xp_no_daily_xp_hash(self, lb_svc, redis_client):
		"""update_leaderboards(xp=0) must not create daily_xp hash entry."""
		from fastapi_app.core.redis_keys import daily_xp_key

		await lb_svc.update_leaderboards(
			"PLAYER-TEST-ZERO-002", xp_amount=0, plan_id=self.PLAN
		)

		dxp_key = daily_xp_key("PLAYER-TEST-ZERO-002")
		exists = await redis_client.exists(dxp_key)
		assert exists == 0

	async def test_zero_xp_after_valid_xp_no_increment(self, lb_svc, redis_client):
		"""0-XP update after valid update must not change the score."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-ZERO-003", xp_amount=100, plan_id=self.PLAN
		)
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-ZERO-003", xp_amount=0, plan_id=self.PLAN
		)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-ZERO-003", "daily", plan_id=self.PLAN
		)
		assert result["xp"] == 100

	async def test_zset_cardinality_not_inflated(self, lb_svc, redis_client):
		"""ZCARD must not include phantom 0-XP members."""
		# Add 5 real players
		for i in range(5):
			await lb_svc.update_leaderboards(
				f"PLAYER-TEST-CARD-{i:03d}", xp_amount=10 * (i + 1), plan_id=self.PLAN
			)
		# Attempt 10 zero-XP updates
		for i in range(10):
			await lb_svc.update_leaderboards(
				f"PLAYER-TEST-GHOST-{i:03d}", xp_amount=0, plan_id=self.PLAN
			)

		key = lb_svc._get_plan_key("daily", self.PLAN)
		zcard = await redis_client.zcard(key)
		assert zcard == 5, f"Expected 5 members, got {zcard} (ghosts leaked)"


class TestNegativeXP:
	"""Negative XP must be a no-op."""

	PLAN = "PLAN-TEST-NEG"

	async def test_negative_xp_no_write(self, lb_svc, redis_client):
		"""update_leaderboards(xp=-5) must not write anything."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-NEG-001", xp_amount=-5, plan_id=self.PLAN
		)

		cursor = 0
		all_keys = []
		while True:
			cursor, keys = await redis_client.scan(cursor, match="memora:lb:*", count=100)
			all_keys.extend(keys)
			if cursor == 0:
				break
		assert len(all_keys) == 0

	async def test_negative_xp_does_not_decrement(self, lb_svc, redis_client):
		"""Negative XP after valid update does not reduce score. See FINDING-8."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-NEG-002", xp_amount=100, plan_id=self.PLAN
		)
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-NEG-002", xp_amount=-50, plan_id=self.PLAN
		)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-NEG-002", "daily", plan_id=self.PLAN
		)
		assert result["xp"] == 100, "Negative XP should not decrement score"

	async def test_large_negative_xp(self, lb_svc, redis_client):
		"""Very large negative XP is still a no-op."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-NEG-003", xp_amount=50, plan_id=self.PLAN
		)
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-NEG-003", xp_amount=-999999999, plan_id=self.PLAN
		)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-NEG-003", "daily", plan_id=self.PLAN
		)
		assert result["xp"] == 50


class TestLargeXPSpike:
	"""Large XP values and accumulation edge cases."""

	PLAN = "PLAN-TEST-LARGE"

	async def test_large_single_xp(self, lb_svc, redis_client):
		"""Single large XP award (1M) works correctly."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-LRG-001", xp_amount=1_000_000, plan_id=self.PLAN
		)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-LRG-001", "daily", plan_id=self.PLAN
		)
		assert result["xp"] == 1_000_000

	async def test_large_accumulated_xp(self, lb_svc, redis_client):
		"""1000 increments of 1000 XP = exactly 1,000,000 (no float drift)."""
		for _ in range(1000):
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-LRG-002", xp_amount=1000, plan_id=self.PLAN
			)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-LRG-002", "daily", plan_id=self.PLAN
		)
		assert result["xp"] == 1_000_000

	async def test_ranking_with_large_xp_spread(self, lb_svc, redis_client):
		"""Ranking works with XP ranging from 1 to 10M."""
		players = [
			("PLAYER-TEST-SPREAD-001", 10_000_000),
			("PLAYER-TEST-SPREAD-002", 5_000_000),
			("PLAYER-TEST-SPREAD-003", 1_000),
			("PLAYER-TEST-SPREAD-004", 1),
		]
		for pid, xp in players:
			await lb_svc.update_leaderboards(pid, xp_amount=xp, plan_id=self.PLAN)

		top = await lb_svc.get_top("daily", limit=10, plan_id=self.PLAN)
		assert len(top) == 4
		assert [e["rank"] for e in top] == [1, 2, 3, 4]
		assert [e["xp"] for e in top] == [10_000_000, 5_000_000, 1_000, 1]


class TestFloatPrecision:
	"""IEEE 754 double precision edge cases. See FINDING-7."""

	PLAN = "PLAN-TEST-FLOAT"

	async def test_integer_precision_safe_range(self, lb_svc, redis_client):
		"""XP values within 2^53 are represented exactly."""
		# 2^53 = 9,007,199,254,740,992 — max safe integer for IEEE 754 double
		safe_xp = 9_000_000_000_000_000  # Well within safe range
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-FP-001", xp_amount=safe_xp, plan_id=self.PLAN
		)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-FP-001", "daily", plan_id=self.PLAN
		)
		assert result["xp"] == safe_xp

	async def test_many_small_increments_no_drift(self, lb_svc, redis_client):
		"""10,000 × 1 XP = exactly 10,000 (no float accumulation error)."""
		for _ in range(10_000):
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-FP-002", xp_amount=1, plan_id=self.PLAN
			)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-FP-002", "daily", plan_id=self.PLAN
		)
		assert result["xp"] == 10_000

	async def test_xp_1_increment_is_exactly_one(self, lb_svc, redis_client):
		"""Each ZINCRBY of 1 adds exactly 1, verifiable via ZSCORE."""
		for i in range(100):
			await lb_svc.update_leaderboards(
				"PLAYER-TEST-FP-003", xp_amount=1, plan_id=self.PLAN
			)

		key = lb_svc._get_plan_key("daily", self.PLAN)
		raw_score = await redis_client.zscore(key, "PLAYER-TEST-FP-003")
		# float(100) should be exactly 100.0
		assert raw_score == 100.0
		assert int(raw_score) == 100


class TestXPAmountEdgeCases:
	"""Boundary values for xp_amount parameter."""

	PLAN = "PLAN-TEST-EDGE"

	async def test_xp_amount_1(self, lb_svc, redis_client):
		"""Minimum valid XP amount (1) works."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-MIN-001", xp_amount=1, plan_id=self.PLAN
		)
		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-MIN-001", "daily", plan_id=self.PLAN
		)
		assert result["xp"] == 1
		assert result["rank"] == 1

	async def test_xp_boundary_at_zero(self, lb_svc, redis_client):
		"""xp_amount=0 is the exact boundary — must NOT write."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-BND-001", xp_amount=0, plan_id=self.PLAN
		)
		key = lb_svc._get_plan_key("daily", self.PLAN)
		exists = await redis_client.exists(key)
		assert exists == 0

	async def test_unranked_player_response(self, lb_svc, redis_client):
		"""Player with no XP in period: rank=None, xp=0, neighbors=[]."""
		# Add another player so the leaderboard exists
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-OTHER", xp_amount=50, plan_id=self.PLAN
		)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-UNRANKED", "daily", plan_id=self.PLAN
		)
		assert result["rank"] is None
		assert result["xp"] == 0
		assert result["neighbors"] == []
		assert result["total_players"] == 1

	async def test_unranked_xp_to_next_is_lowest_score(self, lb_svc, redis_client):
		"""Unranked player's xp_to_next equals the lowest score in the ZSET."""
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-HIRANK", xp_amount=500, plan_id=self.PLAN
		)
		await lb_svc.update_leaderboards(
			"PLAYER-TEST-LORANK", xp_amount=100, plan_id=self.PLAN
		)

		result = await lb_svc.get_my_rank(
			"PLAYER-TEST-NEWCOMER", "daily", plan_id=self.PLAN
		)
		assert result["rank"] is None
		assert result["xp_to_next"] == 100  # Lowest score in ZSET
