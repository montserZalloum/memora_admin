"""Tests for ChallengeService bulk hydration — lock contention, sentinel behavior,
stale-write avoidance, and Frappe bulk API input validation.

Each test uses unique player/subject IDs to avoid key leakage between tests.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis

from fastapi_app.core.redis_keys import ch_progress_key, hydration_lock_key
from fastapi_app.services.challenge import ChallengeService


def _uid() -> str:
	"""Short unique suffix for test isolation."""
	return uuid.uuid4().hex[:8]


def _make_service(redis_client: redis.Redis, frappe_client=None, **kwargs) -> ChallengeService:
	return ChallengeService(redis_client=redis_client, frappe_client=frappe_client, **kwargs)


def _progress_records(subject_id: str) -> list[dict]:
	"""Fake Frappe records for a subject."""
	return [
		{
			"topic": f"T-{subject_id}-1",
			"stamped": 1,
			"best_correct": 8,
			"best_score_pct": 80.0,
			"best_passing_pct": 80.0,
			"total_xp_earned": 40,
			"attempt_count": 2,
		},
		{
			"topic": f"T-{subject_id}-2",
			"stamped": 0,
			"best_correct": 3,
			"best_score_pct": 30.0,
			"best_passing_pct": 0,
			"total_xp_earned": 15,
			"attempt_count": 1,
		},
	]


def _extract_requested_ids(mock_frappe: AsyncMock) -> list[str]:
	"""Extract subject_ids from the mock Frappe call args."""
	call_args = mock_frappe.call.call_args
	# .call(method_name, {params}) — positional args
	params = call_args[0][1]
	return json.loads(params["subject_ids"])


# =============================================================================
# Test: Bulk hydration writes all subjects in one pipeline
# =============================================================================


@pytest.mark.asyncio
class TestBulkHydrationBasic:
	async def test_hydrates_all_missing_subjects(self, redis_client: redis.Redis):
		"""All missing subjects are hydrated from one bulk Frappe call."""
		player = f"PLAYER-{_uid()}"
		subjects = [f"SUBJ-{_uid()}" for _ in range(3)]
		bulk_response = {sid: _progress_records(sid) for sid in subjects}

		mock_frappe = AsyncMock()
		mock_frappe.call.return_value = bulk_response

		svc = _make_service(redis_client, frappe_client=mock_frappe)
		await svc._ensure_hydrated_bulk(player, subjects)

		# Verify one Frappe call was made
		mock_frappe.call.assert_called_once()

		# Verify all keys populated
		for sid in subjects:
			key = ch_progress_key(player, sid)
			data = await redis_client.hgetall(key)
			assert len(data) == 2, f"Expected 2 topics for {sid}, got {len(data)}"
			parsed = json.loads(data[f"T-{sid}-1"])
			assert parsed["stamped"] == 1
			assert parsed["total_xp"] == 40

	async def test_skips_already_cached_subjects(self, redis_client: redis.Redis):
		"""Subjects already in Redis are not re-fetched."""
		player = f"PLAYER-{_uid()}"
		subjects = [f"SUBJ-{_uid()}" for _ in range(3)]

		# Pre-populate first subject
		cached_sid = subjects[0]
		key = ch_progress_key(player, cached_sid)
		await redis_client.hset(key, "T-EXISTING", json.dumps({"stamped": 1}))
		await redis_client.expire(key, 3600)

		mock_frappe = AsyncMock()
		mock_frappe.call.return_value = {
			sid: _progress_records(sid) for sid in subjects[1:]
		}

		svc = _make_service(redis_client, frappe_client=mock_frappe)
		await svc._ensure_hydrated_bulk(player, subjects)

		# Frappe should only have been asked for the 2 missing subjects
		requested_ids = _extract_requested_ids(mock_frappe)
		assert cached_sid not in requested_ids
		assert len(requested_ids) == 2

		# The pre-existing key should be untouched
		data = await redis_client.hgetall(key)
		assert "T-EXISTING" in data

	async def test_no_frappe_call_when_all_cached(self, redis_client: redis.Redis):
		"""No Frappe call when all subjects are already cached."""
		player = f"PLAYER-{_uid()}"
		subjects = [f"SUBJ-{_uid()}" for _ in range(3)]

		for sid in subjects:
			key = ch_progress_key(player, sid)
			await redis_client.hset(key, "T-1", json.dumps({"stamped": 0}))
			await redis_client.expire(key, 3600)

		mock_frappe = AsyncMock()
		svc = _make_service(redis_client, frappe_client=mock_frappe)
		await svc._ensure_hydrated_bulk(player, subjects)

		mock_frappe.call.assert_not_called()


# =============================================================================
# Test: Sentinel behavior — empty players don't re-trigger hydration
# =============================================================================


@pytest.mark.asyncio
class TestBulkSentinel:
	async def test_empty_result_sets_sentinel(self, redis_client: redis.Redis):
		"""When Frappe returns no records for a subject, sentinel is set."""
		player = f"PLAYER-{_uid()}"
		sid = f"SUBJ-EMPTY-{_uid()}"

		mock_frappe = AsyncMock()
		mock_frappe.call.return_value = {sid: []}

		svc = _make_service(redis_client, frappe_client=mock_frappe)
		await svc._ensure_hydrated_bulk(player, [sid])

		# Sentinel should exist
		sentinel = f"{ch_progress_key(player, sid)}:_hydrated"
		assert await redis_client.exists(sentinel)

	async def test_sentinel_prevents_re_hydration(self, redis_client: redis.Redis):
		"""Second call to _ensure_hydrated_bulk skips subjects with sentinel."""
		player = f"PLAYER-{_uid()}"
		sid = f"SUBJ-EMPTY-{_uid()}"

		mock_frappe = AsyncMock()
		mock_frappe.call.return_value = {sid: []}

		svc = _make_service(redis_client, frappe_client=mock_frappe)

		# First call — hydrates (empty) and sets sentinel
		await svc._ensure_hydrated_bulk(player, [sid])
		assert mock_frappe.call.call_count == 1

		# Second call — sentinel prevents re-hydration
		await svc._ensure_hydrated_bulk(player, [sid])
		assert mock_frappe.call.call_count == 1  # no second call

	async def test_frappe_failure_sets_sentinel(self, redis_client: redis.Redis):
		"""When Frappe call fails, sentinels are set to prevent retry storm."""
		player = f"PLAYER-{_uid()}"
		sid = f"SUBJ-FAIL-{_uid()}"

		mock_frappe = AsyncMock()
		mock_frappe.call.side_effect = Exception("Frappe down")

		svc = _make_service(redis_client, frappe_client=mock_frappe)
		await svc._ensure_hydrated_bulk(player, [sid])

		# Sentinel should exist (prevents retry storm)
		sentinel = f"{ch_progress_key(player, sid)}:_hydrated"
		assert await redis_client.exists(sentinel)

		# Lock should be released
		lock = hydration_lock_key(ch_progress_key(player, sid))
		assert not await redis_client.exists(lock)


# =============================================================================
# Test: Lock contention — waiter behavior
# =============================================================================


@pytest.mark.asyncio
class TestBulkLockContention:
	async def test_waits_for_locked_key(self, redis_client: redis.Redis):
		"""When another request holds the lock, bulk hydration waits for data."""
		player = f"PLAYER-{_uid()}"
		sid = f"SUBJ-LOCKED-{_uid()}"
		cache_key = ch_progress_key(player, sid)
		lock_key = hydration_lock_key(cache_key)

		# Simulate another request holding the lock
		await redis_client.set(lock_key, "1", ex=30)

		mock_frappe = AsyncMock()
		svc = _make_service(redis_client, frappe_client=mock_frappe)

		# Schedule the "other request" to finish after 0.3s
		async def _simulate_other_hydration():
			await asyncio.sleep(0.3)
			await redis_client.hset(cache_key, "T-1", json.dumps({"stamped": 1}))
			await redis_client.expire(cache_key, 3600)
			await redis_client.delete(lock_key)

		task = asyncio.create_task(_simulate_other_hydration())

		# This should wait (not return immediately) until the key appears
		await svc._ensure_hydrated_bulk(player, [sid])
		await task

		# No Frappe call — the waiter got data from the other request
		mock_frappe.call.assert_not_called()

		# Key should have data from the simulated hydration
		data = await redis_client.hgetall(cache_key)
		assert "T-1" in data

	async def test_mixed_locked_and_unlocked(self, redis_client: redis.Redis):
		"""Locks some keys, waits for others, hydrates what we can."""
		player = f"PLAYER-{_uid()}"
		free_sid = f"SUBJ-FREE-{_uid()}"
		held_sid = f"SUBJ-HELD-{_uid()}"
		held_key = ch_progress_key(player, held_sid)
		held_lock = hydration_lock_key(held_key)

		# held_sid already locked by another request
		await redis_client.set(held_lock, "1", ex=30)

		mock_frappe = AsyncMock()
		mock_frappe.call.return_value = {free_sid: _progress_records(free_sid)}

		svc = _make_service(redis_client, frappe_client=mock_frappe)

		# Simulate the other hydration finishing for held_sid
		async def _finish_held():
			await asyncio.sleep(0.2)
			await redis_client.hset(held_key, "T-1", json.dumps({"stamped": 0}))
			await redis_client.expire(held_key, 3600)
			await redis_client.delete(held_lock)

		task = asyncio.create_task(_finish_held())
		await svc._ensure_hydrated_bulk(player, [free_sid, held_sid])
		await task

		# Frappe was only called for the unlocked subject
		requested = _extract_requested_ids(mock_frappe)
		assert free_sid in requested
		assert held_sid not in requested

		# Both subjects should have data
		free_data = await redis_client.hgetall(ch_progress_key(player, free_sid))
		held_data = await redis_client.hgetall(held_key)
		assert len(free_data) > 0
		assert len(held_data) > 0

	async def test_waiter_timeout_does_not_crash(self, redis_client: redis.Redis):
		"""If waiter times out, the method completes without error."""
		player = f"PLAYER-{_uid()}"
		sid = f"SUBJ-TIMEOUT-{_uid()}"
		cache_key = ch_progress_key(player, sid)
		lock_key = hydration_lock_key(cache_key)

		# Lock held, but nobody will release it or write data
		await redis_client.set(lock_key, "1", ex=30)

		mock_frappe = AsyncMock()
		svc = _make_service(redis_client, frappe_client=mock_frappe)

		# Should complete (with warning log) after wait_timeout, not hang
		await svc._ensure_hydrated_bulk(player, [sid])

		# No crash, no Frappe call
		mock_frappe.call.assert_not_called()


# =============================================================================
# Test: Stale overwrite prevention
# =============================================================================


@pytest.mark.asyncio
class TestStaleOverwritePrevention:
	async def test_no_clobber_when_key_created_during_frappe_call(self, redis_client: redis.Redis):
		"""If submit_attempt creates a key during the Frappe call, don't overwrite it."""
		player = f"PLAYER-{_uid()}"
		sid = f"SUBJ-RACE-{_uid()}"
		cache_key = ch_progress_key(player, sid)

		# Frappe returns stale data (lower scores)
		stale_records = [{"topic": "T-1", "stamped": 0, "best_correct": 2, "best_score_pct": 20.0, "best_passing_pct": 0, "total_xp_earned": 10, "attempt_count": 1}]

		# Fresh data written by submit_attempt (higher scores)
		fresh_data = json.dumps({"stamped": 1, "best_correct": 9, "best_score_pct": 90.0, "best_passing_pct": 90.0, "total_xp": 45, "attempt_count": 3})

		async def slow_frappe_call(*args, **kwargs):
			# Simulate submit_attempt writing fresh data while Frappe is being called
			await redis_client.hset(cache_key, "T-1", fresh_data)
			await redis_client.expire(cache_key, 3600)
			return {sid: stale_records}

		mock_frappe = AsyncMock()
		mock_frappe.call.side_effect = slow_frappe_call

		svc = _make_service(redis_client, frappe_client=mock_frappe)
		await svc._ensure_hydrated_bulk(player, [sid])

		# The fresh data should NOT be overwritten by stale hydration
		data = await redis_client.hgetall(cache_key)
		parsed = json.loads(data["T-1"])
		assert parsed["stamped"] == 1, "Fresh data was clobbered by stale hydration"
		assert parsed["best_correct"] == 9
		assert parsed["total_xp"] == 45


# =============================================================================
# Test: Bulk progress maps pipeline
# =============================================================================


@pytest.mark.asyncio
class TestBulkProgressMaps:
	async def test_returns_all_subjects_in_one_pipeline(self, redis_client: redis.Redis):
		"""_get_progress_maps_bulk fetches all subjects in a single pipeline."""
		player = f"PLAYER-{_uid()}"
		subjects = [f"SUBJ-{_uid()}" for _ in range(3)]

		for sid in subjects:
			key = ch_progress_key(player, sid)
			await redis_client.hset(key, f"T-{sid}-1", json.dumps({"stamped": 1, "total_xp": 10}))
			await redis_client.expire(key, 3600)

		svc = _make_service(redis_client)
		result = await svc._get_progress_maps_bulk(player, subjects)

		assert len(result) == 3
		for sid in subjects:
			assert sid in result
			assert f"T-{sid}-1" in result[sid]
			assert result[sid][f"T-{sid}-1"]["stamped"] == 1

	async def test_empty_subjects_return_empty_dicts(self, redis_client: redis.Redis):
		"""Subjects with no data return empty dicts, not errors."""
		player = f"PLAYER-{_uid()}"
		svc = _make_service(redis_client)
		sids = [f"SUBJ-NONE-{_uid()}", f"SUBJ-NONE-{_uid()}"]
		result = await svc._get_progress_maps_bulk(player, sids)

		assert all(v == {} for v in result.values())
		assert len(result) == 2
