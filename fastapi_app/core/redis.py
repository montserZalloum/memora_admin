"""Redis connection pool management with fail-fast verification."""

import time
from functools import wraps
from typing import Any, Callable, TypeVar

import redis.asyncio as redis
import structlog

from fastapi_app.core.config import get_settings

logger = structlog.get_logger()

F = TypeVar("F", bound=Callable[..., Any])


async def create_redis_pool() -> redis.ConnectionPool:
    """Create Redis connection pool from settings."""
    settings = get_settings()
    pool = redis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=20,
        decode_responses=True,
    )
    return pool


async def verify_redis_connection(pool: redis.ConnectionPool) -> None:
    """Verify Redis is reachable. Raises RuntimeError if not (fail fast)."""
    client = redis.Redis(connection_pool=pool)
    try:
        if not await client.ping():
            raise RuntimeError("Redis ping returned False")
        logger.info("redis_connected", url=get_settings().redis_url)
    except redis.ConnectionError as e:
        await pool.disconnect()
        raise RuntimeError(f"Cannot start without Redis: {e}") from e
    finally:
        await client.aclose()


def log_slow_redis(threshold_ms: int | None = None) -> Callable[[F], F]:
    """Log Redis operations that exceed threshold (default from settings)."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            settings = get_settings()
            limit = threshold_ms or settings.slow_redis_threshold_ms
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                if duration_ms > limit:
                    logger.warning(
                        "slow_redis_operation",
                        operation=func.__name__,
                        duration_ms=round(duration_ms, 2),
                    )

        return wrapper  # type: ignore[return-value]

    return decorator
