"""FastAPI Game API Sidecar - Main Application."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI

from fastapi_app.api.v1.router import router as v1_router
from fastapi_app.core.config import get_settings
from fastapi_app.core.logging import configure_logging
from fastapi_app.core.redis import create_redis_pool, verify_redis_connection
from fastapi_app.middleware.request_id import RequestIDMiddleware

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager."""
    settings = get_settings()
    configure_logging(settings.environment)

    logger.info("fastapi_starting", environment=settings.environment)

    # Create Redis pool and verify connection (fail fast)
    pool = await create_redis_pool()
    await verify_redis_connection(pool)
    app.state.redis_pool = pool

    yield

    # Cleanup
    await pool.disconnect()
    logger.info("fastapi_shutdown")


app = FastAPI(
    title="Memora Game API",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(RequestIDMiddleware)

# Routers
app.include_router(v1_router)
