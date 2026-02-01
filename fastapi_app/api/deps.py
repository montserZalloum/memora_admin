"""Shared dependencies for API endpoints."""

from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends, Request

from fastapi_app.core.config import Settings, get_settings

# Common dependencies
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_redis(request: Request) -> redis.Redis:
    """Get Redis client from connection pool stored in app state."""
    return redis.Redis(connection_pool=request.app.state.redis_pool)


# Type alias for dependency injection
RedisClient = Annotated[redis.Redis, Depends(get_redis)]
