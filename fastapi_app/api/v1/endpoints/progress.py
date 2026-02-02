"""Progress tracking endpoints for completion and percentages."""

import structlog
from fastapi import APIRouter, HTTPException, status

from fastapi_app.api.deps import (
    AccessServiceDep,
    CurrentUser,
    HierarchyServiceDep,
    ProgressServiceDep,
    SettingsServiceDep,
    WalletServiceDep,
)
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

    # Check content access (Gate 2 - without full Double-Gate for simplicity)
    # Use subject-level access key
    content_key = f"SUB-{request.subject}"
    has_access = await access_service.check_access(user.sub, content_key)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NO_ACCESS", "message": "Content access required"},
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
    Only includes subjects player has access to.

    Per CONTEXT.md:
    - Returns completion percentages only (not raw bitmaps)
    - Lightweight endpoint for dashboard/overview
    """
    # Get all grants for player
    grants = await access_service.get_player_grants(user.sub)

    # Filter to subject grants (SUB-* pattern)
    subject_keys = [g for g in grants if g.startswith("SUB-")]
    subject_ids = [k.replace("SUB-", "") for k in subject_keys]

    summaries = []
    for subject_id in subject_ids:
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


@router.get("/{subject}", response_model=SubjectProgress)
async def get_subject_progress(
    subject: str,
    user: CurrentUser,
    progress_service: ProgressServiceDep,
    hierarchy_service: HierarchyServiceDep,
    access_service: AccessServiceDep,
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
    """
    # Verify access
    content_key = f"SUB-{subject}"
    has_access = await access_service.check_access(user.sub, content_key)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NO_ACCESS", "message": "Content access required"},
        )

    # Get hierarchy
    hierarchy = await hierarchy_service.get_hierarchy(subject)
    if not hierarchy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
        )

    # Get completed bits
    completed_bits = await progress_service.get_completed_bits(
        user_id=user.sub,
        subject_id=subject,
        bit_range=hierarchy.bit_range,
        version=hierarchy.version,
    )

    # Build response with nested progress
    tracks_progress = []

    for track_idx, track in enumerate(hierarchy.tracks):
        # Check track unlock state
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

                topic_completed = _count_topic_completed(topic, completed_bits)
                topic_total = _count_topic_lessons(topic)

                topics_progress.append(
                    TopicProgress(
                        topic_id=topic.topic_id,
                        completed=topic_completed,
                        total=topic_total,
                        unlocked=topic_unlocked,
                    )
                )

            unit_completed = _count_unit_completed(unit, completed_bits)
            unit_total = _count_unit_lessons(unit)

            units_progress.append(
                UnitProgress(
                    unit_id=unit.unit_id,
                    completed=unit_completed,
                    total=unit_total,
                    topics=topics_progress,
                    unlocked=unit_unlocked,
                )
            )

        track_completed = _count_track_completed(track, completed_bits)
        track_total = _count_track_lessons(track)

        tracks_progress.append(
            TrackProgress(
                track_id=track.track_id,
                completed=track_completed,
                total=track_total,
                units=units_progress,
                unlocked=track_unlocked,
            )
        )

    # Calculate subject totals
    subject_completed = sum(t.completed for t in tracks_progress)
    subject_total = sum(t.total for t in tracks_progress)

    return SubjectProgress(
        subject_id=subject,
        completed=subject_completed,
        total=subject_total,
        tracks=tracks_progress,
    )
