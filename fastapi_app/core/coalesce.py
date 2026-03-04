"""Reusable per-key lock pool for cache-fill coalescing."""

from __future__ import annotations

import asyncio


class CoalescingLockPool:
	"""Process-local pool of per-key asyncio locks with soft pruning."""

	def __init__(self, max_size: int = 10_000):
		self._locks: dict[str, asyncio.Lock] = {}
		self._max_size = max_size

	def get(self, key: str) -> asyncio.Lock:
		"""Get or create a lock for key and prune unlocked overflow entries."""
		lock = self._locks.get(key)
		if lock is not None:
			return lock

		lock = self._locks.setdefault(key, asyncio.Lock())
		if len(self._locks) > self._max_size:
			to_remove = [k for k, v in self._locks.items() if k != key and not v.locked()]
			for stale_key in to_remove:
				self._locks.pop(stale_key, None)
		return lock

	def clear(self) -> None:
		"""Clear all tracked locks (used by tests and cold-start resets)."""
		self._locks.clear()
