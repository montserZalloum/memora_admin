"""Live Challenge endpoints — join, submit, result, leaderboard, WebSocket."""

import json

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from fastapi_app.api.deps import CurrentUser, LiveChallengeServiceDep, require_rate_limit
from fastapi_app.core.redis_keys import lc_joined_key, lc_mode_key, lc_status_key
from fastapi_app.core.security import decode_token
from fastapi_app.models.live_challenge import (
	AnswerRequest,
	AnswerResponse,
	EventDetailResponse,
	JoinResponse,
	LeaderboardResponse,
	QuestionsResponse,
	ResultResponse,
	StatusResponse,
	SubmitRequest,
	SubmitResponse,
)
from fastapi_app.services.last_stand_engine import ANSWER_ERROR_CODES
from fastapi_app.services.live_challenge import LiveChallengeService

logger = structlog.get_logger()

# Structured error messages (code → Arabic user-facing message)
_ERROR_MESSAGES: dict[str, str] = {
	"EVENT_NOT_FOUND": "لم نتمكن من العثور على هذا الامتحان",
	"EVENT_NOT_ACTIVE": "هذا الامتحان غير متاح حاليًا",
	"NOT_A_PARTICIPANT": "أنت غير مسجل في هذا الامتحان",
	"ALREADY_SUBMITTED": "لقد سلّمت إجاباتك مسبقًا",
	"EVENT_NOT_JOINABLE": "هذا الامتحان غير متاح للدخول حاليًا",
	"ALREADY_JOINED": "لقد انضممت إلى هذا الامتحان مسبقًا",
	"PLAN_NOT_ELIGIBLE": "خطتك الدراسية لا تتيح لك دخول هذا الامتحان",
	"CAPACITY_FULL": "لم يعد هناك مقاعد متاحة في هذا الامتحان",
	"NO_LATE_JOIN": "لا يمكن الانضمام بعد بدء المسابقة",
	"SUBMISSION_FAILED": "تعذر تسليم إجاباتك، يرجى المحاولة مرة أخرى",
	"NO_PARTICIPATION": "يبدو أنك لم تشارك في هذا الامتحان",
	"EVENT_NOT_ENDED": "النتائج بالطريق ✨ بس لسه في كم طالب ما خلصوا الامتحان.",
	"MODE_NOT_SUPPORTED": "هذا النوع من الامتحانات لا يدعم هذه العملية",
	"ROUND_MISMATCH": "الإجابة لا تتوافق مع الجولة الحالية",
	"WINDOW_CLOSED": "انتهى وقت الإجابة",
	"ALREADY_ANSWERED": "لقد أجبت على هذه الجولة مسبقًا",
	"NOT_ALIVE": "لقد تم إقصاؤك من المسابقة",
}


_GENERIC_ERROR_MESSAGE = "حدث خطأ"


def _error_detail(code: str) -> dict[str, str]:
	return {"code": code, "message": _ERROR_MESSAGES.get(code, _GENERIC_ERROR_MESSAGE)}


router = APIRouter(prefix="/live-challenge", tags=["live-challenge"])


@router.get("/{event_id}/status", response_model=StatusResponse)
async def get_event_status(
	event_id: str,
	service: LiveChallengeServiceDep,
) -> StatusResponse:
	"""Lightweight Redis-only status check with client-driven transitions.

	Returns the current event status, triggering any due transitions atomically.
	No auth required — status is public. Sub-2ms for non-transition reads.
	"""
	result = await service.get_status(event_id)
	if result is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_error_detail("EVENT_NOT_FOUND"))
	return StatusResponse(**result)


@router.get(
	"/{event_id}",
	response_model=EventDetailResponse,
	dependencies=[Depends(require_rate_limit("lc_read"))],
)
async def get_event_detail(
	event_id: str,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> EventDetailResponse:
	"""Get public event details with player-specific flags."""
	detail = await service.get_event_detail(event_id, user.sub)
	if detail is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_error_detail("EVENT_NOT_FOUND"))
	return EventDetailResponse(**detail)


@router.get(
	"/{event_id}/questions",
	response_model=QuestionsResponse,
	dependencies=[Depends(require_rate_limit("lc_read"))],
)
async def get_questions(
	event_id: str,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> QuestionsResponse:
	"""REST fallback: get exam questions when WebSocket is unavailable.

	Only available when event is active, player has joined, and hasn't submitted.
	"""
	try:
		result = await service.get_questions(event_id, user.sub)
	except ValueError as e:
		code = str(e)
		status_map = {
			"EVENT_NOT_ACTIVE": status.HTTP_400_BAD_REQUEST,
			"NOT_A_PARTICIPANT": status.HTTP_403_FORBIDDEN,
			"ALREADY_SUBMITTED": status.HTTP_409_CONFLICT,
		}
		raise HTTPException(
			status_code=status_map.get(code, status.HTTP_400_BAD_REQUEST),
			detail=_error_detail(code),
		)
	return QuestionsResponse(**result)


@router.post(
	"/{event_id}/join",
	response_model=JoinResponse,
	dependencies=[Depends(require_rate_limit("lc_join"))],
)
async def join_event(
	event_id: str,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> JoinResponse:
	"""Join an event's waiting room."""
	try:
		result = await service.join(event_id, user.sub, user.plan)
	except ValueError as e:
		code = str(e)
		status_map = {
			"EVENT_NOT_JOINABLE": status.HTTP_400_BAD_REQUEST,
			"ALREADY_JOINED": status.HTTP_409_CONFLICT,
			"PLAN_NOT_ELIGIBLE": status.HTTP_403_FORBIDDEN,
			"CAPACITY_FULL": status.HTTP_422_UNPROCESSABLE_ENTITY,
			"NO_EVENT_ACCESS": status.HTTP_403_FORBIDDEN,
			"NO_LATE_JOIN": status.HTTP_400_BAD_REQUEST,
		}
		raise HTTPException(
			status_code=status_map.get(code, status.HTTP_400_BAD_REQUEST),
			detail=_error_detail(code),
		)

	return JoinResponse(
		joined=True,
		event_id=event_id,
		position=result["position"],
		waiting_room_duration=result["waiting_room_duration"],
		countdown_remaining=result["countdown_remaining"],
		ws_url=f"/api/v1/live-challenge/{event_id}/ws",
		mode=result.get("mode", "exam"),
		starting_hearts=result.get("starting_hearts"),
	)


@router.post(
	"/{event_id}/submit",
	response_model=SubmitResponse,
	dependencies=[Depends(require_rate_limit("lc_submit"))],
)
async def submit_answers(
	event_id: str,
	body: SubmitRequest,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> SubmitResponse:
	"""Submit all answers for grading. Returns score immediately."""
	answers = [{"question_idx": a.question_idx, "selected": a.selected} for a in body.answers]

	try:
		result = await service.grade(event_id, user.sub, answers)
	except ValueError as e:
		code = str(e)
		status_map = {
			"EVENT_NOT_ACTIVE": status.HTTP_400_BAD_REQUEST,
			"NOT_A_PARTICIPANT": status.HTTP_403_FORBIDDEN,
			"ALREADY_SUBMITTED": status.HTTP_409_CONFLICT,
			"SUBMISSION_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
			"MODE_NOT_SUPPORTED": status.HTTP_400_BAD_REQUEST,
		}
		raise HTTPException(
			status_code=status_map.get(code, status.HTTP_400_BAD_REQUEST),
			detail=_error_detail(code),
		)

	return SubmitResponse(
		score=result["score"],
		correct_count=result["correct_count"],
		total_questions=result["total_questions"],
		submitted_at=result["submitted_at"],
		corrections=result["corrections"],
	)


@router.post(
	"/{event_id}/answer",
	response_model=AnswerResponse,
	dependencies=[Depends(require_rate_limit("lc_answer"))],
)
async def submit_round_answer(
	event_id: str,
	body: AnswerRequest,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> AnswerResponse:
	"""Submit answer for current round (Last Stand only).

	Atomic validation via Lua script: status, alive, round_id, window, uniqueness.
	Correctness is revealed via WebSocket round_result, not in this response.
	"""
	# Mode gate — only Last Stand events use POST /answer
	mode = await service.redis.get(lc_mode_key(event_id))
	if mode != "last_stand":
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=_error_detail("MODE_NOT_SUPPORTED"),
		)

	# Pre-flight checks (avoid hitting Lua for obviously invalid requests)
	pipe = service.redis.pipeline()
	pipe.get(lc_status_key(event_id))
	pipe.sismember(lc_joined_key(event_id), user.sub)
	event_status, is_joined = await pipe.execute()

	if event_status != "active":
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail=_error_detail("EVENT_NOT_ACTIVE"),
		)
	if not is_joined:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail=_error_detail("NOT_A_PARTICIPANT"),
		)

	# Submit via engine's Lua script
	result = await service.submit_last_stand_answer(
		event_id, user.sub, body.round_id, body.selected,
	)

	if result == 1:
		return AnswerResponse(accepted=True, round_id=body.round_id)

	# Map Lua error codes to HTTP errors
	error_code = ANSWER_ERROR_CODES.get(result, "EVENT_NOT_ACTIVE")
	status_map = {
		"ROUND_MISMATCH": status.HTTP_400_BAD_REQUEST,
		"WINDOW_CLOSED": status.HTTP_400_BAD_REQUEST,
		"ALREADY_ANSWERED": status.HTTP_400_BAD_REQUEST,
		"NOT_ALIVE": status.HTTP_400_BAD_REQUEST,
		"EVENT_NOT_ACTIVE": status.HTTP_409_CONFLICT,
	}
	raise HTTPException(
		status_code=status_map.get(error_code, status.HTTP_400_BAD_REQUEST),
		detail=_error_detail(error_code),
	)


@router.get(
	"/{event_id}/result",
	response_model=ResultResponse,
	dependencies=[Depends(require_rate_limit("lc_read"))],
)
async def get_result(
	event_id: str,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> ResultResponse:
	"""Get the student's own result and rank for a completed event."""
	result = await service.get_result(event_id, user.sub)
	if result is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_error_detail("NO_PARTICIPATION"))
	return ResultResponse(**result)


@router.get(
	"/{event_id}/leaderboard",
	response_model=LeaderboardResponse,
	dependencies=[Depends(require_rate_limit("lc_read"))],
)
async def get_leaderboard(
	event_id: str,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> LeaderboardResponse:
	"""Get top 20 leaderboard after event ends."""
	result = await service.get_leaderboard(event_id, user.sub)
	if result is None:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_error_detail("EVENT_NOT_ENDED"))
	return LeaderboardResponse(**result)


@router.websocket("/{event_id}/ws")
async def live_challenge_ws(
	websocket: WebSocket,
	event_id: str,
	token: str = Query(...),
) -> None:
	"""WebSocket waiting room: countdown updates, exam_start signal, event_ended.

	Auth via JWT query parameter (auth-before-accept pattern).
	During Waiting: periodic countdown messages with remaining seconds + participant_count.
	On Waiting->Active: exam_start with questions (no correct_answer).
	During Active (late join/reconnect): immediate exam_start message.
	On Active->Ended: event_ended message.
	"""
	# Authenticate BEFORE accepting the WebSocket
	try:
		payload = decode_token(token, verify_type="access")
		user_id = payload["sub"]
	except jwt.ExpiredSignatureError:
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
		return
	except jwt.InvalidTokenError:
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
		return
	except Exception:
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
		return

	# Get LiveChallengeService singleton from app state
	service: LiveChallengeService = websocket.app.state.live_challenge_service

	# Check event status + joined in one pipeline (reuse service's Redis client)
	pipe = service.redis.pipeline()
	pipe.get(lc_status_key(event_id))
	pipe.sismember(lc_joined_key(event_id), user_id)
	event_status, is_joined = await pipe.execute()

	if event_status not in ("waiting", "active"):
		await websocket.close(code=4000, reason="EVENT_NOT_JOINABLE")
		return

	# Verify player has joined the event (gate bypass prevention)
	if not is_joined:
		await websocket.close(code=4001, reason="NOT_A_PARTICIPANT")
		return

	# Accept the connection
	await websocket.accept()

	# Register connection for broadcast (with player_id for personalized messages)
	service.register_connection(event_id, websocket, user_id)

	try:
		if event_status == "active":
			# Late join / reconnect: mode-specific state delivery
			mode = await service.redis.get(lc_mode_key(event_id))
			if mode == "last_stand":
				await service.send_player_state_to_client(event_id, websocket, user_id)
			else:
				await service.send_exam_start_to_client(event_id, websocket)

		# Ensure countdown loop is running (idempotent — only starts once)
		service.start_countdown_loop(event_id)

		# Keep connection alive + handle client messages (reactions, disconnect)
		while True:
			raw = await websocket.receive_text()
			try:
				msg = json.loads(raw)
			except (json.JSONDecodeError, ValueError):
				continue  # silently drop malformed JSON
			msg_type = msg.get("type")
			if msg_type == "waiting_room_reaction_tap":
				logger.debug("lc_ws_reaction_tap_received", event_id=event_id, user_id=user_id, reaction=msg.get("reaction", ""))
				await service.handle_reaction_tap(event_id, user_id, msg)
			else:
				logger.info("lc_ws_msg_received", event_id=event_id, user_id=user_id, msg_type=msg_type)
	except WebSocketDisconnect:
		pass
	except Exception:
		pass
	finally:
		service.remove_connection(event_id, websocket)
		logger.debug("lc_ws_disconnected", event_id=event_id, user_id=user_id)
