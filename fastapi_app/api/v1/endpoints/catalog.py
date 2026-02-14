"""Product catalog endpoint."""

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, HTTPException, status

from fastapi_app.api.deps import CatalogServiceDep, CurrentUser
from fastapi_app.models.catalog import CatalogResponse

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
