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
