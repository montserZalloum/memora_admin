# Copyright (c) 2026, corex and contributors
"""Session management endpoints for game lesson flow."""

import json

import structlog
from fastapi import APIRouter, Header, HTTPException, status

from fastapi_app.api.deps import (
	AccessServiceDep,
	CurrentUser,
	GameSessionServiceDep,
	HierarchyServiceDep,
	ProgressServiceDep,
	RedisClient,
	SettingsServiceDep,
	WalletServiceDep,
)
from fastapi_app.core.constants import INTERACTION_BUFFER_KEY
from fastapi_app.models.game_session import (
	CurrentSessionResponse,
	EndSessionRequest,
	EndSessionResponse,
	StartSessionRequest,
	StartSessionResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _calculate_xp_award(
	base_xp: int,
	lesson_xp: int,
	current_streak: int,
	max_multiplier_percent: int,
	is_replay: bool,
	replay_xp: int,
) -> int:
	"""Calculate XP to award for completion.

	Per CONTEXT.md:
	- Fresh completion: lesson_xp (if > 0) else base_xp
	- Replay: fixed replay_xp amount
	- Streak multiplier: +1% per day, capped at max_multiplier_percent
	- Streak multiplier applies to BOTH fresh and replay per CONTEXT.md
	"""
	if is_replay:
		base = replay_xp
	else:
		base = lesson_xp if lesson_xp > 0 else base_xp

	# Apply streak multiplier (linear +1% per day, capped)
	capped_streak = min(current_streak, max_multiplier_percent)
	multiplier = 1.0 + (capped_streak * 0.01)

	# Floor the result per RESEARCH.md recommendation
	return int(base * multiplier)


@router.get("/current", response_model=CurrentSessionResponse)
async def get_current_session(
	user: CurrentUser,
	game_session_service: GameSessionServiceDep,
) -> CurrentSessionResponse:
	"""
	Get current active session for user.

	Per VERIFICATION gap closure:
	- Enables session recovery after app crash
	- Client can check if session exists before restarting lesson
	- Returns 404 if no active session

	Args:
		user: Current authenticated user
		game_session_service: Session management service

	Returns:
		CurrentSessionResponse with session details

	Raises:
		404: No active session
	"""
	session = await game_session_service.get_active_session(user.sub)

	if not session:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "NO_ACTIVE_SESSION", "message": "No active session"},
		)

	return CurrentSessionResponse(
		session_id=session.session_id,
		lesson_id=session.lesson_id,
		subject_id=session.subject_id,
		device_id=session.device_id,
		started_at=session.started_at,
	)


@router.post("/start", response_model=StartSessionResponse)
async def start_session(
	request: StartSessionRequest,
	user: CurrentUser,
	game_session_service: GameSessionServiceDep,
	hierarchy_service: HierarchyServiceDep,
	access_service: AccessServiceDep,
	x_device_id: str | None = Header(None, alias="X-Device-ID"),
) -> StartSessionResponse:
	"""
	Start a new lesson session.

	Per CONTEXT.md:
	- Creates session for specified lesson
	- Force-closes any existing session (no error)
	- Validates subject, lesson, and access

	Args:
		request: StartSessionRequest with lesson_id and subject_id
		user: Current authenticated user
		game_session_service: Session management service
		hierarchy_service: For lesson validation
		access_service: For access control
		x_device_id: Optional device identifier from header

	Returns:
		StartSessionResponse with session_id and lesson_id

	Raises:
		404: Subject or lesson not found
		403: No content access
	"""
	# Validate subject exists
	hierarchy = await hierarchy_service.get_hierarchy(request.subject_id)
	if not hierarchy:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
		)

	# Validate lesson exists in hierarchy
	lesson_info = hierarchy.find_lesson(request.lesson_id)
	if not lesson_info:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "LESSON_NOT_FOUND", "message": "Lesson not found"},
		)

	# Check content access (Gate 2)
	content_key = f"SUB-{request.subject_id}"
	has_access = await access_service.check_access(user.sub, content_key)
	if not has_access:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "NO_ACCESS", "message": "Content access required"},
		)

	# Start session (force-closes any existing)
	session_id = await game_session_service.start_session(
		user_id=user.sub,
		lesson_id=request.lesson_id,
		subject_id=request.subject_id,
		device_id=x_device_id,
	)

	logger.info(
		"session_started",
		user_id=user.sub,
		lesson_id=request.lesson_id,
		subject_id=request.subject_id,
		session_id=session_id,
	)

	return StartSessionResponse(
		session_id=session_id,
		lesson_id=request.lesson_id,
	)


@router.post("/end", response_model=EndSessionResponse)
async def end_session(
	request: EndSessionRequest,
	user: CurrentUser,
	game_session_service: GameSessionServiceDep,
	hierarchy_service: HierarchyServiceDep,
	progress_service: ProgressServiceDep,
	wallet_service: WalletServiceDep,
	settings_service: SettingsServiceDep,
	redis_client: RedisClient,
) -> EndSessionResponse:
	"""
	End current lesson session and trigger completion flow.

	Per CONTEXT.md:
	- Validates active session exists
	- Logs stage analytics to interaction buffer
	- Marks lesson complete (idempotent)
	- Updates streak and awards XP

	Args:
		request: EndSessionRequest with stage results
		user: Current authenticated user
		game_session_service: Session management service
		hierarchy_service: For lesson info
		progress_service: For completion tracking
		wallet_service: For XP and streak
		settings_service: For gamification settings
		redis_client: For interaction buffer

	Returns:
		EndSessionResponse with xp_awarded, is_replay, streak

	Raises:
		403: No active session
	"""
	# Get active session
	session = await game_session_service.get_active_session(user.sub)
	if not session:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "NO_ACTIVE_SESSION", "message": "No active lesson session"},
		)

	# Get hierarchy for lesson info
	hierarchy = await hierarchy_service.get_hierarchy(session.subject_id)
	if not hierarchy:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
		)

	# Find lesson to get bit_index and xp
	lesson_info = hierarchy.find_lesson(session.lesson_id)
	if not lesson_info:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "LESSON_NOT_FOUND", "message": "Lesson not found"},
		)

	# Push stage analytics to interaction buffer
	for stage in request.stages:
		interaction = {
			"player": user.sub,
			"lesson": session.lesson_id,
			"stage_id": stage.stage_id,
			"event_type": "Completed",
			"time_spent": stage.time_spent,
			"errors_count": stage.fail_count,
			"timestamp": stage.completed_at,
			"metadata": stage.metadata,
		}
		await redis_client.rpush(INTERACTION_BUFFER_KEY, json.dumps(interaction))

	# End session
	await game_session_service.end_session(user.sub)

	# Mark lesson complete (idempotent)
	is_replay = await progress_service.complete_lesson(
		user_id=user.sub,
		subject_id=session.subject_id,
		bit_index=lesson_info.bit_index,
		version=hierarchy.version,
	)

	# Get gamification settings (cached)
	settings = await settings_service.get_gamification_settings()

	# Update streak atomically (replay doesn't count per CONTEXT.md)
	streak, streak_updated = await wallet_service.update_streak(
		player_id=user.sub,
		is_replay=is_replay,
	)

	# Calculate XP with streak multiplier
	xp_awarded = _calculate_xp_award(
		base_xp=settings.base_lesson_xp,
		lesson_xp=lesson_info.xp,
		current_streak=streak,
		max_multiplier_percent=settings.max_streak_multiplier_percent,
		is_replay=is_replay,
		replay_xp=settings.replay_xp,
	)

	# Award XP atomically
	new_total_xp = await wallet_service.award_xp(user.sub, xp_awarded)

	logger.info(
		"session_ended",
		user_id=user.sub,
		session_id=session.session_id,
		lesson_id=session.lesson_id,
		subject_id=session.subject_id,
		is_replay=is_replay,
		xp_awarded=xp_awarded,
		new_total_xp=new_total_xp,
		streak=streak,
		streak_updated=streak_updated,
		stages_count=len(request.stages),
	)

	return EndSessionResponse(
		success=True,
		xp_awarded=xp_awarded,
		is_replay=is_replay,
		streak=streak,
	)
