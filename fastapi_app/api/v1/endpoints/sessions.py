# Copyright (c) 2026, corex and contributors
"""Session management endpoints for game lesson flow."""

import asyncio
import json
import random

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status

from fastapi_app.api.deps import (
	AccessServiceDep,
	ActiveSeasonDep,
	CurrentUser,
	GameSessionServiceDep,
	HierarchyServiceDep,
	LeaderboardServiceDep,
	ProgressServiceDep,
	RedisClient,
	SettingsServiceDep,
	StatsServiceDep,
	WalletServiceDep,
	require_rate_limit,
)
from fastapi_app.core.redis_keys import freeze_key, stats_key
from fastapi_app.models.game_session import (
	ActiveSessionInfo,
	CurrentSessionResponse,
	EndSessionRequest,
	EndSessionResponse,
	StartSessionRequest,
	StartSessionResponse,
)
from fastapi_app.services.stats import (
	StatsService,
	compute_stats_from_hierarchy,
	get_stats_recompute_semaphore,
)
from fastapi_app.services.wallet import get_amman_today, get_amman_yesterday

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["sessions"])


async def _get_cached_end_response(
	game_session_service: GameSessionServiceDep,
	user_id: str,
	session_id: str,
) -> EndSessionResponse | None:
	"""Fetch and reuse a cached completion response for duplicate retries."""
	state, payload = await game_session_service.get_end_response_state(user_id, session_id)
	if state != "ready" or payload is None:
		return None

	payload = dict(payload)
	payload["is_duplicate"] = True
	return EndSessionResponse(**payload)


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
	- Returns active=false when no session exists

	Args:
		user: Current authenticated user
		game_session_service: Session management service

	Returns:
		CurrentSessionResponse with session details

	"""
	session = await game_session_service.get_active_session(user.sub)

	if not session:
		return CurrentSessionResponse(active=False, session=None)

	return CurrentSessionResponse(
		active=True,
		session=ActiveSessionInfo(
			session_id=session.session_id,
			lesson_id=session.lesson_id,
			subject_id=session.subject_id,
			device_id=session.device_id,
			started_at=session.started_at,
		),
	)


@router.post("/start", response_model=StartSessionResponse)
async def start_session(
	request: StartSessionRequest,
	user: CurrentUser,
	_season: ActiveSeasonDep,
	game_session_service: GameSessionServiceDep,
	hierarchy_service: HierarchyServiceDep,
	access_service: AccessServiceDep,
	redis_client: RedisClient,
	_rate_limit=Depends(require_rate_limit("session_start")),
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
	# Freeze must short-circuit before any downstream hierarchy/Frappe work.
	if await redis_client.exists(freeze_key(user.sub)):
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={
				"code": "PLAN_CHANGE_IN_PROGRESS",
				"message": "A plan change is in progress. Please try again shortly.",
			},
		)

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

	# Check content access (Gate 2) - free lessons bypass, paid lessons require grant or plan
	# Per Phase 3: Free content (is_free at Unit/Topic level) bypasses Gate 2
	# Non-free lessons require EXPLICIT GRANT or plan membership (subject is free in plan)
	if not hierarchy.is_lesson_free(request.lesson_id):
		# Lesson is NOT free - check subject grant or plan membership, then track grant
		has_access = await access_service.check_access_with_plan(
			user.sub, f"SUB-{request.subject_id}", user.plan
		)
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
	progress_service: ProgressServiceDep,
	stats_service: StatsServiceDep,
	redis_client: RedisClient,
	_rate_limit=Depends(require_rate_limit("session_end")),
) -> EndSessionResponse:
	"""End lesson session and trigger completion flow.

	Optimized hot path (~5-6 Redis round-trips, down from 17+N):
	RT1: HGETALL session
	RT2: GET hierarchy (cache hit)
	RT3: GET settings + ensure wallet hydrated
	RT4: Lua complete_session (DEL + SETBIT + interactions + streak + XP + cached response)
	RT5: Stats update (best effort)
	RT6: Leaderboard updates (best effort)

	Args:
		request: EndSessionRequest with stage results
		user: Current authenticated user
		game_session_service: Session management service
		hierarchy_service: For lesson info
		wallet_service: For wallet hydration
		leaderboard_service: For leaderboard updates
		settings_service: For gamification settings
		redis_client: For stats pipeline operations

	Returns:
		EndSessionResponse with xp_awarded, is_replay, streak

	Raises:
		404: Subject or lesson not found
	"""
	# Check if player is frozen (plan change in progress)
	if await redis_client.exists(freeze_key(user.sub)):
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={
				"code": "PLAN_CHANGE_IN_PROGRESS",
				"message": "A plan change is in progress. Please try again shortly.",
			},
		)

	# RT1: Get active session
	session = await game_session_service.get_active_session(user.sub)
	if not session:
		cached_response = await _get_cached_end_response(game_session_service, user.sub, request.session_id)
		if cached_response:
			return cached_response
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"code": "NO_ACTIVE_SESSION", "message": "No active lesson session"},
		)
	if session.session_id != request.session_id:
		cached_response = await _get_cached_end_response(game_session_service, user.sub, request.session_id)
		if cached_response:
			return cached_response
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={
				"code": "SESSION_MISMATCH",
				"message": "Requested session is no longer active",
				"active_session_id": session.session_id,
			},
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

	# RT3: Get settings + hydrate wallet before the atomic completion.
	settings = await settings_service.get_gamification_settings()
	await wallet_service.ensure_hydrated(user.sub)

	# Calculate hearts remaining before completion so the script can compute XP atomically.
	total_fails = sum(stage.fail_count for stage in request.stages)
	hearts_remaining = max(0, lesson_info.max_hearts - total_fails)

	today = get_amman_today()
	yesterday = get_amman_yesterday()

	# RT4: Atomic session completion + wallet update + cached response.
	completion_status, completion_payload, active_session_id = await game_session_service.complete_session(
		user_id=user.sub,
		session_id=request.session_id,
		bit_index=lesson_info.bit_index,
		subject_id=session.subject_id,
		version=hierarchy.version,
		base_xp=settings.base_lesson_xp,
		lesson_xp=lesson_info.xp,
		replay_xp=settings.replay_xp,
		max_multiplier_percent=settings.max_streak_multiplier_percent,
		hearts_remaining=hearts_remaining,
		xp_per_heart=settings.xp_per_heart,
		today=today,
		yesterday=yesterday,
		interaction_jsons=interaction_jsons,
	)
	if completion_status == "duplicate":
		if completion_payload:
			duplicate_payload = dict(json.loads(completion_payload))
			duplicate_payload["is_duplicate"] = True
			return EndSessionResponse(**duplicate_payload)
		cached_response = await _get_cached_end_response(game_session_service, user.sub, request.session_id)
		if cached_response:
			return cached_response
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"code": "NO_ACTIVE_SESSION", "message": "Session is no longer active"},
		)
	if completion_status == "mismatch":
		cached_response = await _get_cached_end_response(game_session_service, user.sub, request.session_id)
		if cached_response:
			return cached_response
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={
				"code": "SESSION_MISMATCH",
				"message": "Requested session is no longer active",
				"active_session_id": active_session_id,
			},
		)
	if completion_status != "completed":
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"code": "NO_ACTIVE_SESSION", "message": "Session is no longer active"},
		)
	if not completion_payload:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail={"code": "SESSION_END_RESPONSE_MISSING", "message": "Session completion response missing"},
		)

	response = EndSessionResponse(**json.loads(completion_payload))

	# RT5: Stats (best effort, derived from persisted progress state)
	stats_updated = False
	stats_cache_needs_evict = False
	try:
		if not response.is_replay:
			lesson_path = hierarchy.find_lesson_path(session.lesson_id)
			if lesson_path:
				sk = stats_key(user.sub, session.subject_id, hierarchy.version)

				# Check if stats hash exists before deciding how to update.
				# Two paths:
				# 1) Stats hash MISSING (cold start): Compute from bitmap which already
				#    includes the just-completed lesson (SETBIT happened in Lua script above).
				#    Do NOT also HINCRBY -- that would double-count the completion.
				# 2) Stats hash EXISTS: Increment completed counters via HINCRBY.
				stats_exists = await redis_client.exists(sk)
				if not stats_exists:
					sem = get_stats_recompute_semaphore()
					acquired = False
					try:
						await asyncio.wait_for(sem.acquire(), timeout=StatsService.RECOMPUTE_TIMEOUT)
						acquired = True
					except asyncio.TimeoutError:
						logger.warning(
							"stats_recompute_semaphore_timeout",
							user_id=user.sub,
							subject_id=session.subject_id,
							path="end_session_cold_start",
						)

					try:
						completed_bits = await progress_service.get_completed_bits(
							user_id=user.sub,
							subject_id=session.subject_id,
							bit_range=hierarchy.bit_range,
							version=hierarchy.version,
						)
						stats = compute_stats_from_hierarchy(hierarchy, completed_bits)
						await stats_service.set_stats(
							user_id=user.sub,
							subject_id=session.subject_id,
							version=hierarchy.version,
							stats=stats,
						)
					finally:
						if acquired:
							sem.release()
				else:
					pipe = redis_client.pipeline()
					pipe.hincrby(sk, "completed", 1)
					pipe.hincrby(sk, f"{lesson_path.track_id}:completed", 1)
					pipe.hincrby(sk, f"{lesson_path.unit_id}:completed", 1)
					pipe.hincrby(sk, f"{lesson_path.topic_id}:completed", 1)
					pipe.expire(sk, StatsService.CACHE_TTL + random.randint(0, StatsService.JITTER_RANGE))
					await pipe.execute()
					stats_cache_needs_evict = True
				stats_updated = True
	except Exception:
		logger.exception(
			"stats_update_failed_after_session_end",
			user_id=user.sub,
			session_id=session.session_id,
			subject_id=session.subject_id,
		)
	if stats_cache_needs_evict:
		stats_service.evict_local_cache(user.sub, session.subject_id, hierarchy.version)

	# RT6: Leaderboard updates
	try:
		await leaderboard_service.update_leaderboards(
			player_id=user.sub,
			xp_amount=response.xp_awarded,
			subject_id=session.subject_id,
			plan_id=user.plan,
		)
	except Exception:
		logger.exception(
			"leaderboard_update_failed_after_session_end",
			user_id=user.sub,
			session_id=session.session_id,
			subject_id=session.subject_id,
		)

	logger.info(
		"session_ended",
		user_id=user.sub,
		session_id=session.session_id,
		lesson_id=session.lesson_id,
		subject_id=session.subject_id,
		is_replay=response.is_replay,
		xp_awarded=response.xp_awarded,
		hearts_remaining=response.hearts_remaining,
		new_total_xp=response.new_total_xp,
		streak=response.streak,
		stages_count=len(request.stages),
		stats_updated=stats_updated,
	)

	return response
