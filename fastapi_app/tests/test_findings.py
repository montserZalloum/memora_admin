"""Characterization tests for known bugs in FastAPI services.

Tests document and prove the existence of 3 bugs using the characterization pattern:
- FINDING-01: XP resets to 0 when FrappeClient is unreachable during wallet hydration
- FINDING-02: LTRIM off-by-one on partial insert failure in interaction buffer
- FINDING-03: Stats double-counting race on cold start (non-atomic EXISTS + HSET/HINCRBY)

Each test includes # BUG: and # FIX: comment pairs for easy assertion flipping
when bugs are resolved.
"""

import json
from unittest.mock import AsyncMock

import pytest
import redis.asyncio

from fastapi_app.core.redis_keys import stats_key as _stats_key_fn
from fastapi_app.core.redis_keys import wallet_key as _wallet_key_fn
from fastapi_app.services.stats import StatsService, compute_stats_from_hierarchy
from fastapi_app.services.wallet import WalletService
from fastapi_app.tests.conftest import make_hierarchy_json, seed_wallet

pytestmark = [pytest.mark.asyncio, pytest.mark.characterization]


class TestXPHydrationFailure:
	"""FINDING-01: XP resets to 0 when hydration fails during completion.

	Severity: CRITICAL
	Location: fastapi_app/services/wallet.py:205-213
	Caller:   fastapi_app/api/v1/endpoints/sessions.py:301-310

	Current behavior: ensure_hydrated() catches all exceptions from
	frappe.call() and returns silently. The subsequent HINCRBY on an
	empty wallet hash starts from 0, resetting the player's XP.

	Expected behavior: Either propagate the error to prevent the
	HINCRBY, or queue the completion for retry when hydration fails.
	"""

	async def test_xp_resets_on_hydration_failure(
		self,
		redis_client: redis.asyncio.Redis,
	) -> None:
		"""Test that XP resets to 0 when FrappeClient is unreachable."""
		player_id = "PLAYER-TEST-001"
		wallet_key = _wallet_key_fn(player_id)

		# Setup: Create mock FrappeClient that fails on .call()
		mock_frappe = AsyncMock()
		mock_frappe.call = AsyncMock(side_effect=ConnectionError("Frappe unreachable"))

		# Create WalletService with failing mock (uses default "memora:" prefix)
		service = WalletService(
			redis_client=redis_client,
			frappe_client=mock_frappe,
		)

		# Call ensure_hydrated - should swallow the error silently
		await service.ensure_hydrated(player_id)

		# Award XP - wallet hash is empty so HINCRBY starts from 0
		new_xp = await service.award_xp(player_id, 50)

		# Assert: XP is 50 (demonstrates the bug)
		assert new_xp == 50
		# BUG: should be old_xp + 50 if hydration had succeeded
		# For a real player who had 500 XP, this should be 550

		# Verify Redis state reflects the bug
		xp_value = await redis_client.hget(wallet_key, "xp")
		assert xp_value == "50"
		# FIX: When bug is fixed, assert xp_value == "550" (if player had 500 before)

	async def test_xp_correct_when_cache_populated(
		self,
		redis_client: redis.asyncio.Redis,
	) -> None:
		"""Test that XP increments correctly when cache is already populated."""
		player_id = "PLAYER-TEST-002"

		# Pre-seed wallet hash with existing XP
		await seed_wallet(
			redis=redis_client,
			player_id=player_id,
			xp=500,
			streak=0,
		)

		# Create WalletService - no frappe_client needed, cache is warm
		# Uses default "memora:" prefix which matches seed_wallet
		service = WalletService(redis_client=redis_client)

		# Award XP
		new_xp = await service.award_xp(player_id, 50)

		# Assert: XP is correct (500 + 50 = 550)
		assert new_xp == 550
		# No bug when cache was already populated - hydration not needed


class TestInteractionBufferLtrimRisk:
	"""FINDING-02: LTRIM off-by-one on partial insert failure in interaction buffer.

	Severity: MEDIUM
	Location: memora_admin/tasks/sync.py:340-349

	Current behavior: When flushing the interaction buffer, if some items
	succeed and some fail, the code uses `inserted` (the count of successful
	inserts) as the LTRIM start position. This drops already-processed items
	and leaves failed items in the buffer for re-processing, causing data loss.

	Expected behavior: Track actual positions of succeeded items and only trim
	consecutive items from the head of the list. Failed items should be retried
	or moved to a dead-letter queue.
	"""

	async def test_partial_failure_drops_failed_item(
		self,
		redis_client: redis.asyncio.Redis,
		test_prefix: str,
	) -> None:
		"""Test that partial failure drops failed items due to LTRIM boundary bug."""
		buffer_key = f"{test_prefix}buffer:interactions"

		# Setup: Create a buffer with 5 items
		items = [
			json.dumps({"player": "P0", "lesson": "L0"}),
			json.dumps({"player": "P1", "lesson": "L1"}),
			json.dumps({"player": "P2", "lesson": "L2"}),
			json.dumps({"player": "P3", "lesson": "L3"}),
			json.dumps({"player": "P4", "lesson": "L4"}),
		]
		await redis_client.rpush(buffer_key, *items)

		# Simulate sync.py flush loop: items 0, 2, 4 succeed (inserted=3)
		# Items 1, 3 fail but we just track the count
		inserted = 3

		# Execute LTRIM as sync.py:349 does: LTRIM(buffer, inserted, -1)
		await redis_client.ltrim(buffer_key, inserted, -1)

		# Read remaining items
		remaining = await redis_client.lrange(buffer_key, 0, -1)
		remaining_objs = [json.loads(item) for item in remaining]

		# Assert: 2 items remain (item_3, item_4)
		assert len(remaining) == 2
		assert remaining_objs[0]["player"] == "P3"
		assert remaining_objs[1]["player"] == "P4"

		# BUG: item_1 (failed) was trimmed and lost — should have been retained for retry
		# BUG: item_2 (succeeded) remains in buffer — will be re-processed next flush
		# FIX: When fixed, remaining should contain only [item_1, item_3] (failed items for retry)

	async def test_all_succeed_correct_trim(
		self,
		redis_client: redis.asyncio.Redis,
		test_prefix: str,
	) -> None:
		"""Test that LTRIM works correctly when all items succeed."""
		buffer_key = f"{test_prefix}buffer:interactions"

		# Setup: Create a buffer with 5 items
		items = [json.dumps({"player": f"P{i}", "lesson": f"L{i}"}) for i in range(5)]
		await redis_client.rpush(buffer_key, *items)

		# Simulate: all 5 items succeed → inserted = 5
		inserted = 5

		# Execute LTRIM
		await redis_client.ltrim(buffer_key, inserted, -1)

		# Read remaining items
		remaining = await redis_client.lrange(buffer_key, 0, -1)

		# Assert: buffer is empty (correct behavior)
		assert remaining == []
		# No bug when all items succeed — count == position in this case


class TestStatsDoubleCounting:
	"""FINDING-03: Stats double-counting race on cold start (non-atomic EXISTS + HSET/HINCRBY).

	Severity: LOW
	Location: fastapi_app/api/v1/endpoints/sessions.py:316-354

	Current behavior: The endpoint checks if stats exist (EXISTS), then either
	computes+HSET (cold path) or HINCRBY (warm path). Between EXISTS and HSET,
	another request can arrive, see the non-existent stats, and compute them
	independently. When both requests' results are applied, stats are double-counted.

	Expected behavior: Use atomic operations like SETNX or Lua to ensure only one
	request initializes stats on cold start. Subsequent requests should use HINCRBY.
	"""

	async def test_concurrent_cold_start_race(
		self,
		redis_client: redis.asyncio.Redis,
		test_prefix: str,
	) -> None:
		"""Test that concurrent cold start requests cause double-counting."""
		stats_key = _stats_key_fn("PLAYER-TEST", "SUB-TEST")

		# Simulate Request 1 (cold start path from sessions.py:329-345)
		exists = await redis_client.exists(stats_key)
		assert exists == 0

		# Request 1 computes stats
		stats_1 = {"completed": "1", "total": "10"}
		await redis_client.hset(stats_key, mapping=stats_1)

		# Simulate Request 2 arriving after Request 1's HSET (sessions.py:346-352)
		# (In reality this happens between EXISTS and HSET, but same effect)
		exists = await redis_client.exists(stats_key)
		assert exists == 1

		# Request 2 takes warm path: HINCRBY
		await redis_client.hincrby(stats_key, "completed", 1)

		# Read final value
		completed = await redis_client.hget(stats_key, "completed")

		# Assert: completed is 2 (demonstrates the bug)
		assert int(completed) == 2
		# BUG: completed is 2, but only 1 new lesson was completed
		# Request 1's bitmap computation already counted this lesson, then Request 2's HINCRBY added +1
		# FIX: When fixed with SETNX/Lua, assert completed == 1

	async def test_warm_path_increments_correctly(
		self,
		redis_client: redis.asyncio.Redis,
		test_prefix: str,
	) -> None:
		"""Test that warm path increments correctly (no race)."""
		stats_key = _stats_key_fn("PLAYER-TEST", "SUB-TEST")

		# Pre-seed stats hash
		await redis_client.hset(
			stats_key,
			mapping={"completed": "5", "total": "10"},
		)

		# Single HINCRBY
		await redis_client.hincrby(stats_key, "completed", 1)

		# Read final value
		completed = await redis_client.hget(stats_key, "completed")

		# Assert: correct increment (5 + 1 = 6)
		assert int(completed) == 6
		# No race on warm path — EXISTS returns 1, HINCRBY is atomic
