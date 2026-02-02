"""Progress tracking service for Redis bitmap operations."""

import redis.asyncio as redis


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
    - get_completed_bits: Pipeline GETBIT for unlock calculation
    """

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _progress_key(self, user_id: str, subject_id: str, version: int = 1) -> str:
        """Generate Redis key for player's progress bitmap.

        Args:
            user_id: Player's user ID
            subject_id: Subject identifier (e.g., "MATH-G5")
            version: Bitmap version for structural changes

        Returns:
            Redis key string
        """
        return f"{self.prefix}progress:{user_id}:{subject_id}:v{version}"

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
        key = self._progress_key(user_id, subject_id, version)
        return await self.redis.bitcount(key)

    async def get_completed_bits(
        self,
        user_id: str,
        subject_id: str,
        bit_range: int,
        version: int = 1,
    ) -> set[int]:
        """Get set of completed bit indexes using pipeline.

        Used for unlock state calculation. Batches GETBIT calls
        for efficiency.

        Args:
            user_id: Player's user ID
            subject_id: Subject identifier
            bit_range: Total bits to check (from SubjectHierarchy.bit_range)
            version: Bitmap version

        Returns:
            Set of bit indexes that are set (completed lessons)
        """
        key = self._progress_key(user_id, subject_id, version)
        completed = set()

        # Use pipeline for batch GETBIT operations
        pipe = self.redis.pipeline()
        for i in range(bit_range):
            pipe.getbit(key, i)
        results = await pipe.execute()

        for i, is_set in enumerate(results):
            if is_set:
                completed.add(i)

        return completed
