"""Shared dependencies for API endpoints."""

from typing import Annotated

import jwt
import redis.asyncio as redis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer

from fastapi_app.core.config import Settings, get_settings
from fastapi_app.core.security import decode_token
from fastapi_app.models.access import ContentAccessRequest, SeasonMeta
from fastapi_app.models.auth import TokenPayload
from fastapi_app.services.access import AccessService
from fastapi_app.services.catalog import CatalogService
from fastapi_app.services.device import DeviceService
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.game_session import GameSessionService
from fastapi_app.services.hierarchy import HierarchyService
from fastapi_app.services.leaderboard import LeaderboardService
from fastapi_app.services.plan import PlanService
from fastapi_app.services.profile import ProfileService
from fastapi_app.services.profile_page import ProfilePageService
from fastapi_app.services.progress import ProgressService
from fastapi_app.services.purchase import PurchaseService
from fastapi_app.services.review import ReviewService
from fastapi_app.services.season import SeasonService
from fastapi_app.services.settings import SettingsService
from fastapi_app.services.stats import StatsService
from fastapi_app.services.wallet import WalletService

# Common dependencies
SettingsDep = Annotated[Settings, Depends(get_settings)]

# HTTP Bearer security scheme
security = HTTPBearer()


async def get_redis(request: Request) -> redis.Redis:
	"""Get Redis client from connection pool stored in app state."""
	return redis.Redis(connection_pool=request.app.state.redis_pool)


# Type alias for dependency injection
RedisClient = Annotated[redis.Redis, Depends(get_redis)]


async def get_current_user(
	credentials: Annotated[str, Depends(security)],
) -> TokenPayload:
	"""
	Stateless JWT verification - no database lookup per CONTEXT.md.

	Checks:
	1. Token signature is valid (HS256)
	2. Token is not expired
	3. Token type is "access"
	4. Required claims present (sub, exp, type, fid)

	Returns TokenPayload with user claims.
	"""
	credentials_exception = HTTPException(
		status_code=status.HTTP_401_UNAUTHORIZED,
		detail="Invalid credentials",
		headers={"WWW-Authenticate": "Bearer"},
	)

	# HTTPBearer returns HTTPAuthorizationCredentials with .credentials attribute
	token = credentials.credentials

	try:
		payload = decode_token(token, verify_type="access")
		return TokenPayload(**payload)

	except jwt.ExpiredSignatureError:
		raise credentials_exception
	except jwt.InvalidTokenError:
		raise credentials_exception
	except Exception:
		# Catch-all for any validation errors (e.g., missing fields in TokenPayload)
		raise credentials_exception


# Type alias for protected endpoints
CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]


# --- Service Dependencies ---


async def get_season_service(request: Request) -> SeasonService:
	"""Get SeasonService with Redis from app state."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	return SeasonService(redis_client)


SeasonServiceDep = Annotated[SeasonService, Depends(get_season_service)]


async def get_access_service(request: Request) -> AccessService:
	"""Get AccessService with Redis and FrappeClient from app state."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return AccessService(redis_client, frappe_client=frappe_client)


AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]


async def get_progress_service(request: Request) -> ProgressService:
	"""Get ProgressService with Redis and FrappeClient from app state."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return ProgressService(redis_client, frappe_client=frappe_client)


ProgressServiceDep = Annotated[ProgressService, Depends(get_progress_service)]


async def get_wallet_service(request: Request) -> WalletService:
	"""Get WalletService with Redis and FrappeClient from app state."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return WalletService(redis_client, frappe_client=frappe_client)


WalletServiceDep = Annotated[WalletService, Depends(get_wallet_service)]


async def get_device_service(request: Request) -> DeviceService:
	"""Get DeviceService with Redis from app state."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	return DeviceService(redis_client)


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]


async def get_game_session_service(request: Request) -> GameSessionService:
	"""Get GameSessionService with Redis from app state."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	return GameSessionService(redis_client)


GameSessionServiceDep = Annotated[GameSessionService, Depends(get_game_session_service)]


async def get_leaderboard_service(request: Request) -> LeaderboardService:
	"""Get LeaderboardService with Redis from app state."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	return LeaderboardService(redis_client)


LeaderboardServiceDep = Annotated[LeaderboardService, Depends(get_leaderboard_service)]


async def get_stats_service(request: Request) -> StatsService:
	"""Get StatsService with Redis from app state."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	return StatsService(redis_client)


StatsServiceDep = Annotated[StatsService, Depends(get_stats_service)]


# Singleton for FrappeClient
_frappe_client: FrappeClient | None = None


async def get_frappe_client() -> FrappeClient:
	"""Get singleton FrappeClient instance."""
	global _frappe_client
	if _frappe_client is None:
		_frappe_client = FrappeClient()
	return _frappe_client


async def get_settings_service(request: Request) -> SettingsService:
	"""Get SettingsService with Redis and FrappeClient."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return SettingsService(redis_client, frappe_client)


SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]


async def get_hierarchy_service(request: Request) -> HierarchyService:
	"""Get HierarchyService with Redis and FrappeClient."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return HierarchyService(redis_client, frappe_client)


HierarchyServiceDep = Annotated[HierarchyService, Depends(get_hierarchy_service)]


async def get_plan_service(request: Request) -> PlanService:
	"""Get PlanService with Redis and FrappeClient."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return PlanService(redis_client, frappe_client)


PlanServiceDep = Annotated[PlanService, Depends(get_plan_service)]


async def get_profile_service(request: Request) -> ProfileService:
	"""Get ProfileService with Redis and FrappeClient."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return ProfileService(redis_client, frappe_client)


ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]


async def get_profile_page_service(request: Request) -> ProfilePageService:
	"""Get ProfilePageService with Redis and FrappeClient."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return ProfilePageService(redis_client, frappe_client)


ProfilePageServiceDep = Annotated[ProfilePageService, Depends(get_profile_page_service)]


async def get_catalog_service(request: Request) -> CatalogService:
	"""Get CatalogService with Redis and FrappeClient."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return CatalogService(redis_client, frappe_client)


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]


async def get_purchase_service(request: Request) -> PurchaseService:
	"""Get PurchaseService with Redis and FrappeClient."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return PurchaseService(redis_client, frappe_client)


PurchaseServiceDep = Annotated[PurchaseService, Depends(get_purchase_service)]


async def get_review_service(request: Request) -> ReviewService:
	"""Get ReviewService with Redis and FrappeClient."""
	redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
	frappe_client = await get_frappe_client()
	return ReviewService(redis_client, frappe_client)


ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]


# --- Double-Gate Dependencies ---


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
