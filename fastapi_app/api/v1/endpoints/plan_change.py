"""Plan Change endpoints — plan browsing and execution."""

import structlog
from fastapi import APIRouter, HTTPException, status

from fastapi_app.api.deps import CurrentUser, PlanChangeServiceDep
from fastapi_app.models.plan_change import (
	AvailablePlan,
	AvailablePlansResponse,
	GradePlanGroup,
	PlanChangeErrorResponse,
	PlanChangeRequest,
	PlanChangeResponse,
)
from fastapi_app.services.plan_change import PlanChangeError, PlanChangeResult

logger = structlog.get_logger()

router = APIRouter(prefix="/plans", tags=["Plans"])

# Error code -> HTTP status mapping
_ERROR_STATUS_MAP = {
	"SAME_PLAN": status.HTTP_400_BAD_REQUEST,
	"INVALID_PLAN": status.HTTP_400_BAD_REQUEST,
	"INVALID_PLAYER": status.HTTP_400_BAD_REQUEST,
	"PLAN_CHANGE_IN_PROGRESS": status.HTTP_409_CONFLICT,
	"COOLDOWN_ACTIVE": status.HTTP_429_TOO_MANY_REQUESTS,
}


@router.post("/change", response_model=PlanChangeResponse)
async def change_plan(
	request: PlanChangeRequest,
	user: CurrentUser,
	plan_change_service: PlanChangeServiceDep,
):
	"""Execute a plan change with clean slate.

	Changes the player's plan to a new eligible plan. Performs complete
	data reset: snapshot preservation, subscription/progress deletion,
	wallet zeroing, leaderboard removal, cache cleanup, and session
	invalidation. Player must re-login after success.
	"""
	player_id = user.sub
	current_plan_id = user.plan or ""

	result = await plan_change_service.execute(
		player_id=player_id,
		new_plan_id=request.new_plan_id,
		current_plan_id=current_plan_id,
	)

	if isinstance(result, PlanChangeError):
		http_status = _ERROR_STATUS_MAP.get(result.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
		error_detail = {"error": result.code, "message": result.message}
		if result.retry_after:
			error_detail["retry_after"] = result.retry_after
		raise HTTPException(status_code=http_status, detail=error_detail)

	return PlanChangeResponse(
		success=True,
		message="Plan changed successfully. Please log in again.",
		history_id=result.history_id,
		previous_plan_id=result.previous_plan,
		new_plan_id=result.new_plan,
	)


@router.get("/available", response_model=AvailablePlansResponse)
async def get_available_plans(
	user: CurrentUser,
	plan_change_service: PlanChangeServiceDep,
):
	"""Get plans available for switching, grouped by grade.

	Returns plans linked to active seasons (published, end_date >= today),
	excluding the player's current plan.
	"""
	current_plan_id = user.plan or ""

	plans = await plan_change_service.get_available_plans(current_plan_id)

	# Group by grade
	grade_map: dict[str, GradePlanGroup] = {}
	for p in plans:
		grade_id = p.get("grade") or ""
		grade_name = p.get("grade_name") or ""
		if grade_id not in grade_map:
			grade_map[grade_id] = GradePlanGroup(
				grade_id=grade_id,
				grade_name=grade_name,
				plans=[],
			)
		grade_map[grade_id].plans.append(
			AvailablePlan(
				plan_id=p["name"],
				plan_name=p.get("plan_name") or "",
				grade_id=grade_id,
				grade_name=grade_name,
				major_id=p.get("major") or "",
				major_name=p.get("major_name") or "",
				season_id=p.get("season") or "",
				season_title=p.get("season_title") or "",
			)
		)

	grades = list(grade_map.values())
	total = sum(len(g.plans) for g in grades)

	return AvailablePlansResponse(grades=grades, total=total)
