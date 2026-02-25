"""Review endpoints for FSRS spaced repetition (item-level)."""

import structlog
from fastapi import APIRouter, Depends

from fastapi_app.api.deps import (
	ActiveSeasonDep,
	CurrentUser,
	LeaderboardServiceDep,
	ReviewServiceDep,
	WalletServiceDep,
	require_rate_limit,
)
from fastapi_app.models.review import (
	DueItem,
	DueItemsResponse,
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

	Returns subjects that have at least one item due for review,
	with the count of due items per subject. Cached in Redis for 5 minutes.
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


@router.get("/{subject}", response_model=DueItemsResponse)
async def get_due_items(
	subject: str,
	user: CurrentUser,
	review_service: ReviewServiceDep,
):
	"""Get up to 10 due items for a specific subject, oldest first (FIFO).

	Each item includes item_id, stage_id, lesson_id, and stage_type.
	Always returns fresh data (no cache).
	"""
	result = await review_service.get_due_items(user.sub, subject)

	items = [
		DueItem(
			item_id=i.get("item_id", ""),
			stage_id=i.get("stage_id", ""),
			lesson_id=i.get("lesson_id", ""),
			stage_type=i.get("stage_type", ""),
			question_text=i.get("question_text"),
			choices=i.get("choices", []),
			correct_choice=i.get("correct_choice"),
			content_json=i.get("content_json"),
		)
		for i in result.get("items", [])
	]

	return DueItemsResponse(
		subject_id=subject,
		items=items,
		has_more=result.get("has_more", False),
	)


@router.post("/{subject}/submit", response_model=ReviewSubmitResponse)
async def submit_reviews(
	subject: str,
	body: ReviewSubmitRequest,
	user: CurrentUser,
	_season: ActiveSeasonDep,
	review_service: ReviewServiceDep,
	wallet_service: WalletServiceDep,
	leaderboard_service: LeaderboardServiceDep,
	_rate_limit=Depends(require_rate_limit("reviews")),
):
	"""Submit batch of reviewed items for a subject.

	Awards 3 XP per review session (not per item). Reviews do NOT update streak.
	Invalidates the cached review overview for this player.
	"""
	items_data = [{"item_id": i.item_id, "fail_count": i.fail_count} for i in body.items]

	result = await review_service.submit_reviews(user.sub, subject, items_data)

	processed = result.get("processed", 0)
	xp_awarded = 0

	if processed > 0:
		XP_PER_REVIEW_SESSION = 3
		new_total_xp = await wallet_service.award_xp(user.sub, XP_PER_REVIEW_SESSION)
		xp_awarded = XP_PER_REVIEW_SESSION

		await leaderboard_service.update_leaderboards(
			player_id=user.sub,
			xp_amount=xp_awarded,
			new_total_xp=new_total_xp,
			subject_id=subject,
			plan_id=user.plan,
		)

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
