"""Live Challenge endpoints — join, submit, result, leaderboard, WebSocket."""

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from fastapi_app.api.deps import CurrentUser, LiveChallengeServiceDep, require_rate_limit
from fastapi_app.core.redis_keys import lc_joined_key, lc_status_key
from fastapi_app.core.security import decode_token
from fastapi_app.models.live_challenge import (
	EventDetailResponse,
	JoinResponse,
	LeaderboardResponse,
	ResultResponse,
	SubmitRequest,
	SubmitResponse,
)
from fastapi_app.services.live_challenge import LiveChallengeService

logger = structlog.get_logger()

router = APIRouter(prefix="/live-challenge", tags=["live-challenge"])


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event_detail(
	event_id: str,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> EventDetailResponse:
	"""Get public event details with player-specific flags."""
	detail = await service.get_event_detail(event_id, user.sub)
	if detail is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="EVENT_NOT_FOUND")
	return EventDetailResponse(**detail)


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
		result = await service.join(event_id, user.sub)
	except ValueError as e:
		code = str(e)
		status_map = {
			"EVENT_NOT_JOINABLE": status.HTTP_400_BAD_REQUEST,
			"ALREADY_JOINED": status.HTTP_409_CONFLICT,
			"PLAN_NOT_ELIGIBLE": status.HTTP_403_FORBIDDEN,
			"CAPACITY_FULL": status.HTTP_422_UNPROCESSABLE_ENTITY,
		}
		raise HTTPException(
			status_code=status_map.get(code, status.HTTP_400_BAD_REQUEST),
			detail=code,
		)

	return JoinResponse(
		joined=True,
		event_id=event_id,
		position=result["position"],
		waiting_room_duration=result["waiting_room_duration"],
		countdown_remaining=result["countdown_remaining"],
		ws_url=f"/api/v1/live-challenge/{event_id}/ws?token=",
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
		}
		raise HTTPException(
			status_code=status_map.get(code, status.HTTP_400_BAD_REQUEST),
			detail=code,
		)

	return SubmitResponse(
		score=result["score"],
		correct_count=result["correct_count"],
		total_questions=result["total_questions"],
		submitted_at=result["submitted_at"],
		corrections=result["corrections"],
	)


@router.get("/{event_id}/result", response_model=ResultResponse)
async def get_result(
	event_id: str,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> ResultResponse:
	"""Get the student's own result and rank for a completed event."""
	result = await service.get_result(event_id, user.sub)
	if result is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NO_PARTICIPATION")
	return ResultResponse(**result)


@router.get("/{event_id}/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(
	event_id: str,
	user: CurrentUser,
	service: LiveChallengeServiceDep,
) -> LeaderboardResponse:
	"""Get top 20 leaderboard after event ends."""
	result = await service.get_leaderboard(event_id, user.sub)
	if result is None:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="EVENT_NOT_ENDED")
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

	# Check event status from Redis
	import redis.asyncio as aioredis

	r = aioredis.Redis(connection_pool=websocket.app.state.redis_pool)
	event_status = await r.get(lc_status_key(event_id))

	if event_status not in ("waiting", "active"):
		await websocket.close(code=4000, reason="EVENT_NOT_JOINABLE")
		return

	# Verify player has joined the event (gate bypass prevention)
	is_joined = await r.sismember(lc_joined_key(event_id), user_id)
	if not is_joined:
		await websocket.close(code=4001, reason="NOT_A_PARTICIPANT")
		return

	# Accept the connection
	await websocket.accept()

	# Register connection for broadcast
	service.register_connection(event_id, websocket)

	try:
		if event_status == "active":
			# Late join / reconnect: send exam_start immediately
			await service.send_exam_start_to_client(event_id, websocket)

		# Ensure countdown loop is running (idempotent — only starts once)
		service.start_countdown_loop(event_id)

		# Keep connection alive: await client messages (detect disconnect)
		while True:
			await websocket.receive_text()
	except WebSocketDisconnect:
		pass
	except Exception:
		pass
	finally:
		service.remove_connection(event_id, websocket)
		logger.debug("lc_ws_disconnected", event_id=event_id, user_id=user_id)
