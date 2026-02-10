"""API v1 router."""

from fastapi import APIRouter

from fastapi_app.api.v1.endpoints import (
    access,
    auth,
    catalog,
    health,
    leaderboard,
    notifications,
    plans,
    profile,
    progress,
    purchase,
    reviews,
    sessions,
    settings,
    subscriptions,
    wallet,
    webhooks,
)

router = APIRouter(prefix="/api/v1")

router.include_router(health.router)
router.include_router(auth.router)
router.include_router(catalog.router)
router.include_router(purchase.router)
router.include_router(access.router)
router.include_router(leaderboard.router)
router.include_router(plans.router)
router.include_router(progress.router)
router.include_router(sessions.router)
router.include_router(settings.router)
router.include_router(subscriptions.router)
router.include_router(wallet.router)
router.include_router(webhooks.router)
router.include_router(notifications.router)
router.include_router(reviews.router)
router.include_router(profile.router)
