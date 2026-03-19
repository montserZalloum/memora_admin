"""Player subscription discovery endpoint."""

import asyncio

from fastapi import APIRouter

from fastapi_app.api.deps import AccessServiceDep, CurrentUser, RedisClient, get_frappe_client
from fastapi_app.services.premium import PremiumService

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("")
async def get_my_subscriptions(
	user: CurrentUser,
	access_service: AccessServiceDep,
	redis_client: RedisClient,
) -> dict:
	"""Return the current player's subscriptions from Redis.

	- grants: explicit paid grants (SUB-*, TRK-*)
	- plan_subjects: subjects included via plan membership (free content only)
	- has_premium: whether the player has an active plan premium
	"""
	grants, plan_subjects = await asyncio.gather(
		access_service.get_player_grants(user.sub),
		access_service.get_plan_free_subjects(user.plan),
	)

	has_premium = False
	if user.plan:
		frappe_client = await get_frappe_client()
		premium_svc = PremiumService(redis_client, frappe_client)
		state = await premium_svc.is_plan_premium_usable(user.sub, user.plan)
		has_premium = state.usable

	return {
		"grants": sorted(grants),
		"plan_subjects": sorted(f"SUB-{s}" for s in plan_subjects),
		"has_premium": has_premium,
	}
