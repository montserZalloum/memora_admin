"""Progress tracking endpoints for completion and percentages."""

import asyncio

import structlog
from fastapi import APIRouter, HTTPException, status

from fastapi_app.api.deps import (
	AccessServiceDep,
	CurrentUser,
	HierarchyServiceDep,
	ProgressServiceDep,
	StatsServiceDep,
)
from fastapi_app.core.redis_keys import progress_key
from fastapi_app.models.progress import (
	LessonCompletionStatus,
	SubjectHierarchy,
	SubjectProgress,
	SubjectSummary,
	TopicInfo,
	TopicLessonsResponse,
	TopicProgress,
	TrackDetail,
	TrackInfo,
	TrackProgress,
	TrackSummary,
	UnitDetail,
	UnitInfo,
	UnitProgress,
	UnitSummary,
)

logger = structlog.get_logger()

PROGRESS_SUMMARY_CONCURRENCY = 6

router = APIRouter(prefix="/progress", tags=["progress"])


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


def _is_entity_complete_from_stats(entity_id: str, stats: dict[str, str]) -> bool:
	"""Check if an entity (track/unit/topic) is complete using stats dict.

	Uses pre-computed completed/total counts from the stats hash instead of
	iterating over bitmap bits. Equivalent to _is_track_complete / _is_unit_complete /
	_is_topic_complete but O(1) dict lookups instead of O(L) bitmap iteration.
	"""
	total_raw = stats.get(f"{entity_id}:total")
	if total_raw is None:
		return False
	total = int(total_raw)
	if total == 0:
		# Empty containers are considered complete, matching the bitmap helpers.
		return True
	completed_raw = stats.get(f"{entity_id}:completed")
	if completed_raw is None:
		return False
	completed = int(completed_raw)
	return completed >= total


def _is_unit_unlocked_from_stats(
	track_idx: int,
	unit_idx: int,
	hierarchy: SubjectHierarchy,
	stats: dict[str, str],
) -> bool:
	"""Check if unit is unlocked using stats-derived completion checks.

	Mirror of _is_unit_unlocked but reads completed/total from stats dict
	instead of iterating bitmap bits.
	"""
	# Track must be unlocked
	if track_idx > 0 and hierarchy.is_linear:
		prev_track = hierarchy.tracks[track_idx - 1]
		if not _is_entity_complete_from_stats(prev_track.track_id, stats):
			return False

	# First unit in track is always unlocked
	if unit_idx == 0:
		return True

	# Check if previous unit is complete (if track is linear)
	track = hierarchy.tracks[track_idx]
	if track.is_linear:
		prev_unit = track.units[unit_idx - 1]
		return _is_entity_complete_from_stats(prev_unit.unit_id, stats)

	return True


def _is_topic_unlocked_from_stats(
	track_idx: int,
	unit_idx: int,
	topic_idx: int,
	hierarchy: SubjectHierarchy,
	stats: dict[str, str],
) -> bool:
	"""Check if topic is unlocked using stats-derived completion checks.

	Mirror of _is_topic_unlocked but reads completed/total from stats dict
	instead of iterating bitmap bits.
	"""
	# Unit must be unlocked
	if not _is_unit_unlocked_from_stats(track_idx, unit_idx, hierarchy, stats):
		return False

	# First topic in unit is always unlocked
	if topic_idx == 0:
		return True

	# Check if previous topic is complete (if unit is linear)
	unit = hierarchy.tracks[track_idx].units[unit_idx]
	if unit.is_linear:
		prev_topic = unit.topics[topic_idx - 1]
		return _is_entity_complete_from_stats(prev_topic.topic_id, stats)

	return True


def _stats_are_valid(stats: dict[str, str] | None, content_hash: str) -> bool:
	"""Check if stats dict is valid and fresh enough to use for stats-first path.

	Returns True only if stats is present, has the "total" key (indicating a
	fully-computed hash), and its _content_hash matches the current hierarchy's
	content_hash. A mismatch means the hierarchy structure changed and stats
	are stale — caller should fall back to bitmap path.
	"""
	if stats is None:
		return False
	if "total" not in stats:
		return False
	return stats.get("_content_hash") == content_hash


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


def _find_topic_in_hierarchy(hierarchy: SubjectHierarchy, topic_id: str) -> TopicInfo | None:
	"""Find topic by ID within hierarchy.

	O(T * U * To) worst case, but typically <100 iterations total.
	Used by get_topic_lessons endpoint for topic lookup.

	Args:
	    hierarchy: Subject hierarchy structure
	    topic_id: Topic identifier to find

	Returns:
	    TopicInfo if found, None otherwise
	"""
	for track in hierarchy.tracks:
		for unit in track.units:
			for topic in unit.topics:
				if topic.topic_id == topic_id:
					return topic
	return None


# --- Endpoints ---


@router.get("/", response_model=list[SubjectSummary])
async def get_progress_summary(
	user: CurrentUser,
	progress_service: ProgressServiceDep,
	access_service: AccessServiceDep,
	hierarchy_service: HierarchyServiceDep,
	stats_service: StatsServiceDep,
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
	# Fetch all 3 sources in parallel (on cache hit = 0 RTTs, on miss = 1 parallel RTT)
	grants, plan_subjects, subjects_with_free_list = await asyncio.gather(
		access_service.get_player_grants(user.sub),
		access_service.get_plan_free_subjects(user.plan),
		hierarchy_service.get_subjects_with_free_content(),
	)
	granted_subjects = {g.replace("SUB-", "") for g in grants if g.startswith("SUB-")}
	subjects_with_free = set(subjects_with_free_list)

	# Combine all accessible subjects (deduplicated via set union)
	all_accessible = granted_subjects | plan_subjects | subjects_with_free

	async def _fetch_subject_summary(subject_id: str) -> SubjectSummary | None:
		hierarchy = await hierarchy_service.get_hierarchy(subject_id)
		if not hierarchy:
			return None

		# Stats-first: try reading completed/total from stats cache
		stats = await stats_service.get_stats(user.sub, subject_id, hierarchy.version)
		if _stats_are_valid(stats, hierarchy.content_hash):
			total = int(stats.get("total", "0"))
			completed = int(stats.get("completed", "0"))
			completed = min(completed, total)
		else:
			# Fallback: BITCOUNT path
			total = hierarchy.bit_range - len(hierarchy.excluded_bits)
			completed = await progress_service.get_completed_count(
				user_id=user.sub,
				subject_id=subject_id,
				version=hierarchy.version,
			)
			# Clamp completed to total (BITCOUNT may exceed total if bitmap has stale bits)
			completed = min(completed, total)

		percentage = round(completed / total * 100, 1) if total > 0 else 0.0

		return SubjectSummary(
			subject_id=subject_id,
			subject_name=subject_id,  # TODO: fetch from Frappe
			percentage=percentage,
			completed=completed,
			total=total,
		)

	sem = asyncio.Semaphore(PROGRESS_SUMMARY_CONCURRENCY)

	async def _bounded_fetch(subject_id: str) -> SubjectSummary | None:
		async with sem:
			return await _fetch_subject_summary(subject_id)

	logger.debug(
		"progress_summary_bounded",
		subject_count=len(all_accessible),
		concurrency=PROGRESS_SUMMARY_CONCURRENCY,
	)
	results = await asyncio.gather(
		*(_bounded_fetch(sid) for sid in all_accessible),
		return_exceptions=True,
	)

	summaries = []
	for subject_id, result in zip(all_accessible, results, strict=True):
		if isinstance(result, Exception):
			logger.warning("subject_summary_failed", subject_id=subject_id, error=str(result))
			continue
		if result is not None:
			summaries.append(result)

	return summaries


# --- Granular Endpoints (Phase 17.2) ---
# NOTE: Must come BEFORE /{subject} route (more specific paths first)


@router.get("/{subject}/tracks", response_model=list[TrackSummary])
async def get_subject_tracks(
	subject: str,
	user: CurrentUser,
	hierarchy_service: HierarchyServiceDep,
	access_service: AccessServiceDep,
	stats_service: StatsServiceDep,
	progress_service: ProgressServiceDep,
) -> list[TrackSummary]:
	"""
	Get all tracks for a subject with their progress.

	Returns track summaries without nested units/topics.
	Frontend can lazy-load units by calling /{subject}/tracks/{track_id}.

	Performance: O(T) where T = number of tracks (typically 5-10)
	"""
	# Get hierarchy (validate subject exists)
	hierarchy = await hierarchy_service.get_hierarchy(subject)
	if not hierarchy:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
		)

	# Parallelize access check + partial stats read (independent after hierarchy)
	content_key = f"SUB-{subject}"
	fields = ["_content_hash"]
	for t in hierarchy.tracks:
		fields.append(f"{t.track_id}:completed")
		fields.append(f"{t.track_id}:total")

	has_access, partial_stats = await asyncio.gather(
		access_service.check_access_with_plan(user.sub, content_key, user.plan),
		stats_service.get_partial_stats(user.sub, subject, hierarchy.version, fields),
	)
	if not has_access:
		if not hierarchy.has_any_free_content():
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail={"code": "NO_ACCESS", "message": "Content access required"},
			)

	use_stats_path = (
		partial_stats is not None and partial_stats.get("_content_hash") == hierarchy.content_hash
	)

	if use_stats_path:
		logger.debug("stats_partial_read", endpoint="get_subject_tracks", subject=subject, fields=len(fields))
		stats = partial_stats
	else:
		# Fallback: bitmap decode + full recompute
		completed_bits = await progress_service.get_completed_bits(
			user.sub, subject, hierarchy.bit_range, hierarchy.version
		)
		stats = await stats_service.get_or_recompute(
			user_id=user.sub, subject_id=subject, version=hierarchy.version,
			content_hash=hierarchy.content_hash, completed_bits=completed_bits,
			hierarchy=hierarchy,
		)

	# Build track summaries
	tracks_summary = []
	for track_idx, track in enumerate(hierarchy.tracks):
		if use_stats_path:
			track_unlocked = track_idx == 0 or not hierarchy.is_linear
			if track_idx > 0 and hierarchy.is_linear:
				prev_track = hierarchy.tracks[track_idx - 1]
				track_unlocked = _is_entity_complete_from_stats(prev_track.track_id, stats)
		else:
			track_unlocked = track_idx == 0 or not hierarchy.is_linear
			if track_idx > 0 and hierarchy.is_linear:
				prev_track = hierarchy.tracks[track_idx - 1]
				track_unlocked = _is_track_complete(prev_track, completed_bits)

		# Read from cached stats
		track_completed = int(stats.get(f"{track.track_id}:completed", "0"))
		track_total = int(stats.get(f"{track.track_id}:total", "0"))

		tracks_summary.append(
			TrackSummary(
				track_id=track.track_id,
				completed=track_completed,
				total=track_total,
				unlocked=track_unlocked,
			)
		)

	return tracks_summary


@router.get("/{subject}/tracks/{track_id}", response_model=TrackDetail)
async def get_track_detail(
	subject: str,
	track_id: str,
	user: CurrentUser,
	hierarchy_service: HierarchyServiceDep,
	access_service: AccessServiceDep,
	stats_service: StatsServiceDep,
	progress_service: ProgressServiceDep,
) -> TrackDetail:
	"""
	Get detailed progress for a specific track with its units.

	Returns track with unit summaries (without topics).
	Frontend can lazy-load topics by calling /{subject}/tracks/{track_id}/units/{unit_id}.

	Performance: O(U) where U = units in track (typically 5-20)
	"""
	# Get hierarchy
	hierarchy = await hierarchy_service.get_hierarchy(subject)
	if not hierarchy:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
		)

	# Find track in hierarchy
	track_info = None
	track_idx = None
	for idx, track in enumerate(hierarchy.tracks):
		if track.track_id == track_id:
			track_info = track
			track_idx = idx
			break

	if track_info is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "TRACK_NOT_FOUND", "message": "Track not found"},
		)

	# Parallelize access check + partial stats read (independent after hierarchy)
	content_key = f"SUB-{subject}"
	fields = ["_content_hash", f"{track_id}:completed", f"{track_id}:total"]
	if track_idx > 0 and hierarchy.is_linear:
		prev_track = hierarchy.tracks[track_idx - 1]
		fields.extend([f"{prev_track.track_id}:completed", f"{prev_track.track_id}:total"])
	for u in track_info.units:
		fields.append(f"{u.unit_id}:completed")
		fields.append(f"{u.unit_id}:total")

	has_access, partial_stats = await asyncio.gather(
		access_service.check_access_with_plan(user.sub, content_key, user.plan),
		stats_service.get_partial_stats(user.sub, subject, hierarchy.version, fields),
	)
	if not has_access:
		if not hierarchy.has_any_free_content():
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail={"code": "NO_ACCESS", "message": "Content access required"},
			)

	use_stats_path = (
		partial_stats is not None and partial_stats.get("_content_hash") == hierarchy.content_hash
	)

	if use_stats_path:
		logger.debug("stats_partial_read", endpoint="get_track_detail", subject=subject, fields=len(fields))
		stats = partial_stats
	else:
		# Fallback: bitmap decode + full recompute
		completed_bits = await progress_service.get_completed_bits(
			user.sub, subject, hierarchy.bit_range, hierarchy.version
		)
		stats = await stats_service.get_or_recompute(
			user_id=user.sub, subject_id=subject, version=hierarchy.version,
			content_hash=hierarchy.content_hash, completed_bits=completed_bits,
			hierarchy=hierarchy,
		)

	# Check track unlock state
	if use_stats_path:
		track_unlocked = track_idx == 0 or not hierarchy.is_linear
		if track_idx > 0 and hierarchy.is_linear:
			prev_track = hierarchy.tracks[track_idx - 1]
			track_unlocked = _is_entity_complete_from_stats(prev_track.track_id, stats)
	else:
		track_unlocked = track_idx == 0 or not hierarchy.is_linear
		if track_idx > 0 and hierarchy.is_linear:
			prev_track = hierarchy.tracks[track_idx - 1]
			track_unlocked = _is_track_complete(prev_track, completed_bits)

	# Read track stats
	track_completed = int(stats.get(f"{track_id}:completed", "0"))
	track_total = int(stats.get(f"{track_id}:total", "0"))

	# Build unit summaries
	units_summary = []
	for unit_idx, unit in enumerate(track_info.units):
		if use_stats_path:
			unit_unlocked = _is_unit_unlocked_from_stats(track_idx, unit_idx, hierarchy, stats)
		else:
			unit_unlocked = _is_unit_unlocked(track_idx, unit_idx, hierarchy, completed_bits)

		unit_completed = int(stats.get(f"{unit.unit_id}:completed", "0"))
		unit_total = int(stats.get(f"{unit.unit_id}:total", "0"))

		units_summary.append(
			UnitSummary(
				unit_id=unit.unit_id,
				completed=unit_completed,
				total=unit_total,
				unlocked=unit_unlocked,
			)
		)

	return TrackDetail(
		track_id=track_id,
		completed=track_completed,
		total=track_total,
		unlocked=track_unlocked,
		units=units_summary,
	)


@router.get("/{subject}/tracks/{track_id}/units/{unit_id}", response_model=UnitDetail)
async def get_unit_detail(
	subject: str,
	track_id: str,
	unit_id: str,
	user: CurrentUser,
	hierarchy_service: HierarchyServiceDep,
	access_service: AccessServiceDep,
	stats_service: StatsServiceDep,
	progress_service: ProgressServiceDep,
) -> UnitDetail:
	"""
	Get detailed progress for a specific unit with its topics.

	Returns unit with topic progress (without lessons).

	Performance: O(To) where To = topics in unit (typically 5-10)
	"""
	# Get hierarchy
	hierarchy = await hierarchy_service.get_hierarchy(subject)
	if not hierarchy:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
		)

	# Find track and unit in hierarchy
	track_info = None
	track_idx = None
	unit_info = None
	unit_idx = None

	for t_idx, track in enumerate(hierarchy.tracks):
		if track.track_id == track_id:
			track_info = track
			track_idx = t_idx
			for u_idx, unit in enumerate(track.units):
				if unit.unit_id == unit_id:
					unit_info = unit
					unit_idx = u_idx
					break
			break

	if unit_info is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "UNIT_NOT_FOUND", "message": "Unit not found"},
		)

	# Parallelize access check + partial stats read (independent after hierarchy)
	content_key = f"SUB-{subject}"
	fields = ["_content_hash", f"{unit_id}:completed", f"{unit_id}:total"]
	if unit_idx > 0 and track_info.is_linear:
		prev_unit = track_info.units[unit_idx - 1]
		fields.extend([f"{prev_unit.unit_id}:completed", f"{prev_unit.unit_id}:total"])
	if track_idx > 0 and hierarchy.is_linear:
		prev_track = hierarchy.tracks[track_idx - 1]
		fields.extend([f"{prev_track.track_id}:completed", f"{prev_track.track_id}:total"])
	for topic in unit_info.topics:
		fields.append(f"{topic.topic_id}:completed")
		fields.append(f"{topic.topic_id}:total")

	has_access, partial_stats = await asyncio.gather(
		access_service.check_access_with_plan(user.sub, content_key, user.plan),
		stats_service.get_partial_stats(user.sub, subject, hierarchy.version, fields),
	)
	if not has_access:
		if not hierarchy.has_any_free_content():
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail={"code": "NO_ACCESS", "message": "Content access required"},
			)

	use_stats_path = (
		partial_stats is not None and partial_stats.get("_content_hash") == hierarchy.content_hash
	)

	if use_stats_path:
		logger.debug("stats_partial_read", endpoint="get_unit_detail", subject=subject, fields=len(fields))
		stats = partial_stats
	else:
		# Fallback: bitmap decode + full recompute
		completed_bits = await progress_service.get_completed_bits(
			user.sub, subject, hierarchy.bit_range, hierarchy.version
		)
		stats = await stats_service.get_or_recompute(
			user_id=user.sub, subject_id=subject, version=hierarchy.version,
			content_hash=hierarchy.content_hash, completed_bits=completed_bits,
			hierarchy=hierarchy,
		)

	# Check unit unlock state
	if use_stats_path:
		unit_unlocked = _is_unit_unlocked_from_stats(track_idx, unit_idx, hierarchy, stats)
	else:
		unit_unlocked = _is_unit_unlocked(track_idx, unit_idx, hierarchy, completed_bits)

	# Read unit stats
	unit_completed = int(stats.get(f"{unit_id}:completed", "0"))
	unit_total = int(stats.get(f"{unit_id}:total", "0"))

	# Build topic progress (reuse existing TopicProgress model)
	topics_progress = []
	for topic_idx, topic in enumerate(unit_info.topics):
		if use_stats_path:
			topic_unlocked = _is_topic_unlocked_from_stats(
				track_idx, unit_idx, topic_idx, hierarchy, stats
			)
		else:
			topic_unlocked = _is_topic_unlocked(track_idx, unit_idx, topic_idx, hierarchy, completed_bits)

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

	return UnitDetail(
		unit_id=unit_id,
		completed=unit_completed,
		total=unit_total,
		unlocked=unit_unlocked,
		topics=topics_progress,
	)


@router.get("/{subject}/topics/{topic_id}/lessons", response_model=TopicLessonsResponse)
async def get_topic_lessons(
	subject: str,
	topic_id: str,
	user: CurrentUser,
	hierarchy_service: HierarchyServiceDep,
	access_service: AccessServiceDep,
	progress_service: ProgressServiceDep,
) -> TopicLessonsResponse:
	"""
	Get completion status for all lessons in a topic.

	Returns lesson_id, bit_index, and completed boolean for each lesson.
	Uses pipeline GETBIT for <5ms response regardless of lesson count.

	Performance:
	- Hierarchy fetch: O(1) from Redis cache
	- Topic lookup: O(T*U*To) but <1ms for typical subjects
	- GETBIT pipeline: O(L) in single round-trip, ~1ms

	Args:
	    subject: Subject identifier
	    topic_id: Topic identifier
	    user: Current authenticated user
	    hierarchy_service: For subject structure
	    access_service: For access validation
	    progress_service: For Redis bitmap access

	Returns:
	    TopicLessonsResponse with completion status for each lesson
	"""
	# Get cached hierarchy
	hierarchy = await hierarchy_service.get_hierarchy(subject)
	if not hierarchy:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
		)

	# Find topic in hierarchy
	topic = _find_topic_in_hierarchy(hierarchy, topic_id)
	if not topic:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail={"code": "TOPIC_NOT_FOUND", "message": "Topic not found"},
		)

	# Parallelize access check + bitmap hydration (independent)
	content_key = f"SUB-{subject}"
	has_access, _ = await asyncio.gather(
		access_service.check_access_with_plan(user.sub, content_key, user.plan),
		progress_service.ensure_hydrated(user.sub, subject, hierarchy.version),
	)
	if not has_access:
		if not hierarchy.has_any_free_content():
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail={"code": "NO_ACCESS", "message": "Content access required"},
			)

	# Get completion status for all lessons via pipeline GETBIT
	lessons_status = []
	completed_count = 0

	if topic.lessons:
		# Use pipeline for batch GETBIT (single round-trip, ~1ms for 100 lessons)
		key = progress_key(user.sub, subject, hierarchy.version)
		pipe = progress_service.redis.pipeline()
		for lesson in topic.lessons:
			pipe.getbit(key, lesson.bit_index)
		results = await pipe.execute()

		for lesson, is_completed in zip(topic.lessons, results):
			completed = bool(is_completed)
			if completed:
				completed_count += 1
			lessons_status.append(
				LessonCompletionStatus(
					lesson_id=lesson.lesson_id,
					bit_index=lesson.bit_index,
					completed=completed,
				)
			)

	return TopicLessonsResponse(
		topic_id=topic_id,
		total=len(topic.lessons),
		completed=completed_count,
		lessons=lessons_status,
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

	# Parallelize access check + stats read (independent after hierarchy)
	content_key = f"SUB-{subject}"
	has_access, stats = await asyncio.gather(
		access_service.check_access_with_plan(user.sub, content_key, user.plan),
		stats_service.get_stats(user.sub, subject, hierarchy.version),
	)
	if not has_access:
		if not hierarchy.has_any_free_content():
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail={"code": "NO_ACCESS", "message": "Content access required"},
			)

	use_stats_path = _stats_are_valid(stats, hierarchy.content_hash)

	if use_stats_path:
		logger.debug("stats_cache_hit", endpoint="get_subject_progress", subject=subject)
	else:
		logger.debug("stats_cache_miss", endpoint="get_subject_progress", subject=subject)
		# Fallback: decode bitmap and recompute stats
		completed_bits = await progress_service.get_completed_bits(
			user_id=user.sub,
			subject_id=subject,
			bit_range=hierarchy.bit_range,
			version=hierarchy.version,
		)
		stats = await stats_service.get_or_recompute(
			user_id=user.sub, subject_id=subject, version=hierarchy.version,
			content_hash=hierarchy.content_hash, completed_bits=completed_bits,
			hierarchy=hierarchy,
		)

	# Build response with nested progress using cached stats
	tracks_progress = []

	for track_idx, track in enumerate(hierarchy.tracks):
		if use_stats_path:
			track_unlocked = track_idx == 0 or not hierarchy.is_linear
			if track_idx > 0 and hierarchy.is_linear:
				prev_track = hierarchy.tracks[track_idx - 1]
				track_unlocked = _is_entity_complete_from_stats(prev_track.track_id, stats)
		else:
			track_unlocked = track_idx == 0 or not hierarchy.is_linear
			if track_idx > 0 and hierarchy.is_linear:
				prev_track = hierarchy.tracks[track_idx - 1]
				track_unlocked = _is_track_complete(prev_track, completed_bits)

		units_progress = []
		for unit_idx, unit in enumerate(track.units):
			if use_stats_path:
				unit_unlocked = _is_unit_unlocked_from_stats(track_idx, unit_idx, hierarchy, stats)
			else:
				unit_unlocked = _is_unit_unlocked(track_idx, unit_idx, hierarchy, completed_bits)

			topics_progress = []
			for topic_idx, topic in enumerate(unit.topics):
				if use_stats_path:
					topic_unlocked = _is_topic_unlocked_from_stats(
						track_idx, unit_idx, topic_idx, hierarchy, stats
					)
				else:
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
