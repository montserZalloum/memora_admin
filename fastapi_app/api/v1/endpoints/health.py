"""Health check endpoints."""

import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from fastapi_app.api.deps import RedisClient
from fastapi_app.core.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict:
    """Liveness check - fast, no dependencies."""
    settings = get_settings()
    return {"status": "alive", "api_version": settings.api_version}


@router.get("/ready")
async def readiness(redis_client: RedisClient) -> JSONResponse:
    """Readiness check - verifies Redis connection."""
    settings = get_settings()
    dependencies: dict[str, str] = {}
    overall_status = "ready"

    try:
        await redis_client.ping()
        dependencies["redis"] = "ok"
    except redis.ConnectionError:
        dependencies["redis"] = "unreachable"
        overall_status = "not_ready"

    status_code = 200 if overall_status == "ready" else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall_status,
            "api_version": settings.api_version,
            "dependencies": dependencies,
        },
        headers={"Cache-Control": "no-store"},
    )
