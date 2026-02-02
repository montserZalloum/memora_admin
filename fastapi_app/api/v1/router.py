"""API v1 router."""

from fastapi import APIRouter

from fastapi_app.api.v1.endpoints import access, auth, health, progress, webhooks

router = APIRouter(prefix="/api/v1")

router.include_router(health.router)
router.include_router(auth.router)
router.include_router(access.router)
router.include_router(progress.router)
router.include_router(webhooks.router)
