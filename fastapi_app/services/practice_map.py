"""Practice Arena — Map file loader with in-process cache.

Loads practice map JSON from local storage, caches parsed dicts in a
process-level dict keyed by subject_id with a 1-hour TTL safety net.
Cache invalidation is driven by Redis pubsub on the
``memora:practice:map_invalidation`` channel.

Each uvicorn worker gets its own copy of the cache (~5 MB for 10 subjects).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from fastapi_app.core.redis_keys import PRACTICE_MAP_INVALIDATION_CHANNEL

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# In-process cache: subject_id -> (parsed_dict, expires_at_monotonic)
# ---------------------------------------------------------------------------
_map_cache: dict[str, tuple[dict, float]] = {}

_MAP_TTL_SECONDS = 3600  # 1 hour safety net


def _sweep_expired() -> None:
    """Remove expired entries from the local map cache."""
    now = time.monotonic()
    expired = [k for k, (_, exp) in _map_cache.items() if now >= exp]
    for k in expired:
        _map_cache.pop(k, None)


def evict(subject_id: str) -> None:
    """Evict a single subject from the in-process cache.

    Called by the pubsub listener when a map invalidation message arrives.
    """
    removed = _map_cache.pop(subject_id, None)
    if removed is not None:
        logger.info("practice_map_cache_evict", subject_id=subject_id)


def evict_all() -> None:
    """Evict all entries (used in tests or on full rebuild)."""
    _map_cache.clear()


def get_map(subject_id: str, *, maps_dir: str | Path) -> dict:
    """Return the parsed map data for *subject_id*.

    On cache hit, returns immediately.  On miss, reads the map JSON file
    from ``{maps_dir}/{subject_id}.json``, parses it, and stores it in the
    process-local cache with a 1-hour TTL.

    Parameters
    ----------
    subject_id:
        Subject identifier (e.g. ``"SUBJ-001"``).
    maps_dir:
        Absolute path to the directory containing map JSON files.
        Typically ``{bench}/sites/{site}/public/files/cdn/practice/maps``.

    Returns
    -------
    dict
        Parsed map data structure (see data-model.md section 3).

    Raises
    ------
    FileNotFoundError
        If the map file does not exist on disk.
    """
    now = time.monotonic()

    # Cache hit
    entry = _map_cache.get(subject_id)
    if entry is not None:
        data, expires_at = entry
        if now < expires_at:
            return data
        # Expired — fall through to reload
        _map_cache.pop(subject_id, None)

    # Cache miss — load from disk
    base = Path(maps_dir).resolve()
    map_path = (base / f"{subject_id}.json").resolve()
    if not map_path.is_relative_to(base):
        raise ValueError(f"Path traversal blocked: {subject_id!r}")
    raw = map_path.read_text(encoding="utf-8")
    data = json.loads(raw)

    # Sweep stale entries before inserting new one
    _sweep_expired()

    _map_cache[subject_id] = (data, now + _MAP_TTL_SECONDS)
    logger.info(
        "practice_map_cache_miss",
        subject_id=subject_id,
        file_size=len(raw),
    )
    return data


# ---------------------------------------------------------------------------
# Pubsub handler — wired up in fastapi_app/core/pubsub.py
# ---------------------------------------------------------------------------

INVALIDATION_CHANNEL = PRACTICE_MAP_INVALIDATION_CHANNEL


async def handle_map_invalidation(message: dict) -> None:
    """Handle a Redis pubsub message on the map invalidation channel.

    Expected message data is the ``subject_id`` string to invalidate.
    If the data is ``"*"``, all entries are evicted.
    """
    subject_id = message.get("data")
    if isinstance(subject_id, bytes):
        subject_id = subject_id.decode("utf-8")
    if not subject_id:
        return

    if subject_id == "*":
        evict_all()
    else:
        evict(subject_id)
