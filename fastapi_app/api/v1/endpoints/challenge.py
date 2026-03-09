"""Challenge Hub API endpoints."""

import json

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from fastapi_app.api.deps import (
	ActiveSeasonDep,
	ChallengeServiceDep,
	CurrentUser,
	ProfileServiceDep,
	RedisClient,
	require_rate_limit,
)
from fastapi_app.core.redis_keys import (
	CH_IDEM_KEY_TTL,
	ch_idem_key,
)
from fastapi_app.models.challenge import (
	AttemptRequest,
	AttemptResponse,
	ChallengeHierarchyResponse,
	ChallengeSubjectSummary,
	LeaderboardEntry,
	LeaderboardResponse,
	MyRankResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/challenge", tags=["challenge"])


@router.get("/hierarchy", dependencies=[Depends(require_rate_limit("ch_hierarchy"))])
async def get_challenge_hierarchy_list(
	user: CurrentUser,
	_season: ActiveSeasonDep,
	challenge_svc: ChallengeServiceDep,
) -> dict[str, list[ChallengeSubjectSummary]]:
	"""Return subjects available for Challenge Hub with summary stats.

	Shows total topics, stamped topics, and total challenge XP per subject.
	"""
	subjects = await challenge_svc.get_challenge_subjects(
		player_id=user.sub,
		plan_id=user.plan,
		season_id=user.season,
	)
	return {"subjects": subjects}


@router.get("/hierarchy/{subject_id}", dependencies=[Depends(require_rate_limit("ch_hierarchy"))])
async def get_challenge_hierarchy_detail(
	subject_id: str,
	user: CurrentUser,
	_season: ActiveSeasonDep,
	challenge_svc: ChallengeServiceDep,
) -> ChallengeHierarchyResponse:
	"""Return tracks/units/topics for a subject with challenge states.

	Each topic has a state (locked/open/stamped), lock reason if locked,
	and progress data (best score, XP, attempt count).
	Empty topics (mcq_count == 0) are hidden and auto-stamped.
	"""
	result = await challenge_svc.get_challenge_hierarchy(
		player_id=user.sub,
		plan_id=user.plan,
		subject_id=subject_id,
	)
	if result is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not in player's plan or not found"},
		)
	return result


@router.post("/attempt", dependencies=[Depends(require_rate_limit("ch_attempt"))])
async def submit_challenge_attempt(
	body: AttemptRequest,
	user: CurrentUser,
	_season: ActiveSeasonDep,
	challenge_svc: ChallengeServiceDep,
	redis_client: RedisClient,
) -> AttemptResponse:
	"""Submit a completed challenge attempt.

	Idempotent: duplicate attempt_key within 5 minutes returns cached response (409).
	Validates topic is open (access + normal path + predecessor stamped).
	Grades, updates best scores, calculates XP delta, pushes FSRS interactions.
	"""
	# Challenge data is season-scoped — reject tokens without a season.
	# Old JWTs pass the season gate (backward compat), but writing season-less
	# attempts to the buffer causes sync failures (season is reqd in DB).
	if not user.season:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail={"code": "SEASON_REQUIRED", "message": "Season is required for challenge attempts"},
		)

	# Idempotency: atomically claim the key BEFORE processing.
	# SET NX returns True only for the first request; concurrent duplicates fail here.
	idem_key = ch_idem_key(user.sub, body.attempt_key)
	claimed = await redis_client.set(idem_key, "__pending__", ex=CH_IDEM_KEY_TTL, nx=True)

	if not claimed:
		# Key already exists — either processing or completed
		cached = await redis_client.get(idem_key)
		cached_str = cached.decode() if isinstance(cached, bytes) else cached if cached else None
		if cached_str and cached_str != "__pending__":
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail={
					"code": "DUPLICATE_ATTEMPT",
					"message": "Attempt already processed",
					"response": json.loads(cached_str),
				},
			)
		# Still processing by another request — treat as duplicate
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail={"code": "DUPLICATE_ATTEMPT", "message": "Attempt is being processed"},
		)

	# --- Execute attempt (side effects: Redis writes, FSRS push, leaderboard) ---
	# ValueError is raised BEFORE any writes (TOPIC_LOCKED, grading validation),
	# so releasing the lock is safe.  After submit_attempt returns, side effects
	# are committed — we must NOT release the lock on later failures.
	try:
		response = await challenge_svc.submit_attempt(
			player_id=user.sub,
			plan_id=user.plan,
			season_id=user.season,
			subject_id=body.subject_id,
			request=body,
		)
	except ValueError as e:
		await redis_client.delete(idem_key)
		error_msg = str(e)
		if error_msg == "ATTEMPT_IN_PROGRESS":
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail={"code": "ATTEMPT_IN_PROGRESS", "message": "Attempt is currently being processed"},
			)
		if error_msg == "TOPIC_NOT_FOUND":
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail={"code": "TOPIC_NOT_FOUND", "message": "Topic not found in the provided subject"},
			)
		if error_msg == "TOPIC_LOCKED":
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail={"code": "TOPIC_LOCKED", "message": "Topic is locked"},
			)
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail={"code": "VALIDATION_ERROR", "message": error_msg},
		)

	# --- Post-processing: store response for future idempotency lookups ---
	# If this fails, leave __pending__ in place (expires in 5 min).
	# Releasing the lock here would allow a retry to duplicate all side effects.
	try:
		response_json = response.model_dump_json()
		await redis_client.set(idem_key, response_json, ex=CH_IDEM_KEY_TTL)
	except Exception:
		logger.warning("ch_idem_store_failed", idem_key=idem_key)

	return response


@router.get("/leaderboard", dependencies=[Depends(require_rate_limit("ch_leaderboard"))])
async def get_challenge_leaderboard(
	user: CurrentUser,
	_season: ActiveSeasonDep,
	challenge_svc: ChallengeServiceDep,
	profile_service: ProfileServiceDep,
	subject_id: str | None = Query(None, description="Filter by subject"),
	limit: int = Query(20, ge=1, le=100, description="Max entries to return"),
	offset: int = Query(0, ge=0, le=1000, description="Pagination offset"),
) -> LeaderboardResponse:
	"""Return Challenge XP leaderboard for player's plan.

	Plan-scoped with optional subject filter. Dense ranking.
	"""
	plan_id = user.plan
	season_id = user.season

	if not plan_id or not season_id:
		return LeaderboardResponse(subject_id=subject_id, entries=[], total_players=0)

	result = await challenge_svc.get_leaderboard(
		season_id=season_id,
		plan_id=plan_id,
		player_id=user.sub,
		subject_id=subject_id,
		limit=limit,
		offset=offset,
	)

	# Batch fetch profiles
	player_ids = [e["player_id"] for e in result["entries"]]
	profiles = await profile_service.get_profiles_batch(player_ids)

	entries = [
		LeaderboardEntry(
			rank=e["rank"],
			player_id=e["player_id"],
			display_name=profiles[e["player_id"]].display_name,
			xp=e["xp"],
			avatar=profiles[e["player_id"]].avatar,
			is_me=e["player_id"] == user.sub,
		)
		for e in result["entries"]
	]

	return LeaderboardResponse(
		subject_id=subject_id,
		entries=entries,
		total_players=result["total_players"],
	)


@router.get("/leaderboard/me", dependencies=[Depends(require_rate_limit("ch_leaderboard"))])
async def get_challenge_my_rank(
	user: CurrentUser,
	_season: ActiveSeasonDep,
	challenge_svc: ChallengeServiceDep,
	profile_service: ProfileServiceDep,
	subject_id: str | None = Query(None, description="Filter by subject"),
) -> MyRankResponse:
	"""Return player's own Challenge XP rank with neighbors.

	Unranked players (no challenge XP) get rank=null, xp=0.
	"""
	plan_id = user.plan
	season_id = user.season

	if not plan_id or not season_id:
		return MyRankResponse(rank=None, xp=0, xp_to_next=None, neighbors=[], total_players=0)

	result = await challenge_svc.get_my_rank(
		season_id=season_id,
		plan_id=plan_id,
		player_id=user.sub,
		subject_id=subject_id,
	)

	# Batch fetch profiles for neighbors
	player_ids = [n["player_id"] for n in result["neighbors"]]
	profiles = await profile_service.get_profiles_batch(player_ids)

	neighbors = [
		LeaderboardEntry(
			rank=n["rank"],
			player_id=n["player_id"],
			display_name=profiles[n["player_id"]].display_name,
			xp=n["xp"],
			avatar=profiles[n["player_id"]].avatar,
			is_me=n.get("is_me", False),
		)
		for n in result["neighbors"]
	]

	return MyRankResponse(
		rank=result["rank"],
		xp=result["xp"],
		xp_to_next=result["xp_to_next"],
		neighbors=neighbors,
		total_players=result["total_players"],
	)
