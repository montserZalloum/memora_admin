"""FastAPI Game API Sidecar - Main Application."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as redis
import structlog
from fastapi import FastAPI

from fastapi_app.api.v1.router import router as v1_router
from fastapi_app.core.config import get_settings
from fastapi_app.core.logging import configure_logging
from fastapi_app.core.pubsub import start_pubsub_listener
from fastapi_app.core.redis import create_redis_pool, verify_redis_connection
from fastapi_app.middleware.request_id import RequestIDMiddleware
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.hierarchy import HierarchyService
from fastapi_app.services.plan import PlanService
from fastapi_app.services.catalog import CatalogService
from fastapi_app.services.profile import ProfileService

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

    # Create Redis client for services
    redis_client = redis.Redis(connection_pool=pool)

    # Create FrappeClient instance
    frappe_client = FrappeClient()
    app.state.frappe_client = frappe_client

    # Create HierarchyService instance
    hierarchy_service = HierarchyService(
        redis_client=redis_client,
        frappe_client=frappe_client,
    )
    app.state.hierarchy_service = hierarchy_service

    # Create PlanService instance
    plan_service = PlanService(
        redis_client=redis_client,
        frappe_client=frappe_client,
    )
    app.state.plan_service = plan_service

    # Create ProfileService instance for pub/sub cache invalidation
    profile_service = ProfileService(
        redis_client=redis_client,
        frappe_client=frappe_client,
    )
    app.state.profile_service = profile_service

    # Create CatalogService instance for pub/sub cache invalidation
    catalog_service = CatalogService(
        redis_client=redis_client,
        frappe_client=frappe_client,
    )
    app.state.catalog_service = catalog_service

    # Start pub/sub listener background task
    pubsub_task = asyncio.create_task(
        start_pubsub_listener(pool, app.state)
    )
    app.state.pubsub_task = pubsub_task

    yield

    # Cancel pub/sub listener
    if hasattr(app.state, "pubsub_task"):
        app.state.pubsub_task.cancel()
        try:
            await app.state.pubsub_task
        except asyncio.CancelledError:
            pass

    # Close FrappeClient
    if hasattr(app.state, "frappe_client"):
        await app.state.frappe_client.close()

    # Cleanup Redis pool
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
