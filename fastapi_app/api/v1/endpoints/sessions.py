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
	LeaderboardServiceDep,
	RedisClient,
	SettingsServiceDep,
	WalletServiceDep,
)
from fastapi_app.core.constants import DIRTY_WALLETS_KEY
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
	hearts_remaining: int = 0,
	xp_per_heart: int = 0,
) -> int:
	"""Calculate XP to award for completion.

	Per Phase 20:
	- Fresh completion: (lesson_xp or base_xp) + hearts_bonus
	- Hearts bonus: remaining_hearts * xp_per_heart (added before streak multiplier)
	- Replay: fixed replay_xp amount (no hearts bonus)
	- Streak multiplier: +1% per day, capped at max_multiplier_percent
	- Streak multiplier applies to BOTH fresh and replay
	"""
	if is_replay:
		base = replay_xp
	else:
		base = lesson_xp if lesson_xp > 0 else base_xp
		# Hearts bonus: remaining hearts * xp_per_heart
		hearts_bonus = hearts_remaining * xp_per_heart
		base += hearts_bonus

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

	# Check content access (Gate 2) - free lessons bypass, paid lessons require explicit grant
	# Per Phase 3: Free content (is_free at Unit/Topic level) bypasses Gate 2
	# Paid lessons require EXPLICIT GRANT - plan membership covers only free content
	if not hierarchy.is_lesson_free(request.lesson_id):
		# Lesson is NOT free - check subject grant first, then track grant
		has_access = await access_service.check_access(user.sub, f"SUB-{request.subject_id}")
		if not has_access:
			# Fallback: check track-level grant
			lesson_path = hierarchy.find_lesson_path(request.lesson_id)
			if lesson_path:
				has_access = await access_service.check_access(user.sub, f"TRK-{lesson_path.track_id}")
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
	wallet_service: WalletServiceDep,
	leaderboard_service: LeaderboardServiceDep,
	settings_service: SettingsServiceDep,
	redis_client: RedisClient,
) -> EndSessionResponse:
	"""End lesson session and trigger completion flow.

	Optimized hot path (~6-7 Redis round-trips, down from 17+N):
	RT1: HGETALL session
	RT2: GET hierarchy (cache hit)
	RT3: Lua complete_session (DEL + SETBIT + SADD + batch RPUSH)
	RT4: GET settings (cache hit)
	RT5: Lua streak update
	RT6: Pipeline (XP + dirty + stats)
	RT7: Leaderboard updates

	Args:
		request: EndSessionRequest with stage results
		user: Current authenticated user
		game_session_service: Session management service
		hierarchy_service: For lesson info
		wallet_service: For XP and streak
		leaderboard_service: For leaderboard updates
		settings_service: For gamification settings
		redis_client: For pipeline operations

	Returns:
		EndSessionResponse with xp_awarded, is_replay, streak

	Raises:
		403: No active session
		404: Subject or lesson not found
	"""
	# RT1: Get active session
	session = await game_session_service.get_active_session(user.sub)
	if not session:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "NO_ACTIVE_SESSION", "message": "No active lesson session"},
		)

	# RT2: Get hierarchy (cache hit)
	hierarchy = await hierarchy_service.get_hierarchy(session.subject_id)
	if not hierarchy:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
		)

	lesson_info = hierarchy.find_lesson(session.lesson_id)
	if not lesson_info:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "LESSON_NOT_FOUND", "message": "Lesson not found"},
		)

	# Prepare interaction JSONs (batch, not N individual pushes)
	# Per Phase 27-02: when stage.items is populated, produce one interaction per item
	# with item_id for FSRS item-level tracking. Legacy stage-level when items is empty.
	interaction_jsons = []
	for stage in request.stages:
		if stage.items:
			# Per-item results: one interaction per item
			for item in stage.items:
				interaction = {
					"player": user.sub,
					"lesson": session.lesson_id,
					"stage_id": stage.stage_id,
					"item_id": item.item_id,
					"event_type": "Completed",
					"time_spent": stage.time_spent,
					"errors_count": item.fail_count,
					"timestamp": stage.completed_at,
					"metadata": stage.metadata,
				}
				interaction_jsons.append(json.dumps(interaction))
		else:
			# Legacy stage-level result (backward compat)
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
			interaction_jsons.append(json.dumps(interaction))

	# RT3: Lua script -- atomic session delete + SETBIT + SADD + batch RPUSH
	lua_success, is_replay, _ = await game_session_service.complete_session(
		user_id=user.sub,
		bit_index=lesson_info.bit_index,
		subject_id=session.subject_id,
		version=hierarchy.version,
		interaction_jsons=interaction_jsons,
	)
	if not lua_success:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "NO_ACTIVE_SESSION", "message": "Session already ended"},
		)

	# RT4: Get settings (cache hit)
	settings = await settings_service.get_gamification_settings()

	# RT5: Streak update (Lua script)
	streak, streak_updated = await wallet_service.update_streak(
		player_id=user.sub,
		is_replay=is_replay,
	)

	# Calculate hearts remaining
	total_fails = sum(stage.fail_count for stage in request.stages)
	hearts_remaining = max(0, lesson_info.max_hearts - total_fails)

	# Calculate XP with hearts bonus
	xp_awarded = _calculate_xp_award(
		base_xp=settings.base_lesson_xp,
		lesson_xp=lesson_info.xp,
		current_streak=streak,
		max_multiplier_percent=settings.max_streak_multiplier_percent,
		is_replay=is_replay,
		replay_xp=settings.replay_xp,
		hearts_remaining=hearts_remaining,
		xp_per_heart=settings.xp_per_heart,
	)

	# Ensure wallet is hydrated from MariaDB before pipeline HINCRBY.
	# Without this, a Redis flush causes HINCRBY to start from 0, resetting XP.
	await wallet_service.ensure_hydrated(user.sub)

	# RT6: Pipeline for XP + dirty + stats
	pipe = redis_client.pipeline()

	# XP award
	wallet_key = f"memora:wallet:{user.sub}"
	pipe.hincrby(wallet_key, "xp", xp_awarded)

	# Dirty wallet
	pipe.sadd(DIRTY_WALLETS_KEY, user.sub)

	# Stats (non-replay only)
	stats_updated = False
	if not is_replay:
		lesson_path = hierarchy.find_lesson_path(session.lesson_id)
		if lesson_path:
			stats_key = f"memora:stats:{user.sub}:{session.subject_id}:v{hierarchy.version}"
			pipe.hincrby(stats_key, "completed", 1)
			pipe.hincrby(stats_key, f"{lesson_path.track_id}:completed", 1)
			pipe.hincrby(stats_key, f"{lesson_path.unit_id}:completed", 1)
			pipe.hincrby(stats_key, f"{lesson_path.topic_id}:completed", 1)
			pipe.expire(stats_key, 3600)
			stats_updated = True

	pipe_results = await pipe.execute()
	new_total_xp = pipe_results[0]  # HINCRBY returns new value

	# RT7: Leaderboard updates
	await leaderboard_service.update_leaderboards(
		player_id=user.sub,
		xp_amount=xp_awarded,
		new_total_xp=new_total_xp,
		subject_id=session.subject_id,
	)

	logger.info(
		"session_ended",
		user_id=user.sub,
		session_id=session.session_id,
		lesson_id=session.lesson_id,
		subject_id=session.subject_id,
		is_replay=is_replay,
		xp_awarded=xp_awarded,
		hearts_remaining=hearts_remaining,
		new_total_xp=new_total_xp,
		streak=streak,
		streak_updated=streak_updated,
		stages_count=len(request.stages),
		stats_updated=stats_updated,
	)

	return EndSessionResponse(
		success=True,
		xp_awarded=xp_awarded,
		is_replay=is_replay,
		streak=streak,
	)
