"""Player subscription discovery endpoint."""

import asyncio

from fastapi import APIRouter

from fastapi_app.api.deps import AccessServiceDep, CurrentUser

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("")
async def get_my_subscriptions(
	user: CurrentUser,
	access_service: AccessServiceDep,
) -> dict:
	"""Return the current player's subscriptions from Redis.

	- grants: explicit paid grants (SUB-*, TRK-*)
	- plan_subjects: subjects included via plan membership (free content only)
	"""
	grants, plan_subjects = await asyncio.gather(
		access_service.get_player_grants(user.sub),
		access_service.get_plan_free_subjects(user.plan),
	)

	return {
		"grants": sorted(grants),
		"plan_subjects": sorted(f"SUB-{s}" for s in plan_subjects),
	}
