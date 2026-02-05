"""Progress tracking endpoints for completion and percentages."""

import json

import structlog
from fastapi import APIRouter, HTTPException, status
from starlette.requests import Request
from sse_starlette import EventSourceResponse

from fastapi_app.api.deps import (
    AccessServiceDep,
    CurrentUser,
    GameSessionServiceDep,
    HierarchyServiceDep,
    ProgressServiceDep,
    SettingsServiceDep,
    StatsServiceDep,
    WalletServiceDep,
)
from fastapi_app.services.stats import compute_stats_from_hierarchy
from fastapi_app.models.progress import (
    CompleteRequest,
    CompleteResponse,
    SubjectHierarchy,
    SubjectProgress,
    SubjectSummary,
    TopicInfo,
    TopicProgress,
    TrackInfo,
    TrackProgress,
    UnitInfo,
    UnitProgress,
)
from fastapi_app.services.unlock import is_lesson_unlocked

logger = structlog.get_logger()

router = APIRouter(prefix="/progress", tags=["progress"])


# --- XP Calculation ---


def calculate_xp_award(
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


# --- Completion Endpoint ---


@router.post("/complete", response_model=CompleteResponse)
async def complete_lesson(
    request: CompleteRequest,
    user: CurrentUser,
    progress_service: ProgressServiceDep,
    hierarchy_service: HierarchyServiceDep,
    access_service: AccessServiceDep,
    wallet_service: WalletServiceDep,
    settings_service: SettingsServiceDep,
    game_session_service: GameSessionServiceDep,
) -> CompleteResponse:
    """
    Mark a lesson as complete.

    Per CONTEXT.md:
    - Enforces unlock state: 403 if lesson is locked
    - Returns minimal response: { success: true }
    - Idempotent: re-completing returns 200 OK

    Pre-requisites (checked by dependencies):
    - Valid JWT token (CurrentUser)
    - Player has content access checked here (access grant)
    """
    # Get hierarchy for validation
    hierarchy = await hierarchy_service.get_hierarchy(request.subject)
    if not hierarchy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
        )

    # Find lesson in hierarchy
    lesson_info = hierarchy.find_lesson(request.lesson)
    if not lesson_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "LESSON_NOT_FOUND", "message": "Lesson not found"},
        )

    # Check content access (Three-level access control)
    # Level 2 & 3: Check if lesson is in free unit/topic
    is_free_content = hierarchy.is_lesson_free(request.lesson)

    if not is_free_content:
        # Level 1: Check explicit grant OR plan membership
        content_key = f"SUB-{request.subject}"
        has_access = await access_service.check_access_with_plan(
            user.sub, content_key, user.plan
        )
        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NO_ACCESS", "message": "Content access required"},
            )

    # Check active session (enforces session-based flow per VERIFICATION gap)
    has_session = await game_session_service.has_active_session(user.sub)
    if not has_session:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NO_ACTIVE_SESSION", "message": "Active session required"},
        )

    # Get completed bits for unlock calculation
    completed_bits = await progress_service.get_completed_bits(
        user_id=user.sub,
        subject_id=request.subject,
        bit_range=hierarchy.bit_range,
        version=hierarchy.version,
    )

    # Check unlock state
    unlocked = is_lesson_unlocked(request.lesson, hierarchy, completed_bits)
    if not unlocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "LESSON_LOCKED", "message": "Lesson is locked"},
        )

    # Mark complete (idempotent)
    is_replay = await progress_service.complete_lesson(
        user_id=user.sub,
        subject_id=request.subject,
        bit_index=lesson_info.bit_index,
        version=hierarchy.version,
    )

    # --- Wallet integration (Phase 5) ---

    # Get gamification settings (cached)
    settings = await settings_service.get_gamification_settings()

    # Update streak atomically (replay doesn't count per CONTEXT.md)
    streak, streak_updated = await wallet_service.update_streak(
        player_id=user.sub,
        is_replay=is_replay,
    )

    # Calculate XP with streak multiplier
    xp_awarded = calculate_xp_award(
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
        "lesson_completed",
        user_id=user.sub,
        subject=request.subject,
        lesson=request.lesson,
        bit_index=lesson_info.bit_index,
        is_replay=is_replay,
        xp_awarded=xp_awarded,
        new_total_xp=new_total_xp,
        streak=streak,
        streak_updated=streak_updated,
    )

    return CompleteResponse(
        success=True,
        xp_awarded=xp_awarded,
        is_replay=is_replay,
        streak=streak,
    )


# --- Helper functions for counting lessons ---


def _count_topic_lessons(topic: TopicInfo) -> int:
    """Count total lessons in topic."""
    return len(topic.lessons)


def _count_topic_completed(topic: TopicInfo, completed_bits: set[int]) -> int:
    """Count completed lessons in topic."""
    return sum(1 for lesson in topic.lessons if lesson.bit_index in completed_bits)


def _count_unit_lessons(unit: UnitInfo) -> int:
    """Count total lessons in unit."""
    return sum(_count_topic_lessons(t) for t in unit.topics)


def _count_unit_completed(unit: UnitInfo, completed_bits: set[int]) -> int:
    """Count completed lessons in unit."""
    return sum(_count_topic_completed(t, completed_bits) for t in unit.topics)


def _count_track_lessons(track: TrackInfo) -> int:
    """Count total lessons in track."""
    return sum(_count_unit_lessons(u) for u in track.units)


def _count_track_completed(track: TrackInfo, completed_bits: set[int]) -> int:
    """Count completed lessons in track."""
    return sum(_count_unit_completed(u, completed_bits) for u in track.units)


# --- Unlock state helper functions ---


def _is_track_complete(track: TrackInfo, completed_bits: set[int]) -> bool:
    """Check if all lessons in track are complete."""
    for unit in track.units:
        for topic in unit.topics:
            for lesson in topic.lessons:
                if lesson.bit_index not in completed_bits:
                    return False
    return True


def _is_unit_complete(unit: UnitInfo, completed_bits: set[int]) -> bool:
    """Check if all lessons in unit are complete."""
    for topic in unit.topics:
        for lesson in topic.lessons:
            if lesson.bit_index not in completed_bits:
                return False
    return True


def _is_topic_complete(topic: TopicInfo, completed_bits: set[int]) -> bool:
    """Check if all lessons in topic are complete."""
    for lesson in topic.lessons:
        if lesson.bit_index not in completed_bits:
            return False
    return True


def _is_unit_unlocked(
    track_idx: int,
    unit_idx: int,
    hierarchy: SubjectHierarchy,
    completed_bits: set[int],
) -> bool:
    """Check if unit is unlocked based on track/unit position."""
    # Track must be unlocked
    if track_idx > 0 and hierarchy.is_linear:
        prev_track = hierarchy.tracks[track_idx - 1]
        if not _is_track_complete(prev_track, completed_bits):
            return False

    # First unit in track is always unlocked
    if unit_idx == 0:
        return True

    # Check if previous unit is complete (if track is linear)
    track = hierarchy.tracks[track_idx]
    if track.is_linear:
        prev_unit = track.units[unit_idx - 1]
        return _is_unit_complete(prev_unit, completed_bits)

    return True


def _is_topic_unlocked(
    track_idx: int,
    unit_idx: int,
    topic_idx: int,
    hierarchy: SubjectHierarchy,
    completed_bits: set[int],
) -> bool:
    """Check if topic is unlocked based on position."""
    # Unit must be unlocked
    if not _is_unit_unlocked(track_idx, unit_idx, hierarchy, completed_bits):
        return False

    # First topic in unit is always unlocked
    if topic_idx == 0:
        return True

    # Check if previous topic is complete (if unit is linear)
    unit = hierarchy.tracks[track_idx].units[unit_idx]
    if unit.is_linear:
        prev_topic = unit.topics[topic_idx - 1]
        return _is_topic_complete(prev_topic, completed_bits)

    return True


# --- Endpoints ---


@router.get("/", response_model=list[SubjectSummary])
async def get_progress_summary(
    user: CurrentUser,
    progress_service: ProgressServiceDep,
    access_service: AccessServiceDep,
    hierarchy_service: HierarchyServiceDep,
) -> list[SubjectSummary]:
    """
    Get progress summary for all player's subjects.

    Returns list of subjects with completion percentages.
    Includes subjects accessible via:
    1. Explicit grants (Memora Player Subscription)
    2. Plan membership (subjects with is_premium=0 in player's plan)
    3. Subjects with free content (units/topics with is_free=True)

    Per CONTEXT.md:
    - Returns completion percentages only (not raw bitmaps)
    - Lightweight endpoint for dashboard/overview
    """
    # Get explicit grants for player
    grants = await access_service.get_player_grants(user.sub)
    granted_subjects = {g.replace("SUB-", "") for g in grants if g.startswith("SUB-")}

    # Get subjects from player's plan (those with is_premium=0)
    plan_subjects = set(await access_service.get_plan_free_subjects(user.plan))

    # Get subjects with free content (units/topics with is_free=True)
    subjects_with_free = set(await hierarchy_service.get_subjects_with_free_content())

    # Combine all accessible subjects (deduplicated via set union)
    all_accessible = granted_subjects | plan_subjects | subjects_with_free

    summaries = []
    for subject_id in all_accessible:
        # Get hierarchy to calculate total
        hierarchy = await hierarchy_service.get_hierarchy(subject_id)
        if not hierarchy:
            continue

        # Count completed lessons
        total = hierarchy.bit_range - len(hierarchy.excluded_bits)
        completed = await progress_service.get_completed_count(
            user_id=user.sub,
            subject_id=subject_id,
            version=hierarchy.version,
        )

        # Calculate percentage
        percentage = round(completed / total * 100, 1) if total > 0 else 0.0

        summaries.append(
            SubjectSummary(
                subject_id=subject_id,
                subject_name=subject_id,  # TODO: fetch from Frappe
                percentage=percentage,
                completed=completed,
                total=total,
            )
        )

    return summaries


# --- SSE Streaming Endpoint ---
# NOTE: Must come BEFORE /{subject} route (more specific routes first)


@router.get("/stream/{subject}")
async def stream_subject_progress(
    subject: str,
    request: Request,
    user: CurrentUser,
    stats_service: StatsServiceDep,
    hierarchy_service: HierarchyServiceDep,
    access_service: AccessServiceDep,
    progress_service: ProgressServiceDep,
) -> EventSourceResponse:
    """
    Stream progress data via Server-Sent Events.

    Per Phase 17 requirements:
    - First data chunk (subject summary) within 10ms
    - Track details stream progressively
    - 'complete' event signals end of stream

    Events emitted:
    - subject: {subject_id, completed, total, percentage}
    - track: {track_id, completed, total, units: [...]}
    - complete: signals end of stream

    Args:
        subject: Subject identifier
        request: Starlette request for disconnect detection
        user: Current authenticated user
        stats_service: For cached stats
        hierarchy_service: For subject structure
        access_service: For access validation
        progress_service: For cold start initialization

    Returns:
        EventSourceResponse streaming progress data
    """
    # Validate subject exists
    hierarchy = await hierarchy_service.get_hierarchy(subject)
    if not hierarchy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
        )

    # Verify access (same as REST endpoint)
    content_key = f"SUB-{subject}"
    has_access = await access_service.check_access_with_plan(user.sub, content_key, user.plan)
    if not has_access:
        if not hierarchy.has_any_free_content():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NO_ACCESS", "message": "Content access required"},
            )

    async def event_generator():
        # Get or initialize stats (same logic as REST endpoint)
        stats = await stats_service.get_stats(
            user_id=user.sub,
            subject_id=subject,
            version=hierarchy.version,
        )

        if stats is None:
            # Cold start: compute from bitmap
            completed_bits = await progress_service.get_completed_bits(
                user_id=user.sub,
                subject_id=subject,
                bit_range=hierarchy.bit_range,
                version=hierarchy.version,
            )
            stats = compute_stats_from_hierarchy(hierarchy, completed_bits)
            await stats_service.set_stats(
                user_id=user.sub,
                subject_id=subject,
                version=hierarchy.version,
                stats=stats,
            )

        # First event: subject summary (within 10ms target)
        subject_completed = int(stats.get("completed", "0"))
        subject_total = int(stats.get("total", "0"))
        percentage = round(subject_completed / subject_total * 100, 1) if subject_total > 0 else 0.0

        yield {
            "event": "subject",
            "data": json.dumps({
                "subject_id": subject,
                "completed": subject_completed,
                "total": subject_total,
                "percentage": percentage,
            }),
        }

        # Stream tracks progressively
        for track in hierarchy.tracks:
            # Check for client disconnect
            if await request.is_disconnected():
                break

            track_completed = int(stats.get(f"{track.track_id}:completed", "0"))
            track_total = int(stats.get(f"{track.track_id}:total", "0"))

            # Build units for this track
            units_data = []
            for unit in track.units:
                unit_completed = int(stats.get(f"{unit.unit_id}:completed", "0"))
                unit_total = int(stats.get(f"{unit.unit_id}:total", "0"))

                # Build topics for this unit
                topics_data = []
                for topic in unit.topics:
                    topic_completed = int(stats.get(f"{topic.topic_id}:completed", "0"))
                    topic_total = int(stats.get(f"{topic.topic_id}:total", "0"))
                    topics_data.append({
                        "topic_id": topic.topic_id,
                        "completed": topic_completed,
                        "total": topic_total,
                        "percentage": round(topic_completed / topic_total * 100, 1) if topic_total > 0 else 0.0,
                    })

                units_data.append({
                    "unit_id": unit.unit_id,
                    "completed": unit_completed,
                    "total": unit_total,
                    "percentage": round(unit_completed / unit_total * 100, 1) if unit_total > 0 else 0.0,
                    "topics": topics_data,
                })

            yield {
                "event": "track",
                "data": json.dumps({
                    "track_id": track.track_id,
                    "completed": track_completed,
                    "total": track_total,
                    "percentage": round(track_completed / track_total * 100, 1) if track_total > 0 else 0.0,
                    "units": units_data,
                }),
            }

        # Final event signals completion
        yield {"event": "complete", "data": ""}

    return EventSourceResponse(
        event_generator(),
        headers={"X-Accel-Buffering": "no"},  # Disable nginx buffering
    )


@router.get("/{subject}", response_model=SubjectProgress)
async def get_subject_progress(
    subject: str,
    user: CurrentUser,
    progress_service: ProgressServiceDep,
    hierarchy_service: HierarchyServiceDep,
    access_service: AccessServiceDep,
    stats_service: StatsServiceDep,
) -> SubjectProgress:
    """
    Get detailed progress breakdown for a subject.

    Returns nested structure with percentages at each level:
    - Subject total
    - Track breakdown
    - Unit breakdown
    - Topic breakdown

    Per CONTEXT.md:
    - Full breakdown: subject + tracks + units + topics
    - Includes unlock state at each level
    - Percentages only (not raw data)

    Per Phase 17 optimization:
    - Stats read from Redis hash cache (O(1) HGETALL)
    - Cold start lazily initializes cache from bitmap
    - Unlock states still computed from completed_bits (O(1) set membership)
    """
    # Get hierarchy first (needed for both access check and progress calc)
    hierarchy = await hierarchy_service.get_hierarchy(subject)
    if not hierarchy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
        )

    # Verify access (three-level check)
    content_key = f"SUB-{subject}"
    has_access = await access_service.check_access_with_plan(
        user.sub, content_key, user.plan
    )
    if not has_access:
        # Still allow if subject has free content (units/topics with is_free=True)
        if not hierarchy.has_any_free_content():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NO_ACCESS", "message": "Content access required"},
            )

    # Try to get cached stats (O(1) read)
    stats = await stats_service.get_stats(
        user_id=user.sub,
        subject_id=subject,
        version=hierarchy.version,
    )

    # Always need completed_bits for unlock state calculation
    completed_bits = await progress_service.get_completed_bits(
        user_id=user.sub,
        subject_id=subject,
        bit_range=hierarchy.bit_range,
        version=hierarchy.version,
    )

    if stats is None:
        # Cold start: compute stats from bitmap and cache
        stats = compute_stats_from_hierarchy(hierarchy, completed_bits)
        await stats_service.set_stats(
            user_id=user.sub,
            subject_id=subject,
            version=hierarchy.version,
            stats=stats,
        )

    # Build response with nested progress using cached stats
    tracks_progress = []

    for track_idx, track in enumerate(hierarchy.tracks):
        # Check track unlock state (still uses completed_bits)
        track_unlocked = track_idx == 0 or not hierarchy.is_linear
        if track_idx > 0 and hierarchy.is_linear:
            prev_track = hierarchy.tracks[track_idx - 1]
            track_unlocked = _is_track_complete(prev_track, completed_bits)

        units_progress = []
        for unit_idx, unit in enumerate(track.units):
            unit_unlocked = _is_unit_unlocked(
                track_idx, unit_idx, hierarchy, completed_bits
            )

            topics_progress = []
            for topic_idx, topic in enumerate(unit.topics):
                topic_unlocked = _is_topic_unlocked(
                    track_idx, unit_idx, topic_idx, hierarchy, completed_bits
                )

                # Read counts from cached stats (O(1) dict access)
                topic_completed = int(stats.get(f"{topic.topic_id}:completed", "0"))
                topic_total = int(stats.get(f"{topic.topic_id}:total", "0"))

                topics_progress.append(
                    TopicProgress(
                        topic_id=topic.topic_id,
                        completed=topic_completed,
                        total=topic_total,
                        unlocked=topic_unlocked,
                    )
                )

            # Read counts from cached stats
            unit_completed = int(stats.get(f"{unit.unit_id}:completed", "0"))
            unit_total = int(stats.get(f"{unit.unit_id}:total", "0"))

            units_progress.append(
                UnitProgress(
                    unit_id=unit.unit_id,
                    completed=unit_completed,
                    total=unit_total,
                    topics=topics_progress,
                    unlocked=unit_unlocked,
                )
            )

        # Read counts from cached stats
        track_completed = int(stats.get(f"{track.track_id}:completed", "0"))
        track_total = int(stats.get(f"{track.track_id}:total", "0"))

        tracks_progress.append(
            TrackProgress(
                track_id=track.track_id,
                completed=track_completed,
                total=track_total,
                units=units_progress,
                unlocked=track_unlocked,
            )
        )

    # Read subject totals from cached stats
    subject_completed = int(stats.get("completed", "0"))
    subject_total = int(stats.get("total", "0"))

    return SubjectProgress(
        subject_id=subject,
        completed=subject_completed,
        total=subject_total,
        tracks=tracks_progress,
    )
