"""FastAPI Game API Sidecar - Main Application."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI

from fastapi_app.api.v1.router import router as v1_router
from fastapi_app.core.config import get_settings
from fastapi_app.core.logging import configure_logging
from fastapi_app.middleware.request_id import RequestIDMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager."""
    settings = get_settings()
    configure_logging(settings.environment)

    logger = structlog.get_logger()
    logger.info("FastAPI starting up", environment=settings.environment)

    yield

    logger.info("FastAPI shutting down")


app = FastAPI(
    title="Memora Game API",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(RequestIDMiddleware)

# Routers
app.include_router(v1_router)
