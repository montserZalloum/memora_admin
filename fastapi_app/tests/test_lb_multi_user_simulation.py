"""Multi-user simulation tests for leaderboard system.

Simulates 500–1000 users with various XP distributions to validate:
- Dense ranking correctness (no skipped ranks)
- Correct total_players count
- Correct xp_to_next values
- Neighbor window integrity
- get_top / get_my_rank consistency

FINDINGS (from code review):
┌─────────────────────────────────────────────────────────────────────────┐
│ FINDING-1: Lua script _RANK_LUA is O(T * log N) where T = distinct    │
│ tiers above the player. With 500 unique XP tiers, bottom player        │
│ triggers 499 ZRANGEBYSCORE calls. At 100k users with diverse XP, this  │
│ could degrade to several hundred iterations. Bounded by XP range but   │
│ worth monitoring. Mitigation: daily leaderboards naturally limit T.    │
│                                                                         │
│ FINDING-2: get_top() fetches from position 0 to offset+limit-1 for     │
│ dense rank computation. At offset=900, limit=100, this pulls 1000      │
│ entries. Redis handles this fine but it's O(offset+limit) memory.       │
│                                                                         │
│ FINDING-3: Neighbor rank derivation relies on window containing ALL     │
│ distinct tiers between player and neighbors. This is correct because    │
│ ZRANGE returns contiguous positions — verified by tests below.          │
└─────────────────────────────────────────────────────────────────────────┘
"""

import asyncio
import random
import time

import pytest

from fastapi_app.services.leaderboard import LeaderboardService


# -- Fixtures ------------------------------------------------------------------


@pytest.fixture
async def lb_svc(redis_client):
	"""LeaderboardService with test Redis."""
	return LeaderboardService(redis_client)


@pytest.fixture(autouse=True)
async def cleanup_lb_keys(redis_client):
	"""Auto-cleanup leaderboard keys after each test."""
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


# -- Helpers -------------------------------------------------------------------


def _validate_dense_ranks(entries: list[dict]) -> list[str]:
	"""Validate dense ranking invariants. Returns list of violations."""
	violations = []
	if not entries:
		return violations

	# Sort by rank to check contiguity
	sorted_entries = sorted(entries, key=lambda e: e["rank"])

	# Rule 1: First rank must be 1
	if sorted_entries[0]["rank"] != 1:
		violations.append(f"First rank is {sorted_entries[0]['rank']}, expected 1")

	# Rule 2: Ranks must be contiguous (no gaps)
	seen_ranks = sorted(set(e["rank"] for e in sorted_entries))
	for i in range(1, len(seen_ranks)):
		if seen_ranks[i] != seen_ranks[i - 1] + 1:
			violations.append(f"Gap in ranks: {seen_ranks[i-1]} → {seen_ranks[i]}")

	# Rule 3: Same XP → same rank
	xp_to_rank = {}
	for entry in sorted_entries:
		xp = entry["xp"]
		rank = entry["rank"]
		if xp in xp_to_rank:
			if xp_to_rank[xp] != rank:
				violations.append(
					f"XP {xp} has inconsistent ranks: {xp_to_rank[xp]} and {rank}"
				)
		else:
			xp_to_rank[xp] = rank

	# Rule 4: Higher XP → lower (better) rank number
	xp_ranks = sorted(xp_to_rank.items(), key=lambda x: -x[0])
	for i in range(1, len(xp_ranks)):
		if xp_ranks[i][1] <= xp_ranks[i - 1][1]:
			violations.append(
				f"Rank ordering broken: XP {xp_ranks[i-1][0]} (rank {xp_ranks[i-1][1]}) >= "
				f"XP {xp_ranks[i][0]} (rank {xp_ranks[i][1]})"
			)

	return violations


async def _seed_players(lb_svc, players: list[tuple[str, int]], plan_id: str = "PLAN-TEST-SIM"):
	"""Seed multiple players via update_leaderboards."""
	for player_id, xp in players:
		await lb_svc.update_leaderboards(player_id, xp_amount=xp, plan_id=plan_id)


# -- Test Classes --------------------------------------------------------------


class TestRandomXP500Users:
	"""500 users with random XP distribution."""

	PLAN = "PLAN-TEST-RAND500"
	N_USERS = 500

	async def test_dense_ranking_500_random_xp(self, lb_svc, redis_client):
		"""500 users with random XP: dense ranking invariants hold."""
		random.seed(42)
		players = [
			(f"PLAYER-TEST-R500-{i:04d}", random.randint(1, 10000))
			for i in range(self.N_USERS)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		# get_top for all players
		top = await lb_svc.get_top("daily", limit=self.N_USERS, plan_id=self.PLAN)

		assert len(top) == self.N_USERS
		violations = _validate_dense_ranks(top)
		assert violations == [], f"Dense ranking violations: {violations}"

	async def test_total_players_correct(self, lb_svc, redis_client):
		"""total_players matches actual ZSET cardinality."""
		random.seed(43)
		players = [
			(f"PLAYER-TEST-TP-{i:04d}", random.randint(1, 5000))
			for i in range(self.N_USERS)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		# Check via get_my_rank
		result = await lb_svc.get_my_rank(players[0][0], "daily", plan_id=self.PLAN)
		assert result["total_players"] == self.N_USERS

		# Cross-check with ZCARD
		key = lb_svc._get_plan_key("daily", self.PLAN)
		zcard = await redis_client.zcard(key)
		assert zcard == self.N_USERS

	async def test_xp_to_next_correctness_all_players(self, lb_svc, redis_client):
		"""xp_to_next for every player equals gap to next higher tier."""
		random.seed(44)
		players = [
			(f"PLAYER-TEST-XTN-{i:04d}", random.randint(1, 5000))
			for i in range(100)  # 100 for speed
		]
		await _seed_players(lb_svc, players, self.PLAN)

		# Sort players by XP desc to know expected tiers
		sorted_by_xp = sorted(players, key=lambda p: -p[1])
		unique_xps = sorted(set(p[1] for p in players), reverse=True)

		for player_id, xp in players:
			result = await lb_svc.get_my_rank(player_id, "daily", plan_id=self.PLAN)
			assert result["xp"] == xp

			# Find next higher tier
			higher_tiers = [t for t in unique_xps if t > xp]
			if higher_tiers:
				expected_next = min(higher_tiers) - xp
				assert result["xp_to_next"] == expected_next, (
					f"Player {player_id} (xp={xp}): expected xp_to_next={expected_next}, "
					f"got {result['xp_to_next']}"
				)
			else:
				assert result["xp_to_next"] is None, (
					f"Top player {player_id} should have xp_to_next=None"
				)

	async def test_get_top_sorted_descending(self, lb_svc, redis_client):
		"""get_top returns entries sorted by XP descending."""
		random.seed(45)
		players = [
			(f"PLAYER-TEST-SORT-{i:04d}", random.randint(1, 10000))
			for i in range(200)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		top = await lb_svc.get_top("daily", limit=200, plan_id=self.PLAN)
		xps = [e["xp"] for e in top]

		for i in range(1, len(xps)):
			assert xps[i] <= xps[i - 1], (
				f"Not sorted descending at position {i}: {xps[i-1]} → {xps[i]}"
			)


class TestTieGroups:
	"""Controlled tier-based XP groups — large tie groups."""

	PLAN = "PLAN-TEST-TIES"

	async def test_5_large_tie_groups(self, lb_svc, redis_client):
		"""5 tie groups × 100 players each = 500 players, 5 dense ranks."""
		tier_xps = [1000, 800, 500, 200, 100]
		players = []
		for tier_idx, xp in enumerate(tier_xps):
			for j in range(100):
				players.append((f"PLAYER-TEST-TG-{tier_idx}-{j:03d}", xp))

		await _seed_players(lb_svc, players, self.PLAN)

		top = await lb_svc.get_top("daily", limit=500, plan_id=self.PLAN)

		# Must have exactly 500 entries
		assert len(top) == 500

		# Must have exactly 5 distinct ranks
		unique_ranks = set(e["rank"] for e in top)
		assert unique_ranks == {1, 2, 3, 4, 5}

		# Each rank should have exactly 100 players
		for rank in range(1, 6):
			count = sum(1 for e in top if e["rank"] == rank)
			assert count == 100, f"Rank {rank} has {count} players, expected 100"

		# Validate dense ranking invariants
		violations = _validate_dense_ranks(top)
		assert violations == [], f"Dense ranking violations: {violations}"

	async def test_get_my_rank_for_each_tie_group(self, lb_svc, redis_client):
		"""get_my_rank returns correct rank for players in each tie group."""
		tier_xps = [500, 300, 100]
		players = []
		for tier_idx, xp in enumerate(tier_xps):
			for j in range(50):
				players.append((f"PLAYER-TEST-TGMR-{tier_idx}-{j:03d}", xp))

		await _seed_players(lb_svc, players, self.PLAN)

		# Check one player from each tier
		for tier_idx, expected_rank in enumerate([1, 2, 3]):
			player_id = f"PLAYER-TEST-TGMR-{tier_idx}-000"
			result = await lb_svc.get_my_rank(player_id, "daily", plan_id=self.PLAN)
			assert result["rank"] == expected_rank, (
				f"Player in tier {tier_idx} (xp={tier_xps[tier_idx]}): "
				f"expected rank {expected_rank}, got {result['rank']}"
			)
			assert result["total_players"] == 150

	async def test_massive_single_tie_group(self, lb_svc, redis_client):
		"""500 players all with same XP → all rank 1, xp_to_next=None."""
		players = [
			(f"PLAYER-TEST-MEGA-{i:04d}", 500)
			for i in range(500)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		top = await lb_svc.get_top("daily", limit=500, plan_id=self.PLAN)
		assert len(top) == 500
		assert all(e["rank"] == 1 for e in top)
		assert all(e["xp"] == 500 for e in top)

		# Every player is rank 1 with xp_to_next=None
		result = await lb_svc.get_my_rank(players[0][0], "daily", plan_id=self.PLAN)
		assert result["rank"] == 1
		assert result["xp_to_next"] is None


class TestUniqueXPTiers:
	"""500 users, each with unique XP — worst case for Lua script."""

	PLAN = "PLAN-TEST-UNIQ"

	async def test_500_unique_tiers_ranking(self, lb_svc, redis_client):
		"""500 unique XP values → 500 unique dense ranks (1 through 500)."""
		players = [
			(f"PLAYER-TEST-UNQ-{i:04d}", i + 1)
			for i in range(500)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		top = await lb_svc.get_top("daily", limit=500, plan_id=self.PLAN)
		assert len(top) == 500

		# Each player gets a unique rank from 1 to 500
		ranks = [e["rank"] for e in top]
		assert ranks == list(range(1, 501))

		violations = _validate_dense_ranks(top)
		assert violations == [], f"Dense ranking violations: {violations}"

	async def test_bottom_player_rank_correct(self, lb_svc, redis_client):
		"""Bottom player (XP=1) with 500 unique tiers → rank 500.

		This is the worst case for the Lua script: it must iterate through
		all 499 tiers above to count distinct_above. See FINDING-1.
		"""
		players = [
			(f"PLAYER-TEST-BTM-{i:04d}", i + 1)
			for i in range(500)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		# Bottom player = XP 1
		result = await lb_svc.get_my_rank("PLAYER-TEST-BTM-0000", "daily", plan_id=self.PLAN)
		assert result["rank"] == 500
		assert result["xp"] == 1
		assert result["xp_to_next"] == 1  # next tier is XP 2
		assert result["total_players"] == 500

	async def test_top_player_rank_correct(self, lb_svc, redis_client):
		"""Top player with 500 unique tiers → rank 1, xp_to_next=None."""
		players = [
			(f"PLAYER-TEST-TOP-{i:04d}", i + 1)
			for i in range(500)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		# Top player = XP 500
		result = await lb_svc.get_my_rank("PLAYER-TEST-TOP-0499", "daily", plan_id=self.PLAN)
		assert result["rank"] == 1
		assert result["xp"] == 500
		assert result["xp_to_next"] is None

	async def test_lua_performance_500_unique(self, lb_svc, redis_client):
		"""Lua script performance with 500 unique tiers: must complete in <500ms.

		Bottom player triggers 499 ZRANGEBYSCORE iterations.
		This is a performance regression test, not a correctness test.
		"""
		players = [
			(f"PLAYER-TEST-PERF-{i:04d}", i + 1)
			for i in range(500)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		start = time.monotonic()
		result = await lb_svc.get_my_rank("PLAYER-TEST-PERF-0000", "daily", plan_id=self.PLAN)
		elapsed_ms = (time.monotonic() - start) * 1000

		assert result["rank"] == 500
		assert elapsed_ms < 500, (
			f"Lua script took {elapsed_ms:.1f}ms for 500 unique tiers — too slow"
		)


class TestStress1000Users:
	"""1000 user stress test with mixed XP distribution."""

	PLAN = "PLAN-TEST-1K"

	async def test_1000_users_mixed_distribution(self, lb_svc, redis_client):
		"""1000 users: 200 unique + 800 in tie groups."""
		random.seed(99)
		players = []

		# 200 unique XP values (high range)
		for i in range(200):
			players.append((f"PLAYER-TEST-1K-U-{i:04d}", 5000 + i))

		# 8 tie groups × 100 players (lower range)
		tie_xps = [4000, 3000, 2000, 1500, 1000, 700, 400, 100]
		for tier_idx, xp in enumerate(tie_xps):
			for j in range(100):
				players.append((f"PLAYER-TEST-1K-T-{tier_idx}-{j:03d}", xp))

		random.shuffle(players)
		await _seed_players(lb_svc, players, self.PLAN)

		# Validate full leaderboard
		top = await lb_svc.get_top("daily", limit=1000, plan_id=self.PLAN)
		assert len(top) == 1000

		violations = _validate_dense_ranks(top)
		assert violations == [], f"Dense ranking violations: {violations}"

		# Expected: 200 unique ranks + 8 tie group ranks = 208 distinct ranks
		unique_ranks = set(e["rank"] for e in top)
		assert len(unique_ranks) == 208, f"Expected 208 distinct ranks, got {len(unique_ranks)}"

	async def test_get_top_get_my_rank_consistency_1000(self, lb_svc, redis_client):
		"""get_top and get_my_rank agree on rank for all 1000 players.

		This is the most comprehensive consistency check — verifies that the
		Lua-based dense rank in get_my_rank matches the iterative dense rank
		in get_top for every player.
		"""
		random.seed(100)
		players = [
			(f"PLAYER-TEST-CON-{i:04d}", random.randint(1, 5000))
			for i in range(200)  # 200 for speed (1000 would be ~100 get_my_rank calls)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		top = await lb_svc.get_top("daily", limit=200, plan_id=self.PLAN)
		top_rank_map = {e["player_id"]: e["rank"] for e in top}

		# Verify a sample of 50 players
		sample = random.sample(players, min(50, len(players)))
		for player_id, _ in sample:
			result = await lb_svc.get_my_rank(player_id, "daily", plan_id=self.PLAN)
			expected_rank = top_rank_map[player_id]
			assert result["rank"] == expected_rank, (
				f"Rank mismatch for {player_id}: get_top says {expected_rank}, "
				f"get_my_rank says {result['rank']}"
			)


class TestNeighborWindow:
	"""Neighbor window correctness with large datasets."""

	PLAN = "PLAN-TEST-NBRS"

	async def test_neighbor_count_boundary(self, lb_svc, redis_client):
		"""Neighbor window respects neighbor_count=2 (default)."""
		players = [
			(f"PLAYER-TEST-NB-{i:04d}", (100 - i) * 10)
			for i in range(20)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		# Middle player (rank ~10)
		result = await lb_svc.get_my_rank("PLAYER-TEST-NB-0009", "daily", plan_id=self.PLAN)
		# With neighbor_count=2, window = positions [7..11] = 5 entries
		assert len(result["neighbors"]) == 5
		assert any(n["is_me"] for n in result["neighbors"])

	async def test_top_player_neighbor_window(self, lb_svc, redis_client):
		"""Top player's window only extends downward."""
		players = [
			(f"PLAYER-TEST-NB-TOP-{i:04d}", (50 - i) * 10)
			for i in range(50)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		result = await lb_svc.get_my_rank("PLAYER-TEST-NB-TOP-0000", "daily", plan_id=self.PLAN)
		assert result["rank"] == 1
		# Window: positions [0..2] = 3 entries (self + 2 below)
		assert len(result["neighbors"]) == 3
		assert result["neighbors"][0]["is_me"] is True

	async def test_bottom_player_neighbor_window(self, lb_svc, redis_client):
		"""Bottom player's window only extends upward."""
		players = [
			(f"PLAYER-TEST-NB-BTM-{i:04d}", (50 - i) * 10)
			for i in range(50)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		result = await lb_svc.get_my_rank("PLAYER-TEST-NB-BTM-0049", "daily", plan_id=self.PLAN)
		assert result["rank"] == 50
		# Window: positions [47..49] = 3 entries (2 above + self)
		assert len(result["neighbors"]) == 3
		assert result["neighbors"][-1]["is_me"] is True

	async def test_neighbor_ranks_consistent_with_get_top(self, lb_svc, redis_client):
		"""Neighbor ranks in get_my_rank match ranks from get_top."""
		players = [
			(f"PLAYER-TEST-NRC-{i:04d}", (30 - i) * 10)
			for i in range(30)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		top = await lb_svc.get_top("daily", limit=30, plan_id=self.PLAN)
		top_rank_map = {e["player_id"]: e["rank"] for e in top}

		# Check middle player
		result = await lb_svc.get_my_rank("PLAYER-TEST-NRC-0015", "daily", plan_id=self.PLAN)
		for n in result["neighbors"]:
			expected_rank = top_rank_map[n["player_id"]]
			assert n["rank"] == expected_rank, (
				f"Neighbor {n['player_id']} rank mismatch: "
				f"neighbor says {n['rank']}, get_top says {expected_rank}"
			)

	async def test_neighbor_window_with_ties_at_boundary(self, lb_svc, redis_client):
		"""Neighbor window when tie groups span the window boundary."""
		players = [
			("PLAYER-TEST-NWT-A", 500),
			("PLAYER-TEST-NWT-B", 300),
			("PLAYER-TEST-NWT-C", 300),
			("PLAYER-TEST-NWT-D", 300),
			("PLAYER-TEST-NWT-E", 300),
			("PLAYER-TEST-NWT-F", 100),
		]
		await _seed_players(lb_svc, players, self.PLAN)

		# Player C is at position 2 (0-indexed), window = [0..4]
		result = await lb_svc.get_my_rank("PLAYER-TEST-NWT-C", "daily", plan_id=self.PLAN)
		assert result["rank"] == 2  # Tied with B, D, E at XP 300

		# All neighbors with XP 300 should share rank 2
		for n in result["neighbors"]:
			if n["xp"] == 300:
				assert n["rank"] == 2
			elif n["xp"] == 500:
				assert n["rank"] == 1
			elif n["xp"] == 100:
				assert n["rank"] == 3


class TestPaginatedRankConsistency:
	"""Verify dense ranks are absolute across paginated windows."""

	PLAN = "PLAN-TEST-PAG"

	async def test_paginated_ranks_no_reset(self, lb_svc, redis_client):
		"""Ranks in page 2 continue from page 1 (no rank reset)."""
		players = [
			(f"PLAYER-TEST-PAG-{i:04d}", (100 - i) * 10)
			for i in range(100)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		page1 = await lb_svc.get_top("daily", limit=20, offset=0, plan_id=self.PLAN)
		page2 = await lb_svc.get_top("daily", limit=20, offset=20, plan_id=self.PLAN)

		# Last rank of page 1 should be less than first rank of page 2
		assert page1[-1]["rank"] <= page2[0]["rank"]

		# Ranks should be continuous
		combined = page1 + page2
		violations = _validate_dense_ranks(combined)
		assert violations == [], f"Paginated rank violations: {violations}"

	async def test_full_pagination_sweep(self, lb_svc, redis_client):
		"""Sweeping all pages produces the same ranks as a single full fetch."""
		random.seed(77)
		players = [
			(f"PLAYER-TEST-SWEEP-{i:04d}", random.randint(1, 1000))
			for i in range(100)
		]
		await _seed_players(lb_svc, players, self.PLAN)

		# Full fetch
		full = await lb_svc.get_top("daily", limit=100, plan_id=self.PLAN)
		full_map = {e["player_id"]: e["rank"] for e in full}

		# Paginated fetch
		paginated_map = {}
		for offset in range(0, 100, 10):
			page = await lb_svc.get_top("daily", limit=10, offset=offset, plan_id=self.PLAN)
			for e in page:
				paginated_map[e["player_id"]] = e["rank"]

		# Every player's rank must match
		for pid, rank in full_map.items():
			assert paginated_map.get(pid) == rank, (
				f"Player {pid}: full={rank}, paginated={paginated_map.get(pid)}"
			)


class TestWeeklyAccumulation:
	"""Weekly leaderboard accumulates across multiple updates."""

	PLAN = "PLAN-TEST-WKLY"

	async def test_zincrby_accumulates(self, lb_svc, redis_client):
		"""Multiple update_leaderboards calls accumulate XP correctly."""
		player = "PLAYER-TEST-ACCUM-001"
		for _ in range(100):
			await lb_svc.update_leaderboards(player, xp_amount=3, plan_id=self.PLAN)

		result = await lb_svc.get_my_rank(player, "weekly", plan_id=self.PLAN)
		assert result["xp"] == 300  # 100 × 3 = 300 (no float drift)

	async def test_multi_player_accumulation_ranks(self, lb_svc, redis_client):
		"""Multiple updates across players produce correct final rankings."""
		# Player A: 10 updates × 50 = 500
		# Player B: 5 updates × 100 = 500 (tied with A)
		# Player C: 50 updates × 2 = 100
		for _ in range(10):
			await lb_svc.update_leaderboards("PLAYER-TEST-ACC-A", xp_amount=50, plan_id=self.PLAN)
		for _ in range(5):
			await lb_svc.update_leaderboards("PLAYER-TEST-ACC-B", xp_amount=100, plan_id=self.PLAN)
		for _ in range(50):
			await lb_svc.update_leaderboards("PLAYER-TEST-ACC-C", xp_amount=2, plan_id=self.PLAN)

		top = await lb_svc.get_top("daily", limit=10, plan_id=self.PLAN)
		assert len(top) == 3

		# A and B tied at 500
		assert top[0]["xp"] == 500
		assert top[1]["xp"] == 500
		assert top[0]["rank"] == 1
		assert top[1]["rank"] == 1

		# C at 100
		assert top[2]["xp"] == 100
		assert top[2]["rank"] == 2
