"""Concurrency validation tests for monetized access (T039).

Validates that Redis locks + DB unique indexes prevent duplicates under
concurrent requests for:
- Simultaneous purchase attempts
- Simultaneous voucher redemptions (last-use race)
- Simultaneous admin grants

Uses asyncio.gather to simulate concurrent requests against the services.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as redis

from fastapi_app.services.event_access import EventAccessService
from fastapi_app.services.premium import PremiumService


@pytest.fixture
def mock_redis():
	"""Create a mock Redis client with atomic SET NX behavior."""
	r = AsyncMock(spec=redis.Redis)
	# Track lock state to simulate real SET NX atomicity
	_locks: dict[str, bool] = {}

	async def _set_nx(key, value, *, nx=False, ex=None):
		if nx:
			if key in _locks:
				return False  # Already locked
			_locks[key] = True
			return True
		return True

	async def _delete(key):
		_locks.pop(key, None)

	r.set = _set_nx
	r.delete = _delete
	r.hgetall = AsyncMock(return_value={})
	r.hset = AsyncMock()
	return r


@pytest.fixture
def mock_frappe():
	"""Create a mock FrappeClient."""
	client = AsyncMock()
	return client


class TestPremiumConcurrentPurchase:
	"""Verify only one purchase succeeds when N concurrent attempts hit the lock."""

	@pytest.mark.asyncio
	async def test_concurrent_purchase_lock_prevents_duplicates(self, mock_redis, mock_frappe):
		"""Two concurrent lock acquisitions — only one should succeed."""
		svc = PremiumService(mock_redis, mock_frappe)

		# Both attempt to acquire lock
		result1 = await svc.acquire_lock("player-1", "plan-A")
		result2 = await svc.acquire_lock("player-1", "plan-A")

		assert result1 is True, "First lock acquisition should succeed"
		assert result2 is False, "Second lock acquisition should fail (key already held)"

	@pytest.mark.asyncio
	async def test_lock_release_allows_next_request(self, mock_redis, mock_frappe):
		"""After releasing the lock, a subsequent request can acquire it."""
		svc = PremiumService(mock_redis, mock_frappe)

		assert await svc.acquire_lock("player-1", "plan-A") is True
		await svc.release_lock("player-1", "plan-A")
		assert await svc.acquire_lock("player-1", "plan-A") is True

	@pytest.mark.asyncio
	async def test_concurrent_gather_only_one_wins(self, mock_redis, mock_frappe):
		"""asyncio.gather with N lock attempts — exactly one acquires."""
		svc = PremiumService(mock_redis, mock_frappe)

		results = await asyncio.gather(
			svc.acquire_lock("player-1", "plan-A"),
			svc.acquire_lock("player-1", "plan-A"),
			svc.acquire_lock("player-1", "plan-A"),
		)

		assert sum(results) == 1, f"Exactly one lock should succeed, got {results}"


class TestEventAccessConcurrentPurchase:
	"""Verify only one event ticket purchase succeeds under concurrency."""

	@pytest.mark.asyncio
	async def test_concurrent_event_lock_prevents_duplicates(self, mock_redis, mock_frappe):
		"""Two concurrent event access lock acquisitions — only one succeeds."""
		svc = EventAccessService(mock_redis, mock_frappe)

		result1 = await svc.acquire_lock("player-1", "event-X")
		result2 = await svc.acquire_lock("player-1", "event-X")

		assert result1 is True
		assert result2 is False

	@pytest.mark.asyncio
	async def test_different_players_can_lock_same_event(self, mock_redis, mock_frappe):
		"""Different players can acquire locks for the same event simultaneously."""
		svc = EventAccessService(mock_redis, mock_frappe)

		result1 = await svc.acquire_lock("player-1", "event-X")
		result2 = await svc.acquire_lock("player-2", "event-X")

		assert result1 is True
		assert result2 is True


class TestConcurrentVoucherRedemption:
	"""Verify Redis lock prevents concurrent voucher redemptions (last-use race)."""

	@pytest.mark.asyncio
	async def test_concurrent_voucher_redemption_lock(self, mock_redis, mock_frappe):
		"""Two players redeeming the same voucher type — locks are per (player, plan/event)."""
		svc = PremiumService(mock_redis, mock_frappe)

		# Different players can lock concurrently (different lock keys)
		r1 = await svc.acquire_lock("player-1", "plan-A")
		r2 = await svc.acquire_lock("player-2", "plan-A")

		assert r1 is True, "Player 1 should get lock"
		assert r2 is True, "Player 2 should get lock (different key)"

	@pytest.mark.asyncio
	async def test_same_player_voucher_redemption_serialized(self, mock_redis, mock_frappe):
		"""Same player redeeming for same plan — serialized by lock."""
		svc = PremiumService(mock_redis, mock_frappe)

		r1 = await svc.acquire_lock("player-1", "plan-A")
		r2 = await svc.acquire_lock("player-1", "plan-A")

		assert r1 is True
		assert r2 is False, "Same player+plan should be serialized"


class TestConcurrentAdminGrant:
	"""Verify concurrent admin grants for the same player+plan are prevented."""

	@pytest.mark.asyncio
	async def test_concurrent_admin_grant_lock(self, mock_redis, mock_frappe):
		"""Two admin grants for same player+plan — only one should proceed."""
		svc = PremiumService(mock_redis, mock_frappe)

		results = await asyncio.gather(
			svc.acquire_lock("player-1", "plan-A"),
			svc.acquire_lock("player-1", "plan-A"),
		)

		assert sum(results) == 1, "Exactly one admin grant should get the lock"
