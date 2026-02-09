"""Review endpoints for FSRS spaced repetition."""

from fastapi import APIRouter
import structlog

from fastapi_app.api.deps import CurrentUser, ReviewServiceDep, WalletServiceDep
from fastapi_app.models.review import (
	DueStage,
	DueStagesResponse,
	ReviewOverviewResponse,
	ReviewSubmitRequest,
	ReviewSubmitResponse,
	SubjectReviewCount,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("", response_model=ReviewOverviewResponse)
async def get_review_overview(
	user: CurrentUser,
	review_service: ReviewServiceDep,
):
	"""Get overview of due reviews per subject for the authenticated player.

	Returns subjects that have at least one stage due for review,
	with the count of due stages per subject. Cached in Redis for 5 minutes.
	"""
	subjects_data = await review_service.get_overview(user.sub)

	subjects = [
		SubjectReviewCount(
			subject_id=s.get("subject", ""),
			due_count=s.get("due_count", 0),
		)
		for s in subjects_data
		if s.get("due_count", 0) > 0
	]

	return ReviewOverviewResponse(subjects=subjects)


@router.get("/{subject}", response_model=DueStagesResponse)
async def get_due_stages(
	subject: str,
	user: CurrentUser,
	review_service: ReviewServiceDep,
):
	"""Get up to 10 due stages for a specific subject, oldest first (FIFO).

	Each stage includes stage_id, lesson_id, and stage_type for client rendering.
	Always returns fresh data (no cache).
	"""
	result = await review_service.get_due_stages(user.sub, subject)

	stages = [
		DueStage(
			stage_id=s.get("stage_id", ""),
			lesson_id=s.get("lesson_id", ""),
			stage_type=s.get("stage_type", ""),
		)
		for s in result.get("stages", [])
	]

	return DueStagesResponse(
		subject_id=subject,
		stages=stages,
		has_more=result.get("has_more", False),
	)


@router.post("/{subject}/submit", response_model=ReviewSubmitResponse)
async def submit_reviews(
	subject: str,
	body: ReviewSubmitRequest,
	user: CurrentUser,
	review_service: ReviewServiceDep,
	wallet_service: WalletServiceDep,
):
	"""Submit batch of reviewed stages for a subject.

	Awards 3 XP per review session (not per stage). Reviews do NOT update streak.
	Invalidates the cached review overview for this player.
	"""
	stages_data = [{"stage_id": s.stage_id, "fail_count": s.fail_count} for s in body.stages]

	result = await review_service.submit_reviews(user.sub, subject, stages_data)

	processed = result.get("processed", 0)
	xp_awarded = 0

	if processed > 0:
		XP_PER_REVIEW_SESSION = 3
		await wallet_service.award_xp(user.sub, XP_PER_REVIEW_SESSION)
		xp_awarded = XP_PER_REVIEW_SESSION

		logger.info(
			"review_xp_awarded",
			player=user.sub,
			subject=subject,
			processed=processed,
			xp=xp_awarded,
		)

	return ReviewSubmitResponse(
		processed=processed,
		remaining_due=result.get("remaining_due", 0),
		has_more=result.get("has_more", False),
		xp_awarded=xp_awarded,
	)
