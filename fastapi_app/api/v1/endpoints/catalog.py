"""Product catalog endpoint."""

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, HTTPException, status

from fastapi_app.api.deps import CatalogServiceDep, CurrentUser, EventCatalogServiceDep, PremiumCatalogServiceDep
from fastapi_app.models.catalog import CatalogResponse
from fastapi_app.models.event_catalog import EventCatalogResponse
from fastapi_app.models.premium_catalog import PremiumCatalogResponse, PremiumVoucherCatalogResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/", response_model=CatalogResponse)
async def get_catalog(
	user: CurrentUser,
	catalog_service: CatalogServiceDep,
) -> CatalogResponse:
	"""Get product catalog for player's plan.

	Returns purchasable products filtered for the authenticated player:
	- Products already fully purchased (all subjects accessible) are excluded
	- Products with pending transactions are excluded
	- Players with no plan get an empty catalog (200 OK)

	Redis cache failure returns 503 Service Unavailable.
	"""
	if not user.plan:
		return CatalogResponse(products=[])

	try:
		products = await catalog_service.get_player_catalog(
			plan_id=user.plan,
			player_id=user.sub,
		)
	except redis.RedisError:
		logger.error("catalog_redis_error", player_id=user.sub, plan_id=user.plan)
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Service temporarily unavailable",
		)
	except Exception:
		logger.exception("catalog_unexpected_error", player_id=user.sub, plan_id=user.plan)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Internal server error",
		)

	return CatalogResponse(products=products)


@router.get("/events/", response_model=EventCatalogResponse)
async def get_event_catalog(
	user: CurrentUser,
	svc: EventCatalogServiceDep,
) -> EventCatalogResponse:
	"""Get purchasable paid events for the authenticated player.

	Returns upcoming paid events the player can purchase:
	- Premium users get an empty list (they join paid events for free)
	- Events the player already has access to are excluded
	- Only upcoming events (scheduled_start > now) are shown
	- Only paid events eligible for the player's plan are shown
	"""
	if not user.plan:
		return EventCatalogResponse(events=[])

	try:
		events = await svc.get_player_event_catalog(
			plan_id=user.plan,
			player_id=user.sub,
		)
	except redis.RedisError:
		logger.error("event_catalog_redis_error", player_id=user.sub, plan_id=user.plan)
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Service temporarily unavailable",
		)
	except Exception:
		logger.exception("event_catalog_unexpected_error", player_id=user.sub, plan_id=user.plan)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Internal server error",
		)

	return EventCatalogResponse(events=events)


@router.get("/premium/", response_model=PremiumCatalogResponse)
async def get_premium_catalog(
	user: CurrentUser,
	svc: PremiumCatalogServiceDep,
) -> PremiumCatalogResponse:
	"""Get plan premium purchase info for the authenticated player.

	Returns whether premium is available for purchase:
	- Players with no plan get available=False
	- Players who already have premium get available=False, has_premium=True
	- Players with a pending purchase get available=False, has_pending_purchase=True
	- Plans without premium pricing configured return available=False
	"""
	if not user.plan:
		return PremiumCatalogResponse(available=False)

	try:
		return await svc.get_player_premium_catalog(
			plan_id=user.plan,
			player_id=user.sub,
		)
	except redis.RedisError:
		logger.error("premium_catalog_redis_error", player_id=user.sub, plan_id=user.plan)
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Service temporarily unavailable",
		)
	except Exception:
		logger.exception("premium_catalog_unexpected_error", player_id=user.sub, plan_id=user.plan)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Internal server error",
		)


@router.get("/premium/voucher/", response_model=PremiumVoucherCatalogResponse)
async def get_premium_voucher_catalog(
	user: CurrentUser,
	svc: PremiumCatalogServiceDep,
) -> PremiumVoucherCatalogResponse:
	"""Check if plan premium voucher cards are available for the player.

	Returns whether there are active plan_premium voucher batches with
	allocated cards eligible for the player's plan. The frontend uses this
	to show a "Redeem Voucher for Premium" option in the store.

	- Players with no plan get available=False
	- Players who already have premium get available=False
	- No active batches with allocated cards → available=False
	"""
	if not user.plan:
		return PremiumVoucherCatalogResponse(available=False)

	try:
		return await svc.get_premium_voucher_catalog(
			plan_id=user.plan,
		)
	except redis.RedisError:
		logger.error("premium_voucher_catalog_redis_error", player_id=user.sub, plan_id=user.plan)
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Service temporarily unavailable",
		)
	except Exception:
		logger.exception("premium_voucher_catalog_unexpected_error", player_id=user.sub, plan_id=user.plan)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Internal server error",
		)
