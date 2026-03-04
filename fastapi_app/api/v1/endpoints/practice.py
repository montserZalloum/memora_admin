"""Practice Arena endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from fastapi_app.api.deps import ActiveSeasonDep, CurrentUser, PracticeServiceDep, require_rate_limit
from fastapi_app.models.practice import (
	PracticeBatchResponse,
	PracticeHierarchyResponse,
	PracticeSubmitResponse,
	StartPracticeRequest,
	SubmitPracticeRequest,
)
from fastapi_app.services.practice import (
	BatchSeqMismatchError,
	InvalidSessionStateError,
	NoActiveSessionError,
	NoItemsError,
	OffBatchItemError,
	PracticeAccessDenied,
	PracticeHierarchyMetaUnavailableError,
	PracticeSubjectNotFoundError,
	PreviousBatchNotSubmittedError,
)

router = APIRouter(prefix="/practice", tags=["practice"])


@router.get(
	"/hierarchy",
	response_model=PracticeHierarchyResponse,
	dependencies=[Depends(require_rate_limit("practice_hierarchy"))],
)
async def get_practice_hierarchy(
	user: CurrentUser,
	practice_service: PracticeServiceDep,
	subject_id: str = Query(..., description="Subject to browse"),
	filter: Literal["all", "completed"] = Query("all", description="Filter mode"),
) -> PracticeHierarchyResponse:
	"""Browse content hierarchy with item counts and access flags for practice."""
	try:
		return await practice_service.get_practice_hierarchy(
			player_id=user.sub,
			subject_id=subject_id,
			plan_id=getattr(user, "plan", None),
			filter_mode=filter,
		)
	except PracticeSubjectNotFoundError:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
		)
	except PracticeHierarchyMetaUnavailableError:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail={
				"code": "PRACTICE_META_UNAVAILABLE",
				"message": "Practice metadata is temporarily unavailable",
			},
		)


@router.post(
	"/start",
	response_model=PracticeBatchResponse,
	dependencies=[Depends(require_rate_limit("practice_start"))],
)
async def start_practice(
	body: StartPracticeRequest,
	user: CurrentUser,
	_season: ActiveSeasonDep,
	practice_service: PracticeServiceDep,
) -> PracticeBatchResponse:
	"""Start a new practice session. Validates access, returns first batch."""
	# Validate request constraints
	if not body.tracks:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
			detail="tracks must be non-empty",
		)

	if len(body.tracks) > 1 and (body.units or body.topics):
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
			detail="units and topics must be empty when selecting multiple tracks",
		)

	if len(body.tracks) == 1 and len(body.units) > 1 and body.topics:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
			detail="topics must be empty when selecting multiple units",
		)

	try:
		return await practice_service.start_session(
			player_id=user.sub,
			subject_id=body.subject_id,
			plan_id=getattr(user, "plan", None),
			filter_mode=body.filter,
			tracks=body.tracks,
			units=body.units,
			topics=body.topics,
		)
	except PracticeAccessDenied as e:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "NO_ACCESS", "tracks": e.denied_tracks},
		)
	except NoItemsError:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
			detail={"code": "NO_ITEMS", "message": "No reviewable items match the selected filters"},
		)


@router.post(
	"/submit",
	response_model=PracticeSubmitResponse,
	dependencies=[Depends(require_rate_limit("practice_submit"))],
)
async def submit_practice(
	body: SubmitPracticeRequest,
	user: CurrentUser,
	practice_service: PracticeServiceDep,
) -> PracticeSubmitResponse:
	"""Submit results for the current practice batch. Idempotent via batch_seq."""
	try:
		return await practice_service.submit_batch(
			player_id=user.sub,
			batch_seq=body.batch_seq,
			results=[r.model_dump() for r in body.results],
		)
	except NoActiveSessionError:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="NO_ACTIVE_SESSION",
		)
	except BatchSeqMismatchError as e:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"code": "BATCH_SEQ_MISMATCH", "expected": e.expected, "received": e.received},
		)
	except OffBatchItemError as e:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
			detail={"code": "OFF_BATCH_ITEMS", "items": e.off_batch_ids[:5]},
		)
	except InvalidSessionStateError as e:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"code": "INVALID_SESSION_STATE", "missing_field": e.missing_field},
		)


@router.post(
	"/continue",
	response_model=PracticeBatchResponse,
	dependencies=[Depends(require_rate_limit("practice_continue"))],
)
async def continue_practice(
	user: CurrentUser,
	practice_service: PracticeServiceDep,
) -> PracticeBatchResponse:
	"""Request the next batch of questions in an active session."""
	try:
		return await practice_service.continue_session(
			player_id=user.sub,
		)
	except NoActiveSessionError:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="NO_ACTIVE_SESSION",
		)
	except PreviousBatchNotSubmittedError as e:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
			detail={"code": "PREVIOUS_BATCH_NOT_SUBMITTED", "batch_seq": e.batch_seq},
		)
