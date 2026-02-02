"""API v1 router."""

from fastapi import APIRouter

from fastapi_app.api.v1.endpoints import auth, health

router = APIRouter(prefix="/api/v1")

router.include_router(health.router)
router.include_router(auth.router)
