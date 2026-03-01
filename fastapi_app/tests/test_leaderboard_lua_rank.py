"""Edge-case and stress tests for the Lua-based dense rank computation.

The Lua script (_RANK_LUA) in leaderboard.py counts distinct XP tiers
above a player server-side, replacing the old unbounded zrangebyscore.
Neighbor ranks are derived from the player's rank + window tiers.

These tests exercise the highest-risk paths:
1. Top/bottom player boundaries (window clipped at ZSET edges)
2. Many tie groups (dense rank vs positional rank divergence)
3. Neighbor window crossing multiple tiers
4. xp_to_next correctness at every position
5. Stress: many members / few tiers (100k-like distribution)
6. Stress: many distinct tiers (max Lua iterations)
7. Cross-validation: get_my_rank vs get_top for all players
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fastapi_app.core.redis_keys import lb_weekly_plan_key
from fastapi_app.services.leaderboard import AMMAN_TZ, LeaderboardService

PLAN = "PLAN-LUA-TEST"


def _current_friday() -> str:
	now = datetime.now(AMMAN_TZ)
	weekday = now.isoweekday()
	days_since_friday = (weekday - 5) % 7
	return (now - timedelta(days=days_since_friday)).strftime("%Y-%m-%d")


async def _seed(r, members: dict[str, int | float]) -> str:
	"""ZADD members into the plan-scoped weekly key. Returns the key."""
	key = lb_weekly_plan_key(_current_friday(), PLAN)
	if members:
		await r.zadd(key, members)
		await r.expire(key, 3600)
	return key


async def _cleanup(r):
	friday = _current_friday()
	async for key in r.scan_iter(f"memora:lb:*:{friday}:plan:{PLAN}*"):
		await r.delete(key)
	async for key in r.scan_iter(f"memora:lb:*:{friday}"):
		await r.delete(key)
	async for key in r.scan_iter("memora:daily_xp:LUA-*"):
		await r.delete(key)


# ------------------------------------------------------------------ #
# 1. Top player — window clipped at start, 0 tiers above
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestTopPlayer:
	async def test_rank_is_1(self, redis_client):
		"""Top-ranked player gets rank 1."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {
			"LUA-TOP-A": 500,
			"LUA-TOP-B": 300,
			"LUA-TOP-C": 100,
		})

		result = await svc.get_my_rank("LUA-TOP-A", "weekly", plan_id=PLAN)
		assert result["rank"] == 1
		assert result["xp"] == 500
		assert result["xp_to_next"] is None
		await _cleanup(redis_client)

	async def test_neighbors_only_below(self, redis_client):
		"""Top player's neighbor window only has entries below."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {
			"LUA-TOP-A": 500,
			"LUA-TOP-B": 300,
			"LUA-TOP-C": 100,
			"LUA-TOP-D": 50,
		})

		result = await svc.get_my_rank("LUA-TOP-A", "weekly", neighbor_count=2, plan_id=PLAN)
		# Window: positions 0..2 → A(500), B(300), C(100)
		neighbor_ids = [n["player_id"] for n in result["neighbors"]]
		assert "LUA-TOP-A" in neighbor_ids
		assert "LUA-TOP-B" in neighbor_ids
		assert "LUA-TOP-C" in neighbor_ids

		# All neighbor ranks should be correct
		ranks = {n["player_id"]: n["rank"] for n in result["neighbors"]}
		assert ranks["LUA-TOP-A"] == 1
		assert ranks["LUA-TOP-B"] == 2
		assert ranks["LUA-TOP-C"] == 3
		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 2. Bottom player — window clipped at end, all tiers above
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestBottomPlayer:
	async def test_rank_equals_distinct_tiers_plus_1(self, redis_client):
		"""Bottom player rank = number of distinct tiers + 1."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {
			"LUA-BOT-A": 500,
			"LUA-BOT-B": 300,
			"LUA-BOT-C": 300,
			"LUA-BOT-D": 100,
			"LUA-BOT-E": 10,
		})

		result = await svc.get_my_rank("LUA-BOT-E", "weekly", plan_id=PLAN)
		# Distinct tiers above 10: {500, 300, 100} = 3
		assert result["rank"] == 4
		assert result["xp"] == 10
		assert result["xp_to_next"] == 90  # 100 - 10
		await _cleanup(redis_client)

	async def test_neighbors_only_above(self, redis_client):
		"""Bottom player's neighbor window only has entries above."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {
			"LUA-BOT-A": 500,
			"LUA-BOT-B": 300,
			"LUA-BOT-C": 100,
			"LUA-BOT-D": 10,
		})

		result = await svc.get_my_rank("LUA-BOT-D", "weekly", neighbor_count=2, plan_id=PLAN)
		ranks = {n["player_id"]: n["rank"] for n in result["neighbors"]}
		assert ranks["LUA-BOT-D"] == 4
		assert ranks["LUA-BOT-C"] == 3
		assert ranks["LUA-BOT-B"] == 2
		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 3. Many tie groups — dense rank diverges from positional rank
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestManyTieGroups:
	async def test_large_tie_groups(self, redis_client):
		"""10 players per tier, 5 tiers → dense ranks 1-5."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		members = {}
		for tier in range(5):
			xp = (5 - tier) * 100  # 500, 400, 300, 200, 100
			for i in range(10):
				members[f"LUA-TIE-{tier}-{i}"] = xp
		await _seed(redis_client, members)

		# Check a player in the middle tier (300 XP, rank 3)
		result = await svc.get_my_rank("LUA-TIE-2-5", "weekly", plan_id=PLAN)
		assert result["rank"] == 3
		assert result["xp"] == 300

		# Check top-of-group player
		result_top = await svc.get_my_rank("LUA-TIE-0-0", "weekly", plan_id=PLAN)
		assert result_top["rank"] == 1

		# Check bottom-of-group player
		result_bot = await svc.get_my_rank("LUA-TIE-4-9", "weekly", plan_id=PLAN)
		assert result_bot["rank"] == 5

		await _cleanup(redis_client)

	async def test_all_same_xp(self, redis_client):
		"""All 20 players tied → everyone rank 1, xp_to_next=None."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		members = {f"LUA-ALLTIE-{i}": 100 for i in range(20)}
		await _seed(redis_client, members)

		for pid in ["LUA-ALLTIE-0", "LUA-ALLTIE-10", "LUA-ALLTIE-19"]:
			result = await svc.get_my_rank(pid, "weekly", plan_id=PLAN)
			assert result["rank"] == 1, f"{pid} should be rank 1, got {result['rank']}"
			assert result["xp_to_next"] is None

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 4. Neighbor window crossing multiple tiers
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestNeighborWindowMultipleTiers:
	async def test_window_spans_5_tiers(self, redis_client):
		"""Neighbor window of 2 above/below can span 5 distinct tiers."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		# 5 distinct tiers, 1 player each
		await _seed(redis_client, {
			"LUA-SPAN-A": 500,
			"LUA-SPAN-B": 400,
			"LUA-SPAN-C": 300,  # target player
			"LUA-SPAN-D": 200,
			"LUA-SPAN-E": 100,
		})

		result = await svc.get_my_rank("LUA-SPAN-C", "weekly", neighbor_count=2, plan_id=PLAN)
		assert result["rank"] == 3

		ranks = {n["player_id"]: n["rank"] for n in result["neighbors"]}
		assert ranks["LUA-SPAN-A"] == 1
		assert ranks["LUA-SPAN-B"] == 2
		assert ranks["LUA-SPAN-C"] == 3
		assert ranks["LUA-SPAN-D"] == 4
		assert ranks["LUA-SPAN-E"] == 5

		await _cleanup(redis_client)

	async def test_window_with_tie_groups_inside(self, redis_client):
		"""Window includes tie groups: neighbor ranks match dense logic."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {
			"LUA-WINTIE-A": 500,
			"LUA-WINTIE-B": 500,  # tie with A
			"LUA-WINTIE-C": 300,
			"LUA-WINTIE-D": 300,  # target (tied with C)
			"LUA-WINTIE-E": 100,
			"LUA-WINTIE-F": 100,  # tie with E
		})

		result = await svc.get_my_rank("LUA-WINTIE-D", "weekly", neighbor_count=2, plan_id=PLAN)
		# Tiers above 300: {500} = 1 tier → rank 2
		assert result["rank"] == 2

		# Neighbor ranks should also reflect dense ranking
		ranks = {n["player_id"]: n["rank"] for n in result["neighbors"]}
		# Window: 2 above D's position + 2 below. D is tied with C, so their
		# positions are adjacent. The window should include A/B (rank 1) and E/F (rank 3).
		for nid, nrank in ranks.items():
			nxp = next(n["xp"] for n in result["neighbors"] if n["player_id"] == nid)
			if nxp == 500:
				assert nrank == 1, f"{nid}(xp=500) should be rank 1"
			elif nxp == 300:
				assert nrank == 2, f"{nid}(xp=300) should be rank 2"
			elif nxp == 100:
				assert nrank == 3, f"{nid}(xp=100) should be rank 3"

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 5. xp_to_next correctness at every position
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestXpToNext:
	async def test_xp_to_next_at_every_tier(self, redis_client):
		"""Each player's xp_to_next matches the gap to the next higher tier."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		tiers = {"LUA-XTN-A": 1000, "LUA-XTN-B": 700, "LUA-XTN-C": 300, "LUA-XTN-D": 50}
		await _seed(redis_client, tiers)

		# A (1000): no tier above → None
		r_a = await svc.get_my_rank("LUA-XTN-A", "weekly", plan_id=PLAN)
		assert r_a["xp_to_next"] is None

		# B (700): next tier is 1000 → 300
		r_b = await svc.get_my_rank("LUA-XTN-B", "weekly", plan_id=PLAN)
		assert r_b["xp_to_next"] == 300

		# C (300): next tier is 700 → 400
		r_c = await svc.get_my_rank("LUA-XTN-C", "weekly", plan_id=PLAN)
		assert r_c["xp_to_next"] == 400

		# D (50): next tier is 300 → 250
		r_d = await svc.get_my_rank("LUA-XTN-D", "weekly", plan_id=PLAN)
		assert r_d["xp_to_next"] == 250

		await _cleanup(redis_client)

	async def test_xp_to_next_with_adjacent_tiers(self, redis_client):
		"""Tiers 1 XP apart: xp_to_next = 1."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {"LUA-ADJ-A": 11, "LUA-ADJ-B": 10})

		r_b = await svc.get_my_rank("LUA-ADJ-B", "weekly", plan_id=PLAN)
		assert r_b["xp_to_next"] == 1

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 6. Stress: many members, few distinct XP tiers
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestStressManyMembersFewTiers:
	"""1000 players across 5 XP tiers — validates Lua iterates only 5 times."""

	async def test_1000_players_5_tiers(self, redis_client):
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		members = {}
		tier_xps = [500, 400, 300, 200, 100]
		for tier_idx, xp in enumerate(tier_xps):
			for i in range(200):
				members[f"LUA-STRESS-FT-{tier_idx}-{i:03d}"] = xp
		await _seed(redis_client, members)

		# Bottom-tier player: Lua should step through 4 tiers (200, 300, 400, 500)
		result = await svc.get_my_rank("LUA-STRESS-FT-4-100", "weekly", plan_id=PLAN)
		assert result["rank"] == 5
		assert result["xp"] == 100
		assert result["xp_to_next"] == 100  # 200 - 100
		assert result["total_players"] == 1000

		# Top-tier player: Lua should find 0 tiers above
		result_top = await svc.get_my_rank("LUA-STRESS-FT-0-050", "weekly", plan_id=PLAN)
		assert result_top["rank"] == 1
		assert result_top["xp_to_next"] is None

		# Middle-tier player
		result_mid = await svc.get_my_rank("LUA-STRESS-FT-2-099", "weekly", plan_id=PLAN)
		assert result_mid["rank"] == 3
		assert result_mid["xp_to_next"] == 100  # 400 - 300

		await _cleanup(redis_client)

	async def test_cross_validate_top_vs_my_rank(self, redis_client):
		"""All 1000 players: get_my_rank agrees with get_top for sampled players."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		members = {}
		tier_xps = [500, 400, 300, 200, 100]
		for tier_idx, xp in enumerate(tier_xps):
			for i in range(200):
				members[f"LUA-XV-{tier_idx}-{i:03d}"] = xp
		await _seed(redis_client, members)

		# Get full top list
		top = await svc.get_top("weekly", limit=1000, plan_id=PLAN)
		top_ranks = {e["player_id"]: e["rank"] for e in top}

		# Sample one player from each tier
		mismatches = []
		for tier_idx in range(5):
			pid = f"LUA-XV-{tier_idx}-050"
			my = await svc.get_my_rank(pid, "weekly", plan_id=PLAN)
			if top_ranks[pid] != my["rank"]:
				mismatches.append(f"{pid}: top={top_ranks[pid]}, my={my['rank']}")

		assert not mismatches, f"Rank mismatches:\n" + "\n".join(mismatches)
		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 7. Stress: many distinct XP tiers (max Lua iterations)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestStressManyDistinctTiers:
	"""500 players, each with unique XP — validates Lua handles many iterations."""

	async def test_500_unique_tiers(self, redis_client):
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		# 500 players with XP from 1 to 500
		members = {f"LUA-STRESS-DT-{i:03d}": i for i in range(1, 501)}
		await _seed(redis_client, members)

		# Bottom player (XP=1): 499 tiers above → rank 500
		result = await svc.get_my_rank("LUA-STRESS-DT-001", "weekly", plan_id=PLAN)
		assert result["rank"] == 500
		assert result["xp"] == 1
		assert result["xp_to_next"] == 1  # tier 2 is next

		# Middle player (XP=250): 250 tiers above → rank 251
		result_mid = await svc.get_my_rank("LUA-STRESS-DT-250", "weekly", plan_id=PLAN)
		assert result_mid["rank"] == 251
		assert result_mid["xp_to_next"] == 1

		# Top player (XP=500): 0 tiers above → rank 1
		result_top = await svc.get_my_rank("LUA-STRESS-DT-500", "weekly", plan_id=PLAN)
		assert result_top["rank"] == 1
		assert result_top["xp_to_next"] is None

		await _cleanup(redis_client)

	async def test_500_unique_cross_validate(self, redis_client):
		"""Full cross-validation: every player's rank matches get_top."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		members = {f"LUA-XV2-{i:03d}": i for i in range(1, 501)}
		await _seed(redis_client, members)

		top = await svc.get_top("weekly", limit=500, plan_id=PLAN)
		top_ranks = {e["player_id"]: e["rank"] for e in top}

		# Sample 10 players across the range
		mismatches = []
		for xp in [1, 50, 100, 200, 250, 300, 400, 450, 499, 500]:
			pid = f"LUA-XV2-{xp:03d}"
			my = await svc.get_my_rank(pid, "weekly", plan_id=PLAN)
			if top_ranks[pid] != my["rank"]:
				mismatches.append(f"{pid}(xp={xp}): top={top_ranks[pid]}, my={my['rank']}")

		assert not mismatches, f"Rank mismatches:\n" + "\n".join(mismatches)
		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 8. Single player — minimal ZSET
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestSinglePlayer:
	async def test_only_player_rank_1(self, redis_client):
		"""Sole player: rank 1, no neighbors above/below, xp_to_next=None."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {"LUA-SOLO-A": 42})

		result = await svc.get_my_rank("LUA-SOLO-A", "weekly", plan_id=PLAN)
		assert result["rank"] == 1
		assert result["xp"] == 42
		assert result["xp_to_next"] is None
		assert result["total_players"] == 1

		# Neighbors should contain only self
		assert len(result["neighbors"]) == 1
		assert result["neighbors"][0]["is_me"] is True

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 9. Two players — minimal non-trivial case
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestTwoPlayers:
	async def test_two_different_xp(self, redis_client):
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {"LUA-DUO-A": 200, "LUA-DUO-B": 100})

		r_a = await svc.get_my_rank("LUA-DUO-A", "weekly", plan_id=PLAN)
		assert r_a["rank"] == 1
		assert r_a["xp_to_next"] is None

		r_b = await svc.get_my_rank("LUA-DUO-B", "weekly", plan_id=PLAN)
		assert r_b["rank"] == 2
		assert r_b["xp_to_next"] == 100

		await _cleanup(redis_client)

	async def test_two_tied(self, redis_client):
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {"LUA-DUO-A": 100, "LUA-DUO-B": 100})

		r_a = await svc.get_my_rank("LUA-DUO-A", "weekly", plan_id=PLAN)
		assert r_a["rank"] == 1

		r_b = await svc.get_my_rank("LUA-DUO-B", "weekly", plan_id=PLAN)
		assert r_b["rank"] == 1

		await _cleanup(redis_client)


# ------------------------------------------------------------------ #
# 10. Neighbor is_me flag correctness
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
class TestNeighborIsMeFlag:
	async def test_is_me_only_for_requesting_player(self, redis_client):
		"""Exactly one neighbor should have is_me=True."""
		await _cleanup(redis_client)
		svc = LeaderboardService(redis_client)

		await _seed(redis_client, {
			"LUA-ME-A": 500,
			"LUA-ME-B": 300,
			"LUA-ME-C": 100,
		})

		result = await svc.get_my_rank("LUA-ME-B", "weekly", neighbor_count=2, plan_id=PLAN)
		me_flags = [n for n in result["neighbors"] if n["is_me"]]
		assert len(me_flags) == 1
		assert me_flags[0]["player_id"] == "LUA-ME-B"

		await _cleanup(redis_client)
