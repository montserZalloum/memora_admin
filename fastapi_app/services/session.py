"""Session management for single-session per player via token family ID."""

import uuid
from typing import Optional

import redis.asyncio as redis


class SessionService:
    """
    Manages single-session per player via token family ID.

    Per CONTEXT.md:
    - New login invalidates previous session
    - Old device discovers invalidation on next API call (401)
    """

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:session:"):
        self.redis = redis_client
        self.prefix = key_prefix

    async def create_session(self, user_id: str, ttl_days: int = 30) -> str:
        """
        Create new session, invalidating any previous session.

        Args:
            user_id: The player's user ID
            ttl_days: Session TTL (matches refresh token lifetime)

        Returns:
            New family_id to embed in tokens
        """
        family_id = str(uuid.uuid4())
        key = f"{self.prefix}{user_id}"

        # Store new family_id (overwrites old, auto-invalidating previous session)
        await self.redis.set(key, family_id, ex=ttl_days * 24 * 3600)
        return family_id

    async def validate_session(self, user_id: str, family_id: str) -> bool:
        """
        Check if family_id matches current session.

        Returns:
            True if session is valid, False if invalidated by new login
        """
        key = f"{self.prefix}{user_id}"
        current_fid = await self.redis.get(key)

        if current_fid is None:
            return False

        # Handle both bytes and str responses (depends on decode_responses setting)
        if isinstance(current_fid, bytes):
            current_fid = current_fid.decode("utf-8")

        return current_fid == family_id

    async def invalidate_session(self, user_id: str) -> bool:
        """
        Explicitly invalidate session (logout).

        Returns:
            True if session existed and was deleted, False otherwise
        """
        key = f"{self.prefix}{user_id}"
        deleted = await self.redis.delete(key)
        return deleted > 0

    async def get_session_family_id(self, user_id: str) -> Optional[str]:
        """
        Get current session's family_id (for debugging/admin).

        Returns:
            Current family_id or None if no session
        """
        key = f"{self.prefix}{user_id}"
        fid = await self.redis.get(key)

        if fid is None:
            return None

        if isinstance(fid, bytes):
            return fid.decode("utf-8")

        return fid
