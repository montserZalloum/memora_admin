"""FastAPI Game API Sidecar - Main Application."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as redis
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from fastapi_app.api.deps import set_frappe_client
from fastapi_app.api.v1.router import router as v1_router
from fastapi_app.core.config import get_settings
from fastapi_app.core.logging import configure_logging
from fastapi_app.core.pubsub import start_notification_listener, start_pubsub_listener
from fastapi_app.core.redis import create_redis_pool, create_redis_raw_pool, verify_redis_connection
from fastapi_app.core.ws_manager import ConnectionManager
from fastapi_app.middleware.rate_limit import GlobalRateLimitMiddleware
from fastapi_app.middleware.request_id import RequestIDMiddleware
from fastapi_app.middleware.request_metrics import RequestMetricsMiddleware
from fastapi_app.services.announcements import AnnouncementService
from fastapi_app.services.catalog import CatalogService
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.global_rate_limit import RateLimitExceeded
from fastapi_app.services.hierarchy import HierarchyService
from fastapi_app.services.live_challenge import LiveChallengeService
from fastapi_app.services.plan import PlanService
from fastapi_app.services.practice_writer import ensure_consumer_group
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

	# Create raw pool for binary-safe bitmap reads (decode_responses=False)
	raw_pool = await create_redis_raw_pool()
	app.state.redis_raw_pool = raw_pool

	# Create Redis client for services
	redis_client = redis.Redis(connection_pool=pool)

	# Create FrappeClient instance (shared across all modules)
	frappe_client = FrappeClient()
	app.state.frappe_client = frappe_client
	set_frappe_client(frappe_client)
	if settings.frappe_site != "test.local":
		await frappe_client.warmup()

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

	# Create AnnouncementService instance for pub/sub cache invalidation
	announcement_service = AnnouncementService(
		redis_client=redis_client,
		frappe_client=frappe_client,
	)
	app.state.announcement_service = announcement_service

	# Create ConnectionManager for WebSocket notifications
	# Note: Per-message deflate compression must be disabled at the ASGI server level
	# (uvicorn --ws none or nginx proxy_set_header) to achieve 14 KiB/connection at 100K scale.
	# Default uvicorn websockets library does NOT enable per-message-deflate.
	ws_manager = ConnectionManager(
		max_connections_per_user=settings.ws_max_connections_per_user,
		broadcast_concurrency=settings.ws_broadcast_concurrency,
	)
	app.state.ws_manager = ws_manager

	# Start pub/sub listener background task (cache invalidation)
	pubsub_task = asyncio.create_task(start_pubsub_listener(pool, app.state))
	app.state.pubsub_task = pubsub_task

	# Start notification pub/sub listener (per-user WebSocket notifications)
	notify_task = asyncio.create_task(start_notification_listener(pool, app.state))
	app.state.notify_task = notify_task

	# Create LiveChallengeService singleton + start cross-worker reaction subscriber
	lc_service = LiveChallengeService(redis_client, frappe_client)
	app.state.live_challenge_service = lc_service
	await lc_service.start_reaction_subscriber()

	# Ensure Practice write queue consumer group exists (idempotent)
	await ensure_consumer_group(redis_client)

	yield

	# Signal LiveChallengeService to stop countdown loops
	if hasattr(app.state, "live_challenge_service"):
		await app.state.live_challenge_service.shutdown()

	# Cancel pub/sub listener (cache invalidation)
	if hasattr(app.state, "pubsub_task"):
		app.state.pubsub_task.cancel()
		try:
			await app.state.pubsub_task
		except asyncio.CancelledError:
			pass

	# Cancel notification listener
	if hasattr(app.state, "notify_task"):
		app.state.notify_task.cancel()
		try:
			await app.state.notify_task
		except asyncio.CancelledError:
			pass

	# Close FrappeClient
	if hasattr(app.state, "frappe_client"):
		await app.state.frappe_client.close()

	# Cleanup Redis pools
	await pool.disconnect()
	if hasattr(app.state, "redis_raw_pool"):
		await app.state.redis_raw_pool.disconnect()
	logger.info("fastapi_shutdown")


app = FastAPI(
	title="Memora Game API",
	version="1.0.0",
	lifespan=lifespan,
	redirect_slashes=True,
)

# Middleware (order: last added = first executed)
# RequestIDMiddleware runs first (outermost), then GlobalRateLimitMiddleware
settings = get_settings()
app.add_middleware(
	GlobalRateLimitMiddleware,
	limit=settings.global_rate_limit,
	window=settings.global_rate_limit_window,
	fail_open=settings.rate_limit_fail_open,
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RequestMetricsMiddleware)
app.add_middleware(
	CORSMiddleware,
	allow_origins=[
		"https://skrterak.com",
		"https://www.skrterak.com",
	],
	allow_credentials=True,
	allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
	allow_headers=["Content-Type", "Authorization", "X-Plan-ID", "X-Device-ID", "X-Request-ID"],
	expose_headers=["Content-Type"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
	"""Return 429 with consistent JSON body for per-player rate limit violations."""
	return JSONResponse(
		status_code=429,
		content={"error": "RATE_LIMITED", "retry_after": exc.retry_after},
		headers={"Retry-After": str(exc.retry_after)},
	)


# Routers
app.include_router(v1_router)
