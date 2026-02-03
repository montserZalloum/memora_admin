"""API v1 router."""

from fastapi import APIRouter

from fastapi_app.api.v1.endpoints import access, auth, health, leaderboard, plans, progress, sessions, wallet, webhooks

router = APIRouter(prefix="/api/v1")

router.include_router(health.router)
router.include_router(auth.router)
router.include_router(access.router)
router.include_router(leaderboard.router)
router.include_router(plans.router)
router.include_router(progress.router)
router.include_router(sessions.router)
router.include_router(wallet.router)
router.include_router(webhooks.router)
