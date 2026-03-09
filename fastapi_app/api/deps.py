"""Shared dependencies for API endpoints."""

import json
from typing import Annotated

import jwt
import redis.asyncio as redis
import structlog
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer

from fastapi_app.core.config import Settings, get_settings
from fastapi_app.core.redis_keys import player_ratelimit_key
from fastapi_app.core.redis_keys import session_key as _session_key_fn
from fastapi_app.core.security import decode_token
from fastapi_app.models.access import ContentAccessRequest, SeasonMeta
from fastapi_app.models.auth import TokenPayload
from fastapi_app.services.access import AccessService
from fastapi_app.services.announcements import AnnouncementService
from fastapi_app.services.catalog import CatalogService
from fastapi_app.services.challenge import ChallengeService
from fastapi_app.services.device import DeviceService
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.game_session import GameSessionService
from fastapi_app.services.global_rate_limit import GlobalRateLimiter, RateLimitExceeded
from fastapi_app.services.hierarchy import HierarchyService
from fastapi_app.services.leaderboard import LeaderboardService
from fastapi_app.services.live_challenge import LiveChallengeService
from fastapi_app.services.plan import PlanService
from fastapi_app.services.plan_change import PlanChangeService
from fastapi_app.services.practice import PracticeService
from fastapi_app.services.profile import ProfileService
from fastapi_app.services.profile_page import ProfilePageService
from fastapi_app.services.progress import ProgressService
from fastapi_app.services.purchase import PurchaseService
from fastapi_app.services.review import ReviewService
from fastapi_app.services.season import SeasonService
from fastapi_app.services.settings import SettingsService
from fastapi_app.services.stats import StatsService
from fastapi_app.services.voucher import VoucherService
from fastapi_app.services.wallet import WalletService

logger = structlog.get_logger()

# Common dependencies
SettingsDep = Annotated[Settings, Depends(get_settings)]

# HTTP Bearer security scheme
security = HTTPBearer()

# PERF-18: In-process TTL cache for session family_id validation.
# Each uvicorn worker has its own instance. TTL=5s, maxsize=10k users/worker.
_session_fid_cache: TTLCache[str, str] = TTLCache(maxsize=10_000, ttl=5)


def evict_session_cache(user_id: str) -> None:
	"""Remove cached session fid after login/logout (best-effort, same-worker only)."""
	_session_fid_cache.pop(user_id, None)


async def get_redis(request: Request) -> redis.Redis:
	"""Get Redis client from connection pool stored in app state."""
	return redis.Redis(connection_pool=request.app.state.redis_pool)


async def get_redis_raw(request: Request) -> redis.Redis | None:
	"""Get raw Redis client (decode_responses=False) for binary-safe reads.

	Returns None if raw pool is not configured (e.g. in test environments),
	causing ProgressService to fall back to BITFIELD decode.
	"""
	raw_pool = getattr(request.app.state, "redis_raw_pool", None)
	if raw_pool is None:
		return None
	return redis.Redis(connection_pool=raw_pool)


# Type alias for dependency injection
RedisClient = Annotated[redis.Redis, Depends(get_redis)]


async def get_current_user(
	credentials: Annotated[str, Depends(security)],
	redis_client: RedisClient,
	settings: SettingsDep,
) -> TokenPayload:
	"""
	JWT verification with Redis session validation for single-session enforcement.

	Checks:
	1. Token signature is valid (HS256)
	2. Token is not expired
	3. Token type is "access"
	4. Required claims present (sub, exp, type, fid)
	5. Session family_id matches current active session in Redis

	Step 5 enforces single-session: when a player logs in on a new device,
	the old device's tokens are immediately invalidated because the family_id
	in Redis no longer matches.

	Returns TokenPayload with user claims.
	Raises 401 if session has been superseded by a newer login.
	"""
	credentials_exception = HTTPException(
		status_code=status.HTTP_401_UNAUTHORIZED,
		detail="Invalid credentials",
		headers={"WWW-Authenticate": "Bearer"},
	)

	session_expired_exception = HTTPException(
		status_code=status.HTTP_401_UNAUTHORIZED,
		detail={"code": "SESSION_SUPERSEDED", "message": "Session invalidated by new login"},
		headers={"WWW-Authenticate": "Bearer"},
	)

	# HTTPBearer returns HTTPAuthorizationCredentials with .credentials attribute
	token = credentials.credentials

	try:
		payload = decode_token(token, verify_type="access")
		token_payload = TokenPayload(**payload)
	except jwt.ExpiredSignatureError:
		raise credentials_exception
	except jwt.InvalidTokenError:
		raise credentials_exception
	except Exception:
		# Catch-all for any validation errors (e.g., missing fields in TokenPayload)
		raise credentials_exception

	# PERF-18: Check in-process cache first (avoids Redis roundtrip ~90% of the time)
	cached_fid = _session_fid_cache.get(token_payload.sub)
	if cached_fid is not None and cached_fid == token_payload.fid:
		return token_payload

	# Validate session family_id against Redis (single-session enforcement)
	session_key = _session_key_fn(token_payload.sub)
	try:
		raw = await redis_client.get(session_key)
		if raw is None:
			# No active session at all -- token is orphaned
			logger.info("session_not_found", user_id=token_payload.sub)
			raise session_expired_exception

		if isinstance(raw, bytes):
			raw = raw.decode("utf-8")

		try:
			session_data = json.loads(raw)
			current_fid = session_data.get("fid")
		except json.JSONDecodeError:
			# Legacy plain family_id string
			current_fid = raw

		if current_fid != token_payload.fid:
			logger.info(
				"session_superseded",
				user_id=token_payload.sub,
				token_fid=token_payload.fid,
				current_fid=current_fid,
			)
			raise session_expired_exception

		# PERF-18: Redis confirmed valid — cache for next ~5 seconds
		_session_fid_cache[token_payload.sub] = current_fid

	except HTTPException:
		raise
	except Exception as e:
		# Redis failure should NOT block the request -- degrade gracefully
		# Log warning but allow the request through (stateless fallback)
		logger.warning("session_check_redis_error", error=str(e), user_id=token_payload.sub)

	return token_payload


# Type alias for protected endpoints
CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]


# --- Admin Dependency ---

ADMIN_ROLE = "System Manager"


async def require_admin(user: CurrentUser) -> TokenPayload:
	"""Dependency that requires admin role. Raises 403 if not admin."""
	if getattr(user, "role", None) != ADMIN_ROLE:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "ADMIN_REQUIRED", "message": "Admin role required"},
		)
	return user


RequireAdmin = Annotated[TokenPayload, Depends(require_admin)]


# --- Service Dependencies ---


async def get_announcement_service(redis_client: RedisClient) -> AnnouncementService:
	"""Get AnnouncementService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return AnnouncementService(redis_client, frappe_client)


AnnouncementServiceDep = Annotated[AnnouncementService, Depends(get_announcement_service)]


async def get_season_service(redis_client: RedisClient) -> SeasonService:
	"""Get SeasonService with Redis and FrappeClient from app state."""
	frappe_client = await get_frappe_client()
	return SeasonService(redis_client, frappe=frappe_client)


SeasonServiceDep = Annotated[SeasonService, Depends(get_season_service)]


async def get_access_service(redis_client: RedisClient) -> AccessService:
	"""Get AccessService with Redis and FrappeClient from app state."""
	frappe_client = await get_frappe_client()
	return AccessService(redis_client, frappe_client=frappe_client)


AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]


async def get_progress_service(
	redis_client: RedisClient,
	raw_redis: Annotated[redis.Redis | None, Depends(get_redis_raw)],
) -> ProgressService:
	"""Get ProgressService with Redis and FrappeClient from app state."""
	frappe_client = await get_frappe_client()
	return ProgressService(redis_client, frappe_client=frappe_client, raw_redis=raw_redis)


ProgressServiceDep = Annotated[ProgressService, Depends(get_progress_service)]


async def get_wallet_service(redis_client: RedisClient) -> WalletService:
	"""Get WalletService with Redis and FrappeClient from app state."""
	frappe_client = await get_frappe_client()
	return WalletService(redis_client, frappe_client=frappe_client)


WalletServiceDep = Annotated[WalletService, Depends(get_wallet_service)]


async def get_device_service(redis_client: RedisClient) -> DeviceService:
	"""Get DeviceService with Redis from app state."""
	return DeviceService(redis_client)


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]


async def get_game_session_service(redis_client: RedisClient) -> GameSessionService:
	"""Get GameSessionService with Redis from app state."""
	return GameSessionService(redis_client)


GameSessionServiceDep = Annotated[GameSessionService, Depends(get_game_session_service)]


async def get_leaderboard_service(redis_client: RedisClient) -> LeaderboardService:
	"""Get LeaderboardService with Redis from app state."""
	return LeaderboardService(redis_client)


LeaderboardServiceDep = Annotated[LeaderboardService, Depends(get_leaderboard_service)]


async def get_stats_service(redis_client: RedisClient) -> StatsService:
	"""Get StatsService with Redis from app state."""
	return StatsService(redis_client)


StatsServiceDep = Annotated[StatsService, Depends(get_stats_service)]


# Singleton for FrappeClient
_frappe_client: FrappeClient | None = None


def set_frappe_client(client: FrappeClient) -> None:
	"""Set the shared FrappeClient instance (called by lifespan)."""
	global _frappe_client
	_frappe_client = client


async def get_frappe_client() -> FrappeClient:
	"""Get singleton FrappeClient instance."""
	global _frappe_client
	if _frappe_client is None:
		_frappe_client = FrappeClient()
	return _frappe_client


async def get_settings_service(redis_client: RedisClient) -> SettingsService:
	"""Get SettingsService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return SettingsService(redis_client, frappe_client)


SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]


async def get_hierarchy_service(redis_client: RedisClient) -> HierarchyService:
	"""Get HierarchyService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return HierarchyService(redis_client, frappe_client)


HierarchyServiceDep = Annotated[HierarchyService, Depends(get_hierarchy_service)]


async def get_plan_service(redis_client: RedisClient) -> PlanService:
	"""Get PlanService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return PlanService(redis_client, frappe_client)


PlanServiceDep = Annotated[PlanService, Depends(get_plan_service)]


async def get_profile_service(redis_client: RedisClient) -> ProfileService:
	"""Get ProfileService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return ProfileService(redis_client, frappe_client)


ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]


async def get_profile_page_service(redis_client: RedisClient) -> ProfilePageService:
	"""Get ProfilePageService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return ProfilePageService(redis_client, frappe_client)


ProfilePageServiceDep = Annotated[ProfilePageService, Depends(get_profile_page_service)]


async def get_catalog_service(redis_client: RedisClient) -> CatalogService:
	"""Get CatalogService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return CatalogService(redis_client, frappe_client)


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]


async def get_purchase_service(redis_client: RedisClient) -> PurchaseService:
	"""Get PurchaseService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return PurchaseService(redis_client, frappe_client)


PurchaseServiceDep = Annotated[PurchaseService, Depends(get_purchase_service)]


async def get_review_service(redis_client: RedisClient) -> ReviewService:
	"""Get ReviewService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return ReviewService(redis_client, frappe_client)


ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]


async def get_practice_service(
	redis_client: RedisClient,
	raw_redis: Annotated[redis.Redis | None, Depends(get_redis_raw)],
	settings: SettingsDep,
) -> PracticeService:
	"""Get PracticeService with all required dependencies."""
	frappe_client = await get_frappe_client()
	hierarchy_service = HierarchyService(redis_client, frappe_client)
	access_service = AccessService(redis_client, frappe_client=frappe_client)
	progress_service = ProgressService(redis_client, frappe_client=frappe_client, raw_redis=raw_redis)
	return PracticeService(
		redis_client,
		frappe_client,
		settings,
		hierarchy_service,
		access_service,
		progress_service,
	)


PracticeServiceDep = Annotated[PracticeService, Depends(get_practice_service)]


async def get_plan_change_service(redis_client: RedisClient) -> PlanChangeService:
	"""Get PlanChangeService with Redis and FrappeClient."""
	frappe_client = await get_frappe_client()
	return PlanChangeService(redis_client, frappe_client)


PlanChangeServiceDep = Annotated[PlanChangeService, Depends(get_plan_change_service)]


async def get_live_challenge_service(request: Request) -> LiveChallengeService:
	"""Get singleton LiveChallengeService from app state (shares submission queue)."""
	return request.app.state.live_challenge_service


LiveChallengeServiceDep = Annotated[LiveChallengeService, Depends(get_live_challenge_service)]


async def get_challenge_service(redis_client: RedisClient) -> ChallengeService:
	"""Get ChallengeService with Redis, FrappeClient, and sub-services."""
	frappe_client = await get_frappe_client()
	hierarchy_service = HierarchyService(redis_client, frappe_client)
	access_service = AccessService(redis_client, frappe_client=frappe_client)
	stats_service = StatsService(redis_client)
	plan_service = PlanService(redis_client, frappe_client)
	return ChallengeService(
		redis_client,
		frappe_client=frappe_client,
		hierarchy_service=hierarchy_service,
		access_service=access_service,
		stats_service=stats_service,
		plan_service=plan_service,
	)


ChallengeServiceDep = Annotated[ChallengeService, Depends(get_challenge_service)]


async def get_voucher_service(redis_client: RedisClient, settings: SettingsDep) -> VoucherService:
	"""Get VoucherService with Redis, FrappeClient, and HMAC secret."""
	frappe_client = await get_frappe_client()
	return VoucherService(redis_client, frappe_client, settings.voucher_hmac_secret)


VoucherServiceDep = Annotated[VoucherService, Depends(get_voucher_service)]


# --- Per-Player Rate Limit Dependency ---

_SCOPE_SETTINGS = {
	"reviews": "reviews_rate_limit",
	"session_start": "session_rate_limit",
	"session_end": "session_rate_limit",
	"practice_hierarchy": "practice_hierarchy_rate_limit",
	"practice_start": "practice_start_rate_limit",
	"practice_submit": "practice_submit_rate_limit",
	"practice_continue": "practice_continue_rate_limit",
	"lc_join": "lc_join_rate_limit",
	"lc_submit": "lc_submit_rate_limit",
	"ch_hierarchy": "ch_hierarchy_rate_limit",
	"ch_attempt": "ch_attempt_rate_limit",
	"ch_leaderboard": "ch_leaderboard_rate_limit",
}


def require_rate_limit(scope: str):
	"""Factory that returns a FastAPI dependency for per-player rate limiting.

	Uses GlobalRateLimiter with key memora:rl:{scope}:{player_id}.
	Reads limit from settings based on scope. Fails open on Redis errors.
	"""
	setting_attr = _SCOPE_SETTINGS[scope]

	async def _check_rate_limit(
		user: CurrentUser,
		redis_client: RedisClient,
		settings: SettingsDep,
	):
		limit = getattr(settings, setting_attr)
		window = settings.global_rate_limit_window
		limiter = GlobalRateLimiter(redis_client)
		key = player_ratelimit_key(scope, user.sub)
		allowed, count, ttl = await limiter.check(key, limit, window)
		if not allowed:
			raise RateLimitExceeded(max(ttl, 1))

	return _check_rate_limit


# --- Double-Gate Dependencies ---


async def require_active_season(
	user: CurrentUser,
	season_service: SeasonServiceDep,
) -> SeasonMeta | None:
	"""Gate 1: Block if player's season is expired or unpublished.

	Reads season_id from JWT. Old tokens without season are allowed through
	(backward compat — they'll get season on next login/refresh).
	"""
	if not user.season:
		return None  # Old token without season — allow through
	return await require_season_access(user.season, season_service)


ActiveSeasonDep = Annotated[SeasonMeta | None, Depends(require_active_season)]


async def require_season_access(
	season_id: str,
	season_service: SeasonServiceDep,
) -> SeasonMeta:
	"""
	Gate 1: Validate season is active and not expired.

	Raises:
	    HTTPException 403 if season fails validation

	Returns:
	    SeasonMeta for the validated season
	"""
	season = await season_service.get_season_meta(season_id)

	if not season:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "SEASON_NOT_FOUND", "message": "Season not available"},
		)

	if not season.is_published:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "SEASON_INACTIVE", "message": "Season is not active"},
		)

	if season.is_expired:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "SEASON_EXPIRED", "message": "Season has ended"},
		)

	return season


async def require_content_access(
	content: ContentAccessRequest,
	user: CurrentUser,
	access_service: AccessServiceDep,
) -> bool:
	"""
	Gate 2: Validate player has access to content.

	Per CONTEXT.md:
	- Free content (is_free=true) bypasses this check entirely
	- Grants are additive (direct OR plan membership)

	Raises:
	    HTTPException 403 if player lacks access

	Returns:
	    True if access granted
	"""
	# Check free content FIRST (per RESEARCH.md pitfall #3)
	if content.is_free:
		return True

	has_access = await access_service.check_access_with_plan(
		player_id=user.sub,
		content_key=content.content_key,
		plan_id=user.plan,
	)

	if not has_access:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "NO_ACCESS", "message": "Content access required"},
		)

	return True


async def require_double_gate(
	season_id: str,
	content: ContentAccessRequest,
	user: CurrentUser,
	season_service: SeasonServiceDep,
	access_service: AccessServiceDep,
) -> tuple[SeasonMeta, bool]:
	"""
	Combined Double-Gate validation.

	1. Gate 1: Validate season
	2. Gate 2: Validate player access (unless content is free)

	Returns:
	    Tuple of (SeasonMeta, access_granted: bool)
	"""
	# Gate 1
	season = await require_season_access(season_id, season_service)

	# Gate 2
	access_granted = await require_content_access(content, user, access_service)

	return (season, access_granted)


# Type aliases for dependency injection
RequireSeasonAccess = Annotated[SeasonMeta, Depends(require_season_access)]
RequireContentAccess = Annotated[bool, Depends(require_content_access)]
