"""Redis connection pool management with fail-fast verification."""

import redis.asyncio as redis
import structlog

from fastapi_app.core.config import get_settings

logger = structlog.get_logger()


async def create_redis_pool() -> redis.ConnectionPool:
    """Create Redis connection pool from settings."""
    settings = get_settings()
    pool = redis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        decode_responses=True,
    )
    return pool


async def create_redis_raw_pool() -> redis.ConnectionPool:
    """Create Redis connection pool for binary-safe reads (decode_responses=False).

    Used by ProgressService.get_completed_bits() to fetch raw bitmap bytes
    via a single GET instead of chunked BITFIELD commands.
    """
    settings = get_settings()
    pool = redis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        decode_responses=False,
    )
    return pool


async def verify_redis_connection(pool: redis.ConnectionPool) -> None:
    """Verify Redis is reachable. Raises RuntimeError if not (fail fast)."""
    client = redis.Redis(connection_pool=pool)
    try:
        if not await client.ping():
            raise RuntimeError("Redis ping returned False")
        settings = get_settings()
        logger.info(
            "redis_connected",
            url=settings.redis_url,
            pool_size=settings.redis_max_connections,
        )
    except redis.ConnectionError as e:
        await pool.disconnect()
        raise RuntimeError(f"Cannot start without Redis: {e}") from e
    finally:
        await client.close()
