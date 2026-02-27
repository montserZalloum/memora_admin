"""Progress tracking service for Redis bitmap operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog

from fastapi_app.core.constants import DIRTY_PROGRESS_KEY
from fastapi_app.core.redis_keys import PROGRESS_KEY_TTL, progress_key as _progress_key_fn
from fastapi_app.services.hydration import guarded_hydrate

if TYPE_CHECKING:
	from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()


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
    - get_completed_bits: Single GET + client-side bitmap decode for unlock calculation

    Hydration: After a Redis flush, progress bitmaps are lost. The ensure_hydrated()
    method restores them from MariaDB via the Frappe API, following the same
    pattern as AccessService.ensure_hydrated().
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        frappe_client: FrappeClient | None = None,
    ):
        self.redis = redis_client
        self.frappe = frappe_client

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

        Uses distributed lock + semaphore to prevent thundering herd after Redis flush.
        Only one request per user+subject hydrates at a time; others wait for the result.

        Args:
            user_id: Player's user ID
            subject_id: Subject identifier
            version: Bitmap version
        """
        key = self._progress_key(user_id, subject_id, version)

        # Fast path: bitmap already exists in Redis
        if await self.redis.exists(key):
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
        # Format: user_id:subject_id:v{version}
        dirty_member = f"{user_id}:{subject_id}:v{version}"
        await self.redis.sadd(DIRTY_PROGRESS_KEY, dirty_member)

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
        """Get set of completed bit indexes via single-fetch bitmap decode.

        Uses a single Redis GET + client-side latin-1 decode instead of
        N GETBIT commands, reducing Redis command volume by ~99.8% for
        large subjects (e.g., 500 lessons → 1 command instead of 500).

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

        # Use BITFIELD GET u8 to read each byte as an integer.
        # Returns integers — immune to decode_responses=True UTF-8 decode
        # (plain GET fails on bitmap bytes > 0x7F like 0xFF).
        # Single command with multiple sub-operations = single Redis round-trip.
        args = []
        for byte_idx in range(num_bytes):
            args.extend(["GET", "u8", str(byte_idx * 8)])

        byte_values = await self.redis.execute_command("BITFIELD", key, *args)

        if not byte_values:
            return set()

        completed = set()
        for i in range(bit_range):
            byte_idx, bit_offset = divmod(i, 8)
            if byte_idx < len(byte_values):
                # Redis bitmaps use MSB-first bit ordering within each byte
                if byte_values[byte_idx] & (0x80 >> bit_offset):
                    completed.add(i)
        return completed
