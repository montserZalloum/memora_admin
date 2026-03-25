"""Plan API endpoints for mobile app consumption."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from fastapi_app.api.deps import get_plan_service
from fastapi_app.models.plan import PlanManifest
from fastapi_app.services.plan import PlanService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get(
	"/{plan_id}/manifest",
	response_model=PlanManifest,
	summary="Get plan manifest",
	description="Fetch plan manifest with subjects list for mobile app display",
)
async def get_plan_manifest(
	plan_id: str,
	plan_service: Annotated[PlanService, Depends(get_plan_service)],
) -> PlanManifest:
	"""
	Get plan manifest JSON.

	Returns plan metadata and subjects list with:
	- Subject titles and images
	- total_lessons, total_tracks counts
	- is_free_preview flag

	Raises:
		404: Plan not found
	"""
	manifest = await plan_service.get_manifest(plan_id)

	if not manifest:
		logger.warning("plan_manifest_not_found", plan_id=plan_id)
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail=f"Plan {plan_id} not found",
		)

	logger.info("plan_manifest_served", plan_id=plan_id, subjects_count=len(manifest.subjects))
	return manifest
