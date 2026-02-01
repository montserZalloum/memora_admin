"""Health check endpoints."""

from fastapi import APIRouter

from fastapi_app.core.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict:
    """Liveness check - fast, no dependencies."""
    settings = get_settings()
    return {"status": "alive", "api_version": settings.api_version}
