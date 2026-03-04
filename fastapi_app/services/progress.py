"""Progress tracking service for Redis bitmap operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog
from cachetools import TTLCache

from fastapi_app.core.constants import DIRTY_PROGRESS_KEY
from fastapi_app.core.redis_keys import PROGRESS_KEY_TTL
from fastapi_app.core.redis_keys import progress_key as _progress_key_fn
from fastapi_app.services.hydration import guarded_hydrate

if TYPE_CHECKING:
	from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()

# Process-local cache tracking whether a progress bitmap EXISTS in Redis.
# Avoids the EXISTS RTT on every ensure_hydrated() call for recently verified keys.
# TTL=30s — after 30s we re-check Redis (safe because bitmaps have 48h TTL).
_progress_exists_cache: TTLCache[str, bool] = TTLCache(maxsize=50_000, ttl=30)


class ProgressService:
	"""Manages lesson completion via Redis bitmaps.

	Key pattern: memora:progress:{user_id}:{subject_id}:v{version}

	Per CONTEXT.md:
	- SETBIT return value detects replay (0 = first, 1 = replay)
	- BITCOUNT for total completed
	- Pipeline for batch GETBIT operations

	Operations:
	- complete_lesson: SETBIT O(1) - marks lesson complete
	- is_complete: GETBIT O(1) - checks single lesson status
	- get_completed_count: BITCOUNT O(N) on bitmap size
	- get_completed_bits: Single GET + byte-skipping decode for unlock calculation

	Hydration: After a Redis flush, progress bitmaps are lost. The ensure_hydrated()
	method restores them from MariaDB via the Frappe API, following the same
	pattern as AccessService.ensure_hydrated().
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient | None = None,
		raw_redis: redis.Redis | None = None,
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self._raw_redis = raw_redis

	def _progress_key(self, user_id: str, subject_id: str, version: int = 1) -> str:
		"""Generate Redis key for player's progress bitmap.

		Args:
		    user_id: Player's user ID
		    subject_id: Subject identifier (e.g., "MATH-G5")
		    version: Bitmap version for structural changes

		Returns:
		    Redis key string
		"""
		return _progress_key_fn(user_id, subject_id, version)

	async def ensure_hydrated(self, user_id: str, subject_id: str, version: int = 1) -> None:
		"""Ensure progress bitmap exists in Redis, hydrating from MariaDB if missing.

		Uses local exists cache (30s TTL) to skip Redis EXISTS RTT on repeat calls.
		"""
		key = self._progress_key(user_id, subject_id, version)

		# Fastest path: local cache says it exists (0 RTTs)
		if _progress_exists_cache.get(key):
			return

		# Fast path: bitmap already exists in Redis
		if await self.redis.exists(key):
			_progress_exists_cache[key] = True
			return

		# No Frappe client — can't hydrate
		if not self.frappe:
			logger.warning(
				"progress_hydration_skipped",
				user_id=user_id,
				subject_id=subject_id,
				reason="no_frappe_client",
			)
			return

		async def _do_hydrate() -> None:
			try:
				result = await self.frappe.call(
					"memora_admin.api.subscriptions.get_player_progress",
					{"player_id": user_id, "subject_id": subject_id},
				)

				if not result or not result.get("passed_lessons_bitset"):
					logger.debug(
						"progress_hydration_empty",
						user_id=user_id,
						subject_id=subject_id,
					)
					return

				hex_bitset = result["passed_lessons_bitset"]
				if hex_bitset:
					bitset_bytes = bytes.fromhex(hex_bitset)
					await self.redis.setrange(key, 0, bitset_bytes)
					await self.redis.expire(key, PROGRESS_KEY_TTL)
					logger.info(
						"progress_hydrated",
						user_id=user_id,
						subject_id=subject_id,
						completion_pct=result.get("completion_percentage", 0),
						bitset_length=len(hex_bitset),
					)

			except Exception as e:
				logger.error(
					"progress_hydration_failed",
					user_id=user_id,
					subject_id=subject_id,
					error=str(e),
				)

		await guarded_hydrate(self.redis, key, _do_hydrate)

	async def complete_lesson(
		self,
		user_id: str,
		subject_id: str,
		bit_index: int,
		version: int = 1,
	) -> bool:
		"""Mark lesson complete via SETBIT.

		O(1) operation. Idempotent - setting same bit twice is safe.

		Args:
		    user_id: Player's user ID
		    subject_id: Subject identifier
		    bit_index: Lesson's position in bitmap
		    version: Bitmap version

		Returns:
		    True if this was a replay (bit was already 1)
		    False if this is first completion (bit was 0)
		"""
		key = self._progress_key(user_id, subject_id, version)
		# SETBIT returns previous value: 0 if first time, 1 if replay
		previous = await self.redis.setbit(key, bit_index, 1)
		await self.redis.expire(key, PROGRESS_KEY_TTL)

		# Mark dirty for background sync to MariaDB
		dirty_member = f"{user_id}:{subject_id}:v{version}"
		await self.redis.sadd(DIRTY_PROGRESS_KEY, dirty_member)

		# We just wrote to the bitmap — it definitely exists
		_progress_exists_cache[key] = True

		return bool(previous)

	async def is_complete(
		self,
		user_id: str,
		subject_id: str,
		bit_index: int,
		version: int = 1,
	) -> bool:
		"""Check if lesson is complete via GETBIT.

		O(1) operation.

		Args:
		    user_id: Player's user ID
		    subject_id: Subject identifier
		    bit_index: Lesson's position in bitmap
		    version: Bitmap version

		Returns:
		    True if lesson is complete, False otherwise
		"""
		await self.ensure_hydrated(user_id, subject_id, version)
		key = self._progress_key(user_id, subject_id, version)
		return bool(await self.redis.getbit(key, bit_index))

	async def get_completed_count(
		self,
		user_id: str,
		subject_id: str,
		version: int = 1,
	) -> int:
		"""Count completed lessons via BITCOUNT.

		O(N) on bitmap size, where N is number of bytes.

		Args:
		    user_id: Player's user ID
		    subject_id: Subject identifier
		    version: Bitmap version

		Returns:
		    Number of completed lessons (set bits)
		"""
		await self.ensure_hydrated(user_id, subject_id, version)
		key = self._progress_key(user_id, subject_id, version)
		return await self.redis.bitcount(key)

	async def get_completed_bits(
		self,
		user_id: str,
		subject_id: str,
		bit_range: int,
		version: int = 1,
	) -> set[int]:
		"""Get set of completed bit indexes via single GET + byte-skipping decode.

		Uses a raw Redis client (decode_responses=False) for a single binary-safe
		GET, then iterates only non-zero bytes to extract set bits.  For a 50k-lesson
		subject at 20% completion, this skips ~80% of bytes vs the old O(bit_range) loop.

		Falls back to chunked BITFIELD if no raw client is available (e.g. in tests
		or PracticeService where raw_redis is None).

		Args:
		    user_id: Player's user ID
		    subject_id: Subject identifier
		    bit_range: Total bits to check (from SubjectHierarchy.bit_range)
		    version: Bitmap version

		Returns:
		    Set of bit indexes that are set (completed lessons)
		"""
		if bit_range <= 0:
			return set()

		await self.ensure_hydrated(user_id, subject_id, version)
		key = self._progress_key(user_id, subject_id, version)

		num_bytes = (bit_range + 7) // 8
		if num_bytes == 0:
			return set()

		if self._raw_redis is not None:
			try:
				return await self._get_completed_bits_raw(key, bit_range, num_bytes)
			except Exception as e:
				logger.warning(
					"progress_raw_read_failed_falling_back",
					key=key,
					bit_range=bit_range,
					error=str(e),
				)

		# Fallback: chunked BITFIELD (for contexts without raw client)
		return await self._get_completed_bits_bitfield(key, bit_range, num_bytes)

	async def _get_completed_bits_raw(self, key: str, bit_range: int, num_bytes: int) -> set[int]:
		"""Fast path: single GET + byte-skipping decode.

		One Redis command (GET) returns the full bitmap as raw bytes.
		Iterates only non-zero bytes, skipping empties entirely.
		For 50k lessons at 20% completion: ~1250 iterations vs 50k.
		"""
		raw: bytes | None = await self._raw_redis.get(key)
		if not raw:
			return set()

		completed = set()
		# Limit to what we care about (bitmap may be larger than bit_range)
		scan_bytes = min(len(raw), num_bytes)
		for byte_idx in range(scan_bytes):
			byte_val = raw[byte_idx]
			if byte_val == 0:
				continue  # Skip empty bytes — huge win at low completion %
			# Redis bitmaps use MSB-first bit ordering within each byte
			base = byte_idx * 8
			for bit_off in range(8):
				if byte_val & (0x80 >> bit_off):
					bit_idx = base + bit_off
					if bit_idx < bit_range:
						completed.add(bit_idx)
		return completed

	async def _get_completed_bits_bitfield(self, key: str, bit_range: int, num_bytes: int) -> set[int]:
		"""Fallback: chunked BITFIELD for contexts without raw Redis client."""
		CHUNK_SIZE = 512
		if num_bytes <= CHUNK_SIZE:
			args = []
			for byte_idx in range(num_bytes):
				args.extend(["GET", "u8", str(byte_idx * 8)])
			byte_values = await self.redis.execute_command("BITFIELD", key, *args)
			if not byte_values:
				return set()
		else:
			pipe = self.redis.pipeline(transaction=False)
			for offset in range(0, num_bytes, CHUNK_SIZE):
				count = min(CHUNK_SIZE, num_bytes - offset)
				args = []
				for i in range(count):
					args.extend(["GET", "u8", str((offset + i) * 8)])
				pipe.execute_command("BITFIELD", key, *args)
			results = await pipe.execute()
			byte_values = []
			for chunk in results:
				if chunk:
					byte_values.extend(chunk)
			if not byte_values:
				return set()

		completed = set()
		for byte_idx in range(len(byte_values)):
			byte_val = byte_values[byte_idx]
			if byte_val == 0:
				continue
			base = byte_idx * 8
			for bit_off in range(8):
				if byte_val & (0x80 >> bit_off):
					bit_idx = base + bit_off
					if bit_idx < bit_range:
						completed.add(bit_idx)
		return completed
