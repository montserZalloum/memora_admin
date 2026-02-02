"""Season metadata cache service for Gate 1 validation."""

from datetime import date
from typing import Optional

import redis.asyncio as redis

from fastapi_app.models.access import SeasonMeta


class SeasonService:
    """Manages season metadata cache via Redis hashes.

    Provides O(1) lookups for Gate 1 season validation:
    - is_published check
    - is_expired check (date comparison)
    - is_started check (date comparison)
    """

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _season_key(self, season_id: str) -> str:
        """Generate Redis key for season metadata."""
        return f"{self.prefix}season:{season_id}"

    async def get_season_meta(self, season_id: str) -> Optional[SeasonMeta]:
        """Get season metadata from cache.

        Args:
            season_id: The season document name/ID

        Returns:
            SeasonMeta if cached, None if not found (fallback to MariaDB needed)
        """
        key = self._season_key(season_id)
        data = await self.redis.hgetall(key)

        if not data:
            return None

        # Handle both bytes and str responses (depends on decode_responses setting)
        def decode_value(val: bytes | str) -> str:
            if isinstance(val, bytes):
                return val.decode("utf-8")
            return val

        # Parse is_published as "1"/"0" string
        is_published_raw = data.get(b"is_published") or data.get("is_published")
        is_published = decode_value(is_published_raw) == "1" if is_published_raw else False

        # Parse dates from ISO format strings
        start_date_raw = data.get(b"start_date") or data.get("start_date")
        end_date_raw = data.get(b"end_date") or data.get("end_date")

        if not start_date_raw or not end_date_raw:
            return None

        return SeasonMeta(
            season_id=season_id,
            is_published=is_published,
            start_date=date.fromisoformat(decode_value(start_date_raw)),
            end_date=date.fromisoformat(decode_value(end_date_raw)),
        )

    async def set_season_meta(self, season: SeasonMeta) -> None:
        """Cache season metadata.

        Called from Frappe doc_events hook on season save.
        Uses HSET with mapping for atomic update.

        Args:
            season: The SeasonMeta to cache
        """
        key = self._season_key(season.season_id)
        await self.redis.hset(
            key,
            mapping={
                "is_published": "1" if season.is_published else "0",
                "start_date": season.start_date.isoformat(),
                "end_date": season.end_date.isoformat(),
            },
        )

    async def delete_season_meta(self, season_id: str) -> None:
        """Remove season from cache (on delete).

        Args:
            season_id: The season document name/ID to remove
        """
        key = self._season_key(season_id)
        await self.redis.delete(key)
