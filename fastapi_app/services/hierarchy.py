"""Hierarchy caching service for fast unlock calculations."""

import time

import redis.asyncio as redis

from fastapi_app.core.redis_keys import hierarchy_key as _hierarchy_key_fn
from fastapi_app.core.redis_keys import subjects_with_free_content_key
from fastapi_app.models.progress import SubjectHierarchy
from fastapi_app.services.frappe_client import FrappeClient

# Process-local in-memory cache for parsed SubjectHierarchy objects.
# Each uvicorn worker gets its own copy. Keyed by subject_id -> (hierarchy, expires_at_monotonic).
# Expired entries are swept on every cache write to prevent unbounded growth from one-off subjects.
_local_hierarchy_cache: dict[str, tuple[SubjectHierarchy, float]] = {}


def _sweep_expired_local_cache() -> None:
    """Remove expired entries from the local hierarchy cache."""
    now = time.monotonic()
    expired = [k for k, (_, exp) in _local_hierarchy_cache.items() if now >= exp]
    for k in expired:
        _local_hierarchy_cache.pop(k, None)


class HierarchyService:
    """Cache subject hierarchy for fast unlock calculations.

    Per RESEARCH.md:
    - Cache with 1 hour TTL
    - Invalidation via Redis pub/sub (Phase 6)
    - Critical for <20ms response time target
    """

    CACHE_TTL = 3600  # 1 hour
    LOCAL_TTL = 300  # 5 minutes for in-process cache

    def __init__(
        self,
        redis_client: redis.Redis,
        frappe_client: FrappeClient,
    ):
        self.redis = redis_client
        self.frappe = frappe_client

    def _cache_key(self, subject_id: str) -> str:
        """Generate Redis key for hierarchy cache."""
        return _hierarchy_key_fn(subject_id)

    async def get_hierarchy(self, subject_id: str) -> SubjectHierarchy | None:
        """
        Get subject hierarchy from local cache, Redis, or Frappe.

        1. Check in-process local cache (< 1µs)
        2. Check Redis cache
        3. If miss, fetch from Frappe API
        4. Cache result in both local and Redis

        Returns:
            SubjectHierarchy or None if subject not found
        """
        # Fast path: in-process local cache hit (returns defensive copy)
        entry = _local_hierarchy_cache.get(subject_id)
        if entry is not None:
            hierarchy, expires_at = entry
            if time.monotonic() < expires_at:
                return hierarchy.model_copy(deep=True)
            # Expired - remove stale entry and fall through
            _local_hierarchy_cache.pop(subject_id, None)

        key = self._cache_key(subject_id)

        # Try Redis cache
        cached = await self.redis.get(key)
        if cached:
            # Handle bytes response
            data = cached.decode() if isinstance(cached, bytes) else cached
            hierarchy = SubjectHierarchy.model_validate_json(data)
            # Sweep expired entries then store in local cache
            _sweep_expired_local_cache()
            _local_hierarchy_cache[subject_id] = (hierarchy, time.monotonic() + self.LOCAL_TTL)
            return hierarchy.model_copy(deep=True)

        # Cache miss - fetch from Frappe
        result = await self.frappe.call(
            "memora_admin.api.hierarchy.get_subject_hierarchy",
            {"subject_id": subject_id},
        )

        if not result:
            return None

        # Parse into model
        hierarchy = SubjectHierarchy.model_validate(result)

        # Cache with TTL (both Redis and local)
        await self.redis.set(
            key,
            hierarchy.model_dump_json(),
            ex=self.CACHE_TTL,
        )
        _sweep_expired_local_cache()
        _local_hierarchy_cache[subject_id] = (hierarchy, time.monotonic() + self.LOCAL_TTL)

        # Auto-repair subjects_with_free_content set on cache miss
        if hierarchy.has_any_free_content():
            await self.redis.sadd(self._free_content_subjects_key(), subject_id)

        return hierarchy.model_copy(deep=True)

    async def invalidate(self, subject_id: str) -> None:
        """
        Invalidate hierarchy cache for subject (both Redis and local).

        Called when:
        - Content structure changes (Phase 6 build)
        - Manual cache clear
        """
        key = self._cache_key(subject_id)
        await self.redis.delete(key)
        _local_hierarchy_cache.pop(subject_id, None)

    async def invalidate_all(self) -> None:
        """
        Invalidate all hierarchy caches (both Redis and local).

        Uses SCAN to find keys matching pattern.
        Use sparingly - for major content updates.
        """
        pattern = _hierarchy_key_fn("*")
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            if keys:
                await self.redis.delete(*keys)
            if cursor == 0:
                break
        _local_hierarchy_cache.clear()

    # =========================================================================
    # Free Content Methods
    # =========================================================================

    def _free_content_subjects_key(self) -> str:
        """Redis key for set of subjects that have free units or topics."""
        return subjects_with_free_content_key()

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
