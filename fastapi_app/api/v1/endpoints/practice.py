"""Practice Arena endpoints.

POST /start  — Start a new practice session (Phase 3)
POST /submit — Submit answers for current batch (Phase 4)
POST /continue — Get next batch (Phase 5)
GET  /session — Session status (Phase 7)
"""

import json

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from fastapi_app.api.deps import (
    AccessServiceDep,
    CurrentUser,
    RedisClient,
    SettingsDep,
    get_frappe_client,
)
from fastapi_app.core.redis_keys import subjects_with_free_content_key
from fastapi_app.models.practice import (
    BatchResponse,
    ContinueRequest,
    SessionStatusResponse,
    StartSessionRequest,
    SubmitRequest,
    SubmitResponse,
)
from fastapi_app.services import practice_map, practice
from fastapi_app.services.practice import RateLimitExceeded

logger = structlog.get_logger()

router = APIRouter(prefix="/practice", tags=["practice"])


@router.post("/start", response_model=BatchResponse)
async def start_session(
    body: StartSessionRequest,
    user: CurrentUser,
    redis_client: RedisClient,
    settings: SettingsDep,
    access_service: AccessServiceDep,
) -> BatchResponse:
    """Start a new practice session."""
    # --- Scope validation (FR-035, FR-036) ---
    if len(body.track_ids) > 1 and (body.unit_ids or body.topic_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot filter by units or topics when multiple tracks are selected",
        )
    if body.unit_ids and len(body.unit_ids) > 1 and body.topic_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot filter by topics when multiple units are selected",
        )

    player_id = user.sub

    # --- Access control check ---
    has_subject = await access_service.check_access_with_plan(
        player_id=player_id,
        content_key=f"SUB-{body.subject_id}",
        plan_id=user.plan,
    )
    if not has_subject:
        # Check individual track-level grants
        for track_id in body.track_ids:
            has_track = await access_service.check_access(player_id, f"TRK-{track_id}")
            if not has_track:
                # Last resort: check if subject has free content
                is_free = await redis_client.sismember(
                    subjects_with_free_content_key(), body.subject_id,
                )
                if not is_free:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="No access to this content",
                    )
                break  # Free content — allow all tracks

    # --- Rate limit check (FR-010, T021) ---
    try:
        await practice.check_rate_limit(redis_client, player_id)
    except RateLimitExceeded as exc:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Maximum 5 sessions per hour exceeded"},
            headers={"Retry-After": str(exc.retry_after)},
        )

    # --- Load map file ---
    if not settings.practice_maps_dir:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Practice maps directory not configured",
        )
    try:
        map_data = practice_map.get_map(body.subject_id, maps_dir=settings.practice_maps_dir)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown subject_id: {body.subject_id}",
        )

    # --- Validate all IDs exist in map ---
    try:
        practice.validate_scope(map_data, body.track_ids, body.unit_ids, body.topic_ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # --- Load player summary per track ---
    frappe_client = await get_frappe_client()
    combined_history: dict = {}
    for track_id in body.track_ids:
        history = await practice.get_player_summary(
            redis_client, player_id, track_id, frappe_client,
        )
        combined_history.update(history)

    # --- Select questions ---
    question_ids, chunk_refs, total_available, all_seen_warning, question_track_map = (
        practice.select_questions(
            map_data,
            body.track_ids,
            body.unit_ids,
            body.topic_ids,
            combined_history,
            set(),  # No served_ids on first batch
            settings.practice_session_size,
        )
    )

    if not question_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No questions available for the selected scope",
        )

    # --- Create session ---
    scope_hash = practice.compute_scope_hash(
        body.subject_id, body.track_ids, body.unit_ids, body.topic_ids,
    )
    await practice.create_session(
        redis_client,
        player_id,
        body.subject_id,
        body.track_ids,
        scope_hash,
        question_ids,
        chunk_refs,
        unit_ids=body.unit_ids,
        topic_ids=body.topic_ids,
        question_track_map=question_track_map,
    )

    return BatchResponse(
        session_active=True,
        batch_seq=0,
        question_ids=question_ids,
        chunk_refs=chunk_refs,
        total_available=total_available,
        all_seen_warning=all_seen_warning,
    )


@router.post("/submit", response_model=SubmitResponse)
async def submit_results(
    body: SubmitRequest,
    user: CurrentUser,
    redis_client: RedisClient,
) -> SubmitResponse:
    """Submit answers for the current batch."""
    player_id = user.sub

    # --- Concurrency guard (distributed lock) ---
    lock = redis_client.lock(
        f"memora:practice:submit:{player_id}", timeout=10,
    )
    acquired = await lock.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Submission already in progress",
        )
    try:
        # --- Read session (404 if none) ---
        session = await practice.get_session(redis_client, player_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active practice session",
            )

        # --- Convert results to dicts for service layer ---
        results_dicts = [{"item_id": r.item_id, "is_correct": r.is_correct} for r in body.results]

        # --- Validate submission ---
        try:
            practice.validate_submission(session, body.batch_seq, results_dicts)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

        # --- Process submission ---
        result = await practice.submit_results(
            redis_client, player_id, session, body.batch_seq, results_dicts,
        )

        return SubmitResponse(**result)
    finally:
        try:
            await lock.release()
        except Exception:
            logger.warning("practice_submit_lock_release_failed", player_id=player_id)


@router.post("/continue", response_model=BatchResponse)
async def continue_session(
    body: ContinueRequest,
    user: CurrentUser,
    redis_client: RedisClient,
    settings: SettingsDep,
) -> BatchResponse:
    """Request the next batch of questions after submitting the current batch."""
    player_id = user.sub

    # --- Read session (404 if none) ---
    session = await practice.get_session(redis_client, player_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active practice session",
        )

    # --- Validate batch_seq matches session ---
    current_batch_seq = int(session["batch_seq"])
    if body.batch_seq != current_batch_seq:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"batch_seq {body.batch_seq} does not match current batch {current_batch_seq}",
        )

    # --- Verify current batch is submitted (FR-023) ---
    if session.get("submitted") != "1":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"batch_seq {body.batch_seq} has not been submitted yet",
        )

    # --- Load map data ---
    if not settings.practice_maps_dir:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Practice maps directory not configured",
        )
    subject_id = session["subject_id"]
    try:
        map_data = practice_map.get_map(subject_id, maps_dir=settings.practice_maps_dir)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Map file not found for subject: {subject_id}",
        )

    # --- Continue session (next batch) ---
    try:
        result = await practice.continue_session(
            redis_client,
            player_id,
            session,
            map_data,
            settings.practice_session_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return BatchResponse(**result)


@router.get("/session", response_model=SessionStatusResponse)
async def get_session_status(
    user: CurrentUser,
    redis_client: RedisClient,
) -> SessionStatusResponse:
    """Get current session status."""
    player_id = user.sub

    session = await practice.get_session(redis_client, player_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active practice session",
        )

    return SessionStatusResponse(
        session_active=True,
        subject_id=session["subject_id"],
        track_ids=json.loads(session["track_ids"]),
        batch_seq=int(session["batch_seq"]),
        submitted=session["submitted"] == "1",
        question_ids=json.loads(session["current_batch"]),
        chunk_refs=json.loads(session["chunk_refs"]) if session.get("chunk_refs") else [],
    )
