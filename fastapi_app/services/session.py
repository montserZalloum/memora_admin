"""Session management for single-session per player via token family ID."""

import json
import uuid
from typing import Optional

import redis.asyncio as redis

from fastapi_app.core.redis_keys import session_key as _session_key_fn


class SessionService:
    """
    Manages single-session per player via token family ID.

    Per CONTEXT.md:
    - New login invalidates previous session
    - Old device discovers invalidation on next API call (401)

    Session data is stored as JSON: {"fid": family_id, "plan": plan_id}
    This allows refresh token flow to get plan_id without Frappe roundtrip.
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def create_session(
        self, user_id: str, plan_id: str, ttl_days: int = 30, season_id: str | None = None
    ) -> str:
        """
        Create new session, invalidating any previous session.

        Args:
            user_id: The player's user ID
            plan_id: Player's plan document name (e.g., 'PLAN-00001')
            ttl_days: Session TTL (matches refresh token lifetime)
            season_id: Player's season ID (e.g., 'SEAS-00027')

        Returns:
            New family_id to embed in tokens
        """
        family_id = str(uuid.uuid4())
        key = _session_key_fn(user_id)

        # Store session data as JSON (overwrites old, auto-invalidating previous session)
        data = {"fid": family_id, "plan": plan_id}
        if season_id:
            data["season"] = season_id
        session_data = json.dumps(data)
        await self.redis.set(key, session_data, ex=ttl_days * 24 * 3600)
        return family_id

    async def validate_session(self, user_id: str, family_id: str) -> tuple[bool, str | None, str | None]:
        """
        Check if family_id matches current session.

        Returns:
            Tuple of (is_valid, plan_id, season_id):
            - (True, plan_id, season_id) if session is valid
            - (False, None, None) if invalidated by new login or no session
        """
        session_data = await self.get_session_data(user_id)

        if session_data is None:
            return (False, None, None)

        if session_data.get("fid") != family_id:
            return (False, None, None)

        return (True, session_data.get("plan"), session_data.get("season"))

    async def invalidate_session(self, user_id: str) -> bool:
        """
        Explicitly invalidate session (logout).

        Returns:
            True if session existed and was deleted, False otherwise
        """
        key = _session_key_fn(user_id)
        deleted = await self.redis.delete(key)
        return deleted > 0

    async def get_session_data(self, user_id: str) -> dict | None:
        """
        Get session data including plan_id for refresh flow.

        Returns:
            Dict with "fid" and "plan" keys, or None if no session
        """
        key = _session_key_fn(user_id)
        raw = await self.redis.get(key)

        if raw is None:
            return None

        # Handle both bytes and str responses (depends on decode_responses setting)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Legacy format (plain family_id string) - return as dict for compatibility
            return {"fid": raw, "plan": None}

    async def get_session_family_id(self, user_id: str) -> Optional[str]:
        """
        Get current session's family_id (for debugging/admin).

        Returns:
            Current family_id or None if no session
        """
        session_data = await self.get_session_data(user_id)

        if session_data is None:
            return None

        return session_data.get("fid")
