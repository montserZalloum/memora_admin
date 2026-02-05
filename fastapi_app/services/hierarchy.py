"""Hierarchy caching service for fast unlock calculations."""

from typing import Optional

import redis.asyncio as redis

from fastapi_app.models.progress import SubjectHierarchy
from fastapi_app.services.frappe_client import FrappeClient


class HierarchyService:
    """Cache subject hierarchy for fast unlock calculations.

    Per RESEARCH.md:
    - Cache with 1 hour TTL
    - Invalidation via Redis pub/sub (Phase 6)
    - Critical for <20ms response time target
    """

    CACHE_TTL = 3600  # 1 hour

    def __init__(
        self,
        redis_client: redis.Redis,
        frappe_client: FrappeClient,
        key_prefix: str = "memora:",
    ):
        self.redis = redis_client
        self.frappe = frappe_client
        self.prefix = key_prefix

    def _cache_key(self, subject_id: str) -> str:
        """Generate Redis key for hierarchy cache."""
        return f"{self.prefix}hierarchy:{subject_id}"

    async def get_hierarchy(self, subject_id: str) -> Optional[SubjectHierarchy]:
        """
        Get subject hierarchy from cache or Frappe.

        1. Check Redis cache
        2. If miss, fetch from Frappe API
        3. Cache result with TTL

        Returns:
            SubjectHierarchy or None if subject not found
        """
        key = self._cache_key(subject_id)

        # Try cache first
        cached = await self.redis.get(key)
        if cached:
            # Handle bytes response
            data = cached.decode() if isinstance(cached, bytes) else cached
            return SubjectHierarchy.model_validate_json(data)

        # Cache miss - fetch from Frappe
        result = await self.frappe.call(
            "memora_admin.api.hierarchy.get_subject_hierarchy",
            {"subject_id": subject_id},
        )

        if not result:
            return None

        # Parse into model
        hierarchy = SubjectHierarchy.model_validate(result)

        # Cache with TTL
        await self.redis.set(
            key,
            hierarchy.model_dump_json(),
            ex=self.CACHE_TTL,
        )

        return hierarchy

    async def invalidate(self, subject_id: str) -> None:
        """
        Invalidate hierarchy cache for subject.

        Called when:
        - Content structure changes (Phase 6 build)
        - Manual cache clear
        """
        key = self._cache_key(subject_id)
        await self.redis.delete(key)

    async def invalidate_all(self) -> None:
        """
        Invalidate all hierarchy caches.

        Uses SCAN to find keys matching pattern.
        Use sparingly - for major content updates.
        """
        pattern = f"{self.prefix}hierarchy:*"
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            if keys:
                await self.redis.delete(*keys)
            if cursor == 0:
                break

    # =========================================================================
    # Free Content Methods
    # =========================================================================

    def _free_content_subjects_key(self) -> str:
        """Redis key for set of subjects that have free units or topics."""
        return f"{self.prefix}subjects_with_free_content"

    async def get_subjects_with_free_content(self) -> list[str]:
        """Get list of subjects that have free units or topics.

        This is cached in Redis and updated by Frappe hooks when
        Unit.is_free or Topic.is_free changes.

        Returns:
            List of subject IDs that have at least one free unit/topic
        """
        key = self._free_content_subjects_key()
        members = await self.redis.smembers(key)
        return [m.decode() if isinstance(m, bytes) else m for m in members]
