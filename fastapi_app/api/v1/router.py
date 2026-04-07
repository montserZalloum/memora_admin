"""API v1 router."""

from fastapi import APIRouter

from fastapi_app.api.v1.endpoints import (
	access,
	announcements,
	auth,
	bootstrap,
	catalog,
	challenge,
	event_access,
	health,
	leaderboard,
	live_challenge,
	monetized_webhooks,
	notifications,
	plan_change,
	plans,
	practice,
	premium,
	profile,
	progress,
	purchase,
	push,
	reports,
	reviews,
	sessions,
	settings,
	subscriptions,
	voucher,
	wallet,
	webhooks,
)

router = APIRouter(prefix="/api/v1")

router.include_router(health.router)
router.include_router(bootstrap.router)
router.include_router(announcements.router)
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
router.include_router(reports.router)
router.include_router(voucher.router)
router.include_router(practice.router)
router.include_router(plan_change.router)
router.include_router(live_challenge.router)
router.include_router(challenge.router)
router.include_router(premium.router)
router.include_router(push.router)
router.include_router(event_access.router)
router.include_router(monetized_webhooks.router)
