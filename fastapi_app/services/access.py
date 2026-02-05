"""Player access set management via Redis for O(1) access checks."""

import redis.asyncio as redis


class AccessService:
    """Manages player access grants via Redis sets.

    Key pattern: memora:access:{player_id} -> set of content keys
    Content keys follow pattern: SUB-{subject} or TRK-{track}

    Per CONTEXT.md:
    - Grants are additive (direct OR plan membership)
    - Grants are permanent until explicitly revoked
    - Grant granularity: Subject-level or Track-level
    """

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _access_key(self, player_id: str) -> str:
        """Generate Redis key for player's access set."""
        return f"{self.prefix}access:{player_id}"

    async def check_access(self, player_id: str, content_key: str) -> bool:
        """
        Check if player has access to content.
        O(1) complexity via SISMEMBER.

        Args:
            player_id: Player's user ID
            content_key: Access key (e.g., "SUB-MATH", "TRK-MATH-01")

        Returns:
            True if player has grant for this content
        """
        key = self._access_key(player_id)
        result = await self.redis.sismember(key, content_key)
        # Handle both int (0/1) and bool responses
        return bool(result)

    async def grant_access(self, player_id: str, content_keys: list[str]) -> int:
        """
        Grant access to content.
        Idempotent - re-granting same key is safe (SADD ignores duplicates).

        Returns:
            Number of NEW grants added (0 if all existed)
        """
        if not content_keys:
            return 0
        key = self._access_key(player_id)
        return await self.redis.sadd(key, *content_keys)

    async def revoke_access(self, player_id: str, content_keys: list[str]) -> int:
        """
        Revoke access to content.

        Returns:
            Number of grants removed
        """
        if not content_keys:
            return 0
        key = self._access_key(player_id)
        return await self.redis.srem(key, *content_keys)

    async def get_player_grants(self, player_id: str) -> set[str]:
        """
        Get all content keys player has access to.
        O(N) - use sparingly, prefer check_access for single checks.
        """
        key = self._access_key(player_id)
        members = await self.redis.smembers(key)
        # Handle bytes or str responses
        return {m.decode() if isinstance(m, bytes) else m for m in members}

    # =========================================================================
    # Plan-Aware Access Methods (Level 1: Plan membership grants)
    # =========================================================================

    def _plan_free_subjects_key(self, plan_id: str) -> str:
        """Generate Redis key for plan's free subjects set."""
        return f"{self.prefix}plan:{plan_id}:free_subjects"

    async def is_subject_free_in_plan(self, plan_id: str, subject_id: str) -> bool:
        """Check if subject is marked non-premium in player's plan.

        Per CONTEXT.md: is_premium=0 on Memora Plan Subject means the subject
        is included in the plan without requiring an explicit grant.

        O(1) complexity via SISMEMBER.

        Args:
            plan_id: The plan identifier
            subject_id: The subject identifier

        Returns:
            True if subject is free in the plan (is_premium=0)
        """
        if not plan_id:
            return False
        key = self._plan_free_subjects_key(plan_id)
        result = await self.redis.sismember(key, subject_id)
        return bool(result)

    async def get_plan_free_subjects(self, plan_id: str | None) -> list[str]:
        """Get subjects marked as non-premium in player's plan (from Redis cache).

        Args:
            plan_id: The plan identifier, or None if player has no plan

        Returns:
            List of subject IDs that are free in the plan
        """
        if not plan_id:
            return []
        key = self._plan_free_subjects_key(plan_id)
        members = await self.redis.smembers(key)
        return [m.decode() if isinstance(m, bytes) else m for m in members]

    async def check_access_with_plan(
        self, player_id: str, content_key: str, plan_id: str | None
    ) -> bool:
        """Check access via explicit grant OR plan membership.

        Per CONTEXT.md: Grants are additive (direct OR plan membership).

        Returns True if:
        1. Player has explicit grant (SUB-* in Redis set), OR
        2. Subject is in player's plan with is_premium=0

        Args:
            player_id: Player's user ID
            content_key: Access key (e.g., "SUB-MATH")
            plan_id: Player's plan ID (from JWT), or None

        Returns:
            True if player has access through either method
        """
        # Check explicit grant first (fast path)
        if await self.check_access(player_id, content_key):
            return True

        # Check plan membership (if plan provided and content is subject-level)
        if plan_id and content_key.startswith("SUB-"):
            subject_id = content_key.replace("SUB-", "")
            if await self.is_subject_free_in_plan(plan_id, subject_id):
                return True

        return False
