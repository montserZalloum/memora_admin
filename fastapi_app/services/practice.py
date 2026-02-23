"""Practice Arena service for hierarchy browsing and session management."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog

from fastapi_app.core.config import Settings
from fastapi_app.core.redis_keys import practice_hierarchy_meta_key, practice_session_key
from fastapi_app.models.practice import (
	PracticeBatchResponse,
	PracticeHierarchyResponse,
	PracticeQuestion,
	PracticeSubmitResponse,
	PracticeTopicInfo,
	PracticeTrackInfo,
	PracticeUnitInfo,
)
from fastapi_app.models.progress import SubjectHierarchy

if TYPE_CHECKING:
	from fastapi_app.services.access import AccessService
	from fastapi_app.services.frappe_client import FrappeClient
	from fastapi_app.services.hierarchy import HierarchyService
	from fastapi_app.services.progress import ProgressService

logger = structlog.get_logger()

META_CACHE_TTL = 3600  # 1 hour — same as hierarchy cache


class PracticeAccessDenied(Exception):
	"""Raised when player has no access to one or more selected tracks."""

	def __init__(self, denied_tracks: list[str]):
		self.denied_tracks = denied_tracks
		super().__init__(f"No access to tracks: {denied_tracks}")


class NoItemsError(Exception):
	"""Raised when filters produce zero reviewable items."""


class NoActiveSessionError(Exception):
	"""Raised when no active practice session exists for the player."""


class BatchSeqMismatchError(Exception):
	"""Raised when submitted batch_seq doesn't match expected."""

	def __init__(self, expected: int, received: int):
		self.expected = expected
		self.received = received
		super().__init__(f"Expected batch_seq {expected}, got {received}")


class PreviousBatchNotSubmittedError(Exception):
	"""Raised when trying to continue but previous batch wasn't submitted."""

	def __init__(self, batch_seq: int):
		self.batch_seq = batch_seq
		super().__init__(f"Batch {batch_seq} not yet submitted")


class PracticeService:
	"""Practice Arena business logic.

	Handles hierarchy browsing with item counts, access flags,
	and completed-only filtering.
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient | None,
		config: Settings,
		hierarchy_service: HierarchyService,
		access_service: AccessService,
		progress_service: ProgressService,
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self.config = config
		self.hierarchy = hierarchy_service
		self.access = access_service
		self.progress = progress_service

	# =========================================================================
	# Hierarchy Browsing (US2)
	# =========================================================================

	async def get_practice_hierarchy(
		self,
		player_id: str,
		subject_id: str,
		plan_id: str | None,
		filter_mode: str = "all",
	) -> PracticeHierarchyResponse | None:
		"""Build practice hierarchy with titles, item counts, and access flags.

		Flow:
		1. Load SubjectHierarchy from cache (structure + free content)
		2. Load practice meta (titles + item counts) from cache or Frappe
		3. Check access per track via AccessService
		4. If filter=completed, prune to nodes with completed lessons
		5. Return PracticeHierarchyResponse

		Returns None if subject not found.
		"""
		# Step 1: Load hierarchy structure
		hier = await self.hierarchy.get_hierarchy(subject_id)
		if not hier:
			return None

		# Step 2: Load practice metadata (titles + item counts)
		meta = await self._load_hierarchy_meta(subject_id)
		if not meta:
			return None

		item_counts: dict[str, int] = meta.get("item_counts", {})
		track_titles: dict[str, dict] = meta.get("tracks", {})
		unit_data: dict[str, dict] = meta.get("units", {})
		topic_data: dict[str, dict] = meta.get("topics", {})

		# Step 3 (optional): Get completed lesson IDs for filtering
		completed_lesson_ids: set[str] | None = None
		if filter_mode == "completed":
			completed_lesson_ids = await self._get_completed_lesson_ids(player_id, subject_id, hier)

		# Step 4: Build response with access checks
		tracks: list[PracticeTrackInfo] = []

		for track in hier.tracks:
			track_id = track.track_id

			# Check access: subject grant OR plan membership OR track grant
			has_access = await self._check_track_access(player_id, subject_id, track_id, plan_id)

			# If no access, include track (for UI) but with empty units
			if not has_access:
				# Still compute track-level item count for display
				track_item_count = self._compute_track_item_count(track, item_counts)
				if filter_mode == "completed":
					# Skip inaccessible tracks entirely in completed mode
					continue
				tracks.append(
					PracticeTrackInfo(
						track_id=track_id,
						track_title=track_titles.get(track_id, {}).get("title", track_id),
						has_access=False,
						item_count=track_item_count,
						units=[],
					)
				)
				continue

			# Build units and topics for accessible tracks
			units: list[PracticeUnitInfo] = []
			track_item_count = 0

			for unit in track.units:
				unit_id = unit.unit_id
				topics: list[PracticeTopicInfo] = []
				unit_item_count = 0

				for topic in unit.topics:
					topic_id = topic.topic_id
					topic_count = item_counts.get(topic_id, 0)

					# Apply completed filter: skip topics with no completed lessons
					if completed_lesson_ids is not None:
						topic_lesson_ids = {l.lesson_id for l in topic.lessons}
						if not topic_lesson_ids & completed_lesson_ids:
							continue

					if topic_count > 0:
						topics.append(
							PracticeTopicInfo(
								topic_id=topic_id,
								topic_title=topic_data.get(topic_id, {}).get("title", topic_id),
								item_count=topic_count,
							)
						)
						unit_item_count += topic_count

				# Skip empty units after filtering
				if not topics and filter_mode == "completed":
					continue

				if topics:
					units.append(
						PracticeUnitInfo(
							unit_id=unit_id,
							unit_title=unit_data.get(unit_id, {}).get("title", unit_id),
							item_count=unit_item_count,
							topics=topics,
						)
					)
					track_item_count += unit_item_count

			# Skip empty tracks after filtering
			if not units and filter_mode == "completed":
				continue

			tracks.append(
				PracticeTrackInfo(
					track_id=track_id,
					track_title=track_titles.get(track_id, {}).get("title", track_id),
					has_access=True,
					item_count=track_item_count,
					units=units,
				)
			)

		return PracticeHierarchyResponse(
			subject_id=subject_id,
			subject_title=meta.get("subject_title", subject_id),
			tracks=tracks,
		)

	# =========================================================================
	# Private Helpers
	# =========================================================================

	async def _load_hierarchy_meta(self, subject_id: str) -> dict | None:
		"""Load practice hierarchy metadata from cache or Frappe.

		Caches titles + Review Item counts with 1h TTL.
		On miss, calls Frappe API to fetch from MariaDB.
		"""
		cache_key = practice_hierarchy_meta_key(subject_id)

		# Try cache first
		cached = await self.redis.get(cache_key)
		if cached:
			return json.loads(cached)

		# Cache miss — fetch from Frappe
		if not self.frappe:
			logger.warning(
				"practice_meta_fetch_skipped",
				subject_id=subject_id,
				reason="no_frappe_client",
			)
			return None

		try:
			result = await self.frappe.call(
				"memora_admin.api.practice.get_practice_hierarchy_meta",
				{"subject_id": subject_id},
			)
		except Exception as e:
			logger.error(
				"practice_meta_fetch_failed",
				subject_id=subject_id,
				error=str(e),
			)
			return None

		if not result:
			return None

		# Cache with TTL
		await self.redis.set(cache_key, json.dumps(result), ex=META_CACHE_TTL)

		return result

	async def _check_track_access(
		self,
		player_id: str,
		subject_id: str,
		track_id: str,
		plan_id: str | None,
	) -> bool:
		"""Check if player has access to a track.

		Access is granted if:
		1. Subject-level grant (SUB-{subject_id})
		2. Plan membership (subject free in plan)
		3. Track-level grant (TRK-{track_id})
		4. Track has any free content (free units/topics within)
		"""
		# Check subject-level access (grant or plan)
		subject_key = f"SUB-{subject_id}"
		if await self.access.check_access_with_plan(player_id, subject_key, plan_id):
			return True

		# Check track-level grant
		track_key = f"TRK-{track_id}"
		if await self.access.check_access(player_id, track_key):
			return True

		return False

	async def _get_completed_lesson_ids(self, player_id: str, subject_id: str, hier) -> set[str]:
		"""Get set of completed lesson IDs for a subject.

		Decodes progress bitmap and maps bit_indices to lesson IDs.
		"""
		completed_bits = await self.progress.get_completed_bits(
			player_id, subject_id, hier.bit_range, hier.version
		)

		# Build bit_index → lesson_id mapping from hierarchy
		completed_lessons: set[str] = set()
		for track in hier.tracks:
			for unit in track.units:
				for topic in unit.topics:
					for lesson in topic.lessons:
						if lesson.bit_index in completed_bits:
							completed_lessons.add(lesson.lesson_id)

		return completed_lessons

	def _compute_track_item_count(self, track, item_counts: dict[str, int]) -> int:
		"""Sum Review Item counts across all topics in a track."""
		total = 0
		for unit in track.units:
			for topic in unit.topics:
				total += item_counts.get(topic.topic_id, 0)
		return total

	# =========================================================================
	# Session Management (US3 + US4)
	# =========================================================================

	async def start_session(
		self,
		player_id: str,
		subject_id: str,
		plan_id: str | None,
		filter_mode: str,
		tracks: list[str],
		units: list[str],
		topics: list[str],
	) -> PracticeBatchResponse:
		"""Start a new practice session.

		Validates access, resolves accessible lessons, creates Redis session,
		selects first batch of questions.

		Raises:
			PracticeAccessDenied: If any selected track is inaccessible
			NoItemsError: If filters produce zero items
		"""
		log = logger.bind(player_id=player_id, subject_id=subject_id)

		# Load hierarchy
		hier = await self.hierarchy.get_hierarchy(subject_id)
		if not hier:
			raise NoItemsError()

		# Resolve accessible lessons + check access
		lesson_ids, denied_tracks = await self._get_accessible_lessons(
			player_id,
			subject_id,
			plan_id,
			hier,
			tracks,
			units,
			topics,
			filter_mode,
		)

		if denied_tracks:
			log.info("practice_access_denied", denied_tracks=denied_tracks)
			raise PracticeAccessDenied(denied_tracks)

		if not lesson_ids:
			log.info("practice_no_items", filter=filter_mode, tracks=tracks)
			raise NoItemsError()

		# Resolve topic IDs for selected lessons (for proportional distribution)
		selected_topic_ids = self._get_topic_ids_for_lessons(hier, lesson_ids)

		# Select first batch of questions
		batch_size = self.config.practice_session_size
		questions, total_available = await self._select_questions(
			player_id=player_id,
			subject_id=subject_id,
			accessible_lessons=lesson_ids,
			selected_topics=selected_topic_ids,
			served_item_ids=[],
			batch_size=batch_size,
		)

		all_seen = total_available > 0 and len(questions) == 0
		served_ids = [q.item_id for q in questions]

		# Create Redis session (overwrites any existing session)
		session_key = practice_session_key(player_id)
		now = datetime.now(timezone.utc).isoformat()

		session_data = {
			"subject_id": subject_id,
			"filter": filter_mode,
			"tracks": json.dumps(tracks),
			"units": json.dumps(units),
			"topics": json.dumps(topics),
			"batch_seq": "0",
			"served_item_ids": json.dumps(served_ids),
			"accessible_lessons": json.dumps(lesson_ids),
			"selected_topics": json.dumps(selected_topic_ids),
			"created_at": now,
		}

		pipe = self.redis.pipeline()
		pipe.delete(session_key)
		pipe.hset(session_key, mapping=session_data)
		pipe.expire(session_key, self.config.practice_session_ttl)
		await pipe.execute()

		log.info(
			"practice_session_started",
			batch_seq=0,
			item_count=len(questions),
			total_available=total_available,
			track_count=len(tracks),
			all_seen=all_seen,
		)

		return PracticeBatchResponse(
			session_active=True,
			batch_seq=0,
			questions=questions,
			total_available=total_available,
			all_seen_warning=all_seen,
		)

	async def submit_batch(
		self,
		player_id: str,
		batch_seq: int,
		results: list[dict],
	) -> PracticeSubmitResponse:
		"""Submit results for a practice batch.

		Idempotent via submitted_{batch_seq} marker in session hash.

		Raises:
			NoActiveSessionError: If no active session exists
			BatchSeqMismatchError: If batch_seq is ahead of current
		"""
		log = logger.bind(player_id=player_id, batch_seq=batch_seq)
		session_key = practice_session_key(player_id)
		session = await self.redis.hgetall(session_key)

		if not session:
			log.info("practice_session_expired")
			raise NoActiveSessionError()

		current_seq = int(session.get("batch_seq", "0"))

		# Check for future batch_seq (skipping batches)
		if batch_seq > current_seq:
			raise BatchSeqMismatchError(expected=current_seq, received=batch_seq)

		# Check for duplicate submission
		submitted_marker = f"submitted_{batch_seq}"
		if session.get(submitted_marker):
			# Duplicate — return cached result without updating Practice Log
			log.info("practice_batch_duplicate")
			correct = sum(1 for r in results if r.get("is_correct"))
			total = len(results)
			return PracticeSubmitResponse(
				accepted=True,
				batch_seq=batch_seq,
				correct_count=correct,
				total_count=total,
				accuracy_percent=round(correct / total * 100, 1) if total > 0 else 0.0,
				is_duplicate=True,
			)

		# UPSERT Practice Log for each result
		now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive for MariaDB
		correct_count = 0
		total_count = 0

		if self.frappe and results:
			# Validate item_ids still exist (items may be deleted during active session)
			submitted_ids = [r.get("item_id", "") for r in results]
			valid_ids = await self._get_valid_item_ids(submitted_ids)
			skipped_ids = set(submitted_ids) - valid_ids

			if skipped_ids:
				log.warning(
					"practice_items_deleted_during_session",
					skipped_count=len(skipped_ids),
					skipped_ids=list(skipped_ids),
				)

			# Filter to valid results only
			valid_results = [r for r in results if r.get("item_id", "") in valid_ids]

			# Build bulk UPSERT values
			values_parts = []
			params = []
			for r in valid_results:
				item_id = r.get("item_id", "")
				is_correct = r.get("is_correct", False)
				result_str = "Correct" if is_correct else "Incorrect"
				correct_int = 1 if is_correct else 0

				if is_correct:
					correct_count += 1
				total_count += 1

				values_parts.append("(%s, %s, %s, %s, %s, 1, %s)")
				params.extend([player_id, item_id, now, now, result_str, correct_int])

			if values_parts:
				sql = f"""
					INSERT INTO `tabMemora Practice Log`
						(player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
					VALUES {", ".join(values_parts)}
					ON DUPLICATE KEY UPDATE
						last_seen_at = VALUES(last_seen_at),
						last_result = VALUES(last_result),
						attempt_count = attempt_count + 1,
						correct_count = correct_count + VALUES(correct_count)
				"""
				try:
					await self.frappe.call(
						"memora_admin.api.practice.execute_practice_log_upsert",
						{"sql": sql, "params": params},
					)
				except Exception as e:
					log.error(
						"practice_log_upsert_failed",
						error=str(e),
					)
		else:
			# Count without DB write (no frappe client)
			for r in results:
				total_count += 1
				if r.get("is_correct"):
					correct_count += 1

		# Set submitted marker in session hash + reset TTL
		pipe = self.redis.pipeline()
		pipe.hset(session_key, submitted_marker, "1")
		pipe.expire(session_key, self.config.practice_session_ttl)
		await pipe.execute()

		log.info(
			"practice_batch_submitted",
			correct_count=correct_count,
			total_count=total_count,
			accuracy_percent=round(correct_count / total_count * 100, 1) if total_count > 0 else 0.0,
		)

		return PracticeSubmitResponse(
			accepted=True,
			batch_seq=batch_seq,
			correct_count=correct_count,
			total_count=total_count,
			accuracy_percent=round(correct_count / total_count * 100, 1) if total_count > 0 else 0.0,
			is_duplicate=False,
		)

	async def continue_session(
		self,
		player_id: str,
	) -> PracticeBatchResponse:
		"""Continue a practice session with the next batch.

		Raises:
			NoActiveSessionError: If no active session exists
			PreviousBatchNotSubmittedError: If previous batch not submitted
		"""
		log = logger.bind(player_id=player_id)
		session_key = practice_session_key(player_id)
		session = await self.redis.hgetall(session_key)

		if not session:
			log.info("practice_session_expired")
			raise NoActiveSessionError()

		current_seq = int(session.get("batch_seq", "0"))

		# Verify current batch was submitted before serving the next one
		submitted_marker = f"submitted_{current_seq}"
		if not session.get(submitted_marker):
			raise PreviousBatchNotSubmittedError(current_seq)

		# Increment batch sequence
		next_seq = current_seq + 1

		# Load session context
		accessible_lessons = json.loads(session.get("accessible_lessons", "[]"))
		selected_topics = json.loads(session.get("selected_topics", "[]"))
		served_item_ids = json.loads(session.get("served_item_ids", "[]"))
		subject_id = session.get("subject_id", "")

		# Select next batch of questions
		batch_size = self.config.practice_session_size
		questions, total_available = await self._select_questions(
			player_id=player_id,
			subject_id=subject_id,
			accessible_lessons=accessible_lessons,
			selected_topics=selected_topics,
			served_item_ids=served_item_ids,
			batch_size=batch_size,
		)

		all_seen = total_available > 0 and len(questions) == 0

		# If all items seen, re-serve from the full pool (wrap around)
		if all_seen and total_available > 0:
			questions, _ = await self._select_questions(
				player_id=player_id,
				subject_id=subject_id,
				accessible_lessons=accessible_lessons,
				selected_topics=selected_topics,
				served_item_ids=[],  # Clear dedup to allow re-serve
				batch_size=batch_size,
			)

		# Update served_item_ids with new batch
		new_served_ids = served_item_ids + [q.item_id for q in questions]

		# Update session in Redis
		pipe = self.redis.pipeline()
		pipe.hset(
			session_key,
			mapping={
				"batch_seq": str(next_seq),
				"served_item_ids": json.dumps(new_served_ids),
			},
		)
		pipe.expire(session_key, self.config.practice_session_ttl)
		await pipe.execute()

		log.info(
			"practice_session_continued",
			batch_seq=next_seq,
			item_count=len(questions),
			total_available=total_available,
			all_seen=all_seen,
		)

		return PracticeBatchResponse(
			session_active=True,
			batch_seq=next_seq,
			questions=questions,
			total_available=total_available,
			all_seen_warning=all_seen,
		)

	# =========================================================================
	# Session Helpers
	# =========================================================================

	async def _get_accessible_lessons(
		self,
		player_id: str,
		subject_id: str,
		plan_id: str | None,
		hier: SubjectHierarchy,
		tracks: list[str],
		units: list[str],
		topics: list[str],
		filter_mode: str,
	) -> tuple[list[str], list[str]]:
		"""Resolve accessible lesson IDs based on access control and filters.

		For each selected track:
		- If player has full access (grant/plan): include all lessons
		- If no access: check for free content within the track
		  - Include lessons from free units/topics
		  - If zero free content, add to denied_tracks

		Returns:
			Tuple of (lesson_ids, denied_tracks)
		"""
		track_set = set(tracks)
		unit_set = set(units) if units else None
		topic_set = set(topics) if topics else None

		# Get completed lesson IDs if filter=completed
		completed_lesson_ids: set[str] | None = None
		if filter_mode == "completed":
			completed_lesson_ids = await self._get_completed_lesson_ids(player_id, subject_id, hier)

		free_units_set = set(hier.free_units)
		free_topics_set = set(hier.free_topics)

		lesson_ids: list[str] = []
		denied_tracks: list[str] = []

		for track in hier.tracks:
			if track.track_id not in track_set:
				continue

			# Check full access to this track
			has_access = await self._check_track_access(player_id, subject_id, track.track_id, plan_id)

			track_lessons: list[str] = []

			for unit in track.units:
				# Apply unit filter if provided
				if unit_set and unit.unit_id not in unit_set:
					continue

				for topic in unit.topics:
					# Apply topic filter if provided
					if topic_set and topic.topic_id not in topic_set:
						continue

					for lesson in topic.lessons:
						# If fully accessible, include all lessons
						if has_access:
							track_lessons.append(lesson.lesson_id)
						else:
							# No full access — only include free content
							is_free = (
								unit.unit_id in free_units_set
								or topic.topic_id in free_topics_set
								or topic.is_free
								or unit.is_free
							)
							if is_free:
								track_lessons.append(lesson.lesson_id)

			# Apply completed filter
			if completed_lesson_ids is not None:
				track_lessons = [lid for lid in track_lessons if lid in completed_lesson_ids]

			if not has_access and not track_lessons:
				# Inaccessible track with zero free content
				denied_tracks.append(track.track_id)
			else:
				lesson_ids.extend(track_lessons)

		return lesson_ids, denied_tracks

	async def _select_questions(
		self,
		player_id: str,
		subject_id: str,
		accessible_lessons: list[str],
		selected_topics: list[str],
		served_item_ids: list[str],
		batch_size: int,
	) -> tuple[list[PracticeQuestion], int]:
		"""Select questions with 3-tier priority and proportional topic distribution.

		Priority:
		0 = never seen (no Practice Log entry)
		1 = seen before, not in this session
		2 = seen in this session (served_item_ids)

		Returns:
			Tuple of (questions, total_available_items)
		"""
		if not accessible_lessons or not self.frappe:
			return [], 0

		# Build the query with parameterized placeholders
		lesson_placeholders = ", ".join(["%s"] * len(accessible_lessons))

		# Base WHERE clause
		where_clause = f"ri.subject = %s AND ri.lesson IN ({lesson_placeholders})"
		params: list = [subject_id, *accessible_lessons]

		# Add topic filter if specified
		if selected_topics:
			topic_placeholders = ", ".join(["%s"] * len(selected_topics))
			where_clause += f" AND ri.topic IN ({topic_placeholders})"
			params.extend(selected_topics)

		# Build served_item_ids CASE expression
		if served_item_ids:
			served_placeholders = ", ".join(["%s"] * len(served_item_ids))
			priority_case = f"""
				CASE
					WHEN pl.item_id IS NULL THEN 0
					WHEN ri.item_id NOT IN ({served_placeholders}) THEN 1
					ELSE 2
				END
			"""
			priority_params = list(served_item_ids)
		else:
			priority_case = """
				CASE
					WHEN pl.item_id IS NULL THEN 0
					ELSE 1
				END
			"""
			priority_params = []

		# First, get total count of available items
		count_sql = f"""
			SELECT COUNT(*) as cnt
			FROM `tabMemora Review Item` ri
			WHERE {where_clause}
		"""

		# Then get the actual questions with priority ordering
		select_sql = f"""
			SELECT ri.item_id, ri.question_text, ri.choice_1, ri.choice_2,
				   ri.choice_3, ri.choice_4, ri.correct_choice, ri.content_json,
				   ri.stage_type, ri.topic,
				   {priority_case} AS priority,
				   COALESCE(pl.last_seen_at, '1970-01-01') AS sort_seen
			FROM `tabMemora Review Item` ri
			LEFT JOIN `tabMemora Practice Log` pl
				ON pl.item_id = ri.item_id AND pl.player_id = %s
			WHERE {where_clause}
			ORDER BY priority ASC, sort_seen ASC
			LIMIT %s
		"""

		try:
			# Get total count
			count_result = await self.frappe.call(
				"memora_admin.api.practice.execute_practice_query",
				{"sql": count_sql, "params": list(params)},
			)
			total_available = count_result[0]["cnt"] if count_result else 0

			# Get questions
			select_params = [*priority_params, player_id, *params, batch_size]
			rows = await self.frappe.call(
				"memora_admin.api.practice.execute_practice_query",
				{"sql": select_sql, "params": select_params},
			)
		except Exception as e:
			logger.error(
				"practice_question_select_failed",
				player_id=player_id,
				subject_id=subject_id,
				error=str(e),
			)
			return [], 0

		if not rows:
			return [], total_available

		# Convert rows to PracticeQuestion models
		questions = []
		for row in rows:
			choices = []
			for i in range(1, 5):
				c = row.get(f"choice_{i}")
				if c:
					choices.append(c)

			content_json = None
			raw_content = row.get("content_json")
			if raw_content:
				try:
					content_json = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
				except (json.JSONDecodeError, TypeError):
					content_json = None

			questions.append(
				PracticeQuestion(
					item_id=row["item_id"],
					stage_type=row.get("stage_type", ""),
					question_text=row.get("question_text"),
					choices=choices,
					correct_choice=row.get("correct_choice"),
					content_json=content_json,
				)
			)

		return questions, total_available

	async def _get_valid_item_ids(self, item_ids: list[str]) -> set[str]:
		"""Check which item_ids still exist in the Review Item table.

		Used to handle the edge case where items are deleted during an active session.
		"""
		if not item_ids or not self.frappe:
			return set(item_ids)

		placeholders = ", ".join(["%s"] * len(item_ids))
		sql = f"SELECT item_id FROM `tabMemora Review Item` WHERE item_id IN ({placeholders})"
		try:
			rows = await self.frappe.call(
				"memora_admin.api.practice.execute_practice_query",
				{"sql": sql, "params": list(item_ids)},
			)
			return {r["item_id"] for r in (rows or [])}
		except Exception:
			# On failure, assume all valid to avoid data loss
			return set(item_ids)

	def _get_topic_ids_for_lessons(self, hier: SubjectHierarchy, lesson_ids: list[str]) -> list[str]:
		"""Get unique topic IDs that contain the given lessons."""
		lesson_set = set(lesson_ids)
		topic_ids: list[str] = []
		seen: set[str] = set()

		for track in hier.tracks:
			for unit in track.units:
				for topic in unit.topics:
					if topic.topic_id in seen:
						continue
					for lesson in topic.lessons:
						if lesson.lesson_id in lesson_set:
							topic_ids.append(topic.topic_id)
							seen.add(topic.topic_id)
							break

		return topic_ids
