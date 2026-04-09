"""Official Exam endpoints — start and submit mock exams."""

from fastapi import APIRouter, Depends, HTTPException, status

from fastapi_app.api.deps import CurrentUser, ExamServiceDep, require_rate_limit
from fastapi_app.models.exam import (
	ExamStartResponse,
	ExamSubmitRequest,
	ExamSubmitResponse,
)

router = APIRouter(prefix="/exams", tags=["exams"])


@router.post("/{plan_id}/{subject_id}/{exam_id}/start", response_model=ExamStartResponse)
async def start_exam(
	plan_id: str,
	subject_id: str,
	exam_id: str,
	user: CurrentUser,
	exam_service: ExamServiceDep,
) -> ExamStartResponse:
	"""Verify the player holds an EXAM-PLAN-{plan_id} grant.

	Returns 403 if the grant is missing; 200 otherwise.
	"""
	try:
		result = await exam_service.start_exam(user.sub, plan_id, subject_id, exam_id)
	except ValueError as e:
		code = str(e)
		if code == "EXAM_NOT_FOUND":
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail={"code": code, "message": "Exam not found"},
			)
		if code == "NO_EXAM_ACCESS":
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail={"code": code, "message": "Exam access required (EXAM-PLAN-* grant)"},
			)
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail={"code": code, "message": str(e)},
		)
	return ExamStartResponse(**result)


@router.post(
	"/{plan_id}/{subject_id}/{exam_id}/submit",
	response_model=ExamSubmitResponse,
	dependencies=[Depends(require_rate_limit("exam_submit"))],
)
async def submit_exam(
	plan_id: str,
	subject_id: str,
	exam_id: str,
	body: ExamSubmitRequest,
	user: CurrentUser,
	exam_service: ExamServiceDep,
) -> ExamSubmitResponse:
	"""Submit exam results — records attempt and updates best score.

	Requires EXAM-PLAN-{plan_id} grant.
	"""
	results = [{"question_idx": r.question_idx, "is_correct": r.is_correct} for r in body.results]

	try:
		result = await exam_service.submit_exam(
			player_id=user.sub,
			exam_id=exam_id,
			plan_id=plan_id,
			subject_id=subject_id,
			score=body.score,
			total=body.total,
			results=results,
		)
	except ValueError as e:
		code = str(e)
		if code == "EXAM_NOT_FOUND":
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail={"code": code, "message": "Exam not found"},
			)
		if code == "NO_EXAM_ACCESS":
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail={"code": code, "message": "Exam access required (EXAM-PLAN-* grant)"},
			)
		status_code = status.HTTP_400_BAD_REQUEST
		messages = {
			"RESULT_COUNT_MISMATCH": "Result count does not match exam question count",
			"TOTAL_MISMATCH": "Total does not match exam question count",
			"SCORE_MISMATCH": "Score does not match number of correct answers in results",
		}
		raise HTTPException(
			status_code=status_code,
			detail={"code": code, "message": messages.get(code, str(e))},
		)
	return ExamSubmitResponse(**result)
