"""Practice Arena service for hierarchy browsing and session management."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog

from fastapi_app.core.coalesce import CoalescingLockPool
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
PRACTICE_SESSION_SCHEMA_VERSION = 2

# Process-local per-key locks for practice metadata cache-fill coalescing.
_meta_fill_locks = CoalescingLockPool(max_size=5_000)


def _get_meta_fill_lock(key: str) -> asyncio.Lock:
	"""Backward-compatible accessor for the per-key practice meta fill lock."""
	return _meta_fill_locks.get(key)


class PracticeAccessDenied(Exception):
	"""Raised when player has no access to one or more selected tracks."""

	def __init__(self, denied_tracks: list[str]):
		self.denied_tracks = denied_tracks
		super().__init__(f"No access to tracks: {denied_tracks}")


class NoItemsError(Exception):
	"""Raised when filters produce zero reviewable items."""


class PracticeSubjectNotFoundError(Exception):
	"""Raised when the requested subject does not exist."""


class PracticeHierarchyMetaUnavailableError(Exception):
	"""Raised when practice metadata cannot be loaded for a valid subject."""


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


class OffBatchItemError(Exception):
	"""Raised when submitted item_ids were not served in the active batch."""

	def __init__(self, off_batch_ids: list[str]):
		self.off_batch_ids = off_batch_ids
		super().__init__(f"Items not in served batch: {off_batch_ids[:5]}")


class InvalidSessionStateError(Exception):
	"""Raised when a current-format session is missing required state."""

	def __init__(self, missing_field: str):
		self.missing_field = missing_field
		super().__init__(f"Session missing required field: {missing_field}")


def _compute_topic_quotas(counts: dict[str, int], batch_size: int) -> dict[str, int]:
	"""Distribute batch_size across topics proportionally by item count.

	Each topic gets at least 1 question (if it has items). Remainder goes
	to the largest topics. Quotas are capped at the topic's available count.

	Args:
		counts: Mapping of topic_id → available item count (must be > 0 for each).
		batch_size: Total number of questions to distribute.

	Returns:
		Mapping of topic_id → quota (number of questions to draw from that topic).
	"""
	if not counts:
		return {}

	total = sum(counts.values())
	if total == 0:
		return {}

	# Single topic — skip proportional logic
	if len(counts) == 1:
		topic_id = next(iter(counts))
		return {topic_id: min(batch_size, counts[topic_id])}

	quotas: dict[str, int] = {}
	remaining = batch_size

	# First pass: proportional allocation (round down), min 1 each, capped at available
	for topic_id, count in counts.items():
		quota = max(1, int(batch_size * count / total))
		quota = min(quota, count)  # Don't exceed available
		quotas[topic_id] = quota
		remaining -= quota

	# Second pass: distribute remainder to largest topics (that still have room)
	if remaining > 0:
		sorted_topics = sorted(counts.keys(), key=lambda t: counts[t], reverse=True)
		for topic_id in sorted_topics:
			if remaining <= 0:
				break
			extra = min(remaining, counts[topic_id] - quotas[topic_id])
			quotas[topic_id] += extra
			remaining -= extra

	return quotas


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
	) -> PracticeHierarchyResponse:
		"""Build practice hierarchy with titles, item counts, and access flags.

		Flow:
		1. Load SubjectHierarchy from cache (structure + free content)
		2. Load practice meta (titles + item counts) from cache or Frappe
		3. Check access per track via AccessService
		4. If filter=completed, prune to nodes with completed lessons
		5. Return PracticeHierarchyResponse

		Raises:
			PracticeSubjectNotFoundError: If the subject does not exist
			PracticeHierarchyMetaUnavailableError: If metadata cannot be loaded
		"""
		# Step 1: Load hierarchy structure
		hier = await self.hierarchy.get_hierarchy(subject_id)
		if not hier:
			raise PracticeSubjectNotFoundError(subject_id)

		# Step 2: Load practice metadata (titles + item counts)
		meta = await self._load_hierarchy_meta(subject_id)
		if not meta:
			raise PracticeHierarchyMetaUnavailableError(subject_id)

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

		# Hoist subject-level access check out of the per-track loop.
		# This is invariant (same player, subject, plan for every track)
		# and saves N-1 redundant Redis lookups.
		subject_key = f"SUB-{subject_id}"
		has_subject_access = await self.access.check_access_with_plan(player_id, subject_key, plan_id)

		for track in hier.tracks:
			track_id = track.track_id

			# Check full access: subject-level (already computed) or track-level grant
			if has_subject_access:
				has_full_access = True
			else:
				track_key = f"TRK-{track_id}"
				has_full_access = await self.access.check_access(player_id, track_key)
			has_free = self._track_has_free_content(hier, track_id)
			has_access = has_full_access or has_free

			# If no access at all, include track (for UI) but with empty units
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

			# Build units and topics for accessible tracks.
			# When the player only has free content access (not full),
			# restrict to free units/topics so the UI doesn't show paid
			# nodes the player can't actually select.
			free_only = has_free and not has_full_access
			free_units_set = set(hier.free_units) if free_only else set()
			free_topics_set = set(hier.free_topics) if free_only else set()

			units: list[PracticeUnitInfo] = []
			track_item_count = 0

			for unit in track.units:
				unit_id = unit.unit_id
				unit_is_free = unit.unit_id in free_units_set or unit.is_free

				# In free-only mode, skip units that are neither free
				# themselves nor contain any free topics
				if free_only and not unit_is_free:
					has_free_topic = any(t.topic_id in free_topics_set or t.is_free for t in unit.topics)
					if not has_free_topic:
						continue

				topics: list[PracticeTopicInfo] = []
				unit_item_count = 0

				for topic in unit.topics:
					topic_id = topic.topic_id

					# In free-only mode, skip paid topics (unless entire unit is free)
					if free_only and not unit_is_free:
						if topic_id not in free_topics_set and not topic.is_free:
							continue

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
				if not topics and (filter_mode == "completed" or free_only):
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
		On miss, coalesces concurrent fills via per-key lock so only one
		Frappe call fires per subject per worker process.
		"""
		cache_key = practice_hierarchy_meta_key(subject_id)

		# Try cache first
		cached = await self.redis.get(cache_key)
		if cached:
			return json.loads(cached)

		# Cache miss — no Frappe client means we can't fill
		if not self.frappe:
			logger.warning(
				"practice_meta_fetch_skipped",
				subject_id=subject_id,
				reason="no_frappe_client",
			)
			return None

		# Coalesce concurrent fills via per-key lock
		fill_lock = _meta_fill_locks.get(subject_id)
		acquired = False
		try:
			await asyncio.wait_for(fill_lock.acquire(), timeout=5.0)
			acquired = True
		except (asyncio.TimeoutError, TimeoutError):
			logger.warning("meta_fill_timeout", subject_id=subject_id)

		try:
			if acquired:
				# Double-check: another request may have filled while we waited
				cached = await self.redis.get(cache_key)
				if cached:
					logger.debug("meta_fill_coalesced", subject_id=subject_id)
					return json.loads(cached)

			# Fetch from Frappe
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
		finally:
			if acquired:
				fill_lock.release()

	async def _check_track_access(
		self,
		player_id: str,
		subject_id: str,
		track_id: str,
		plan_id: str | None,
	) -> bool:
		"""Check if player has FULL access to a track (grants/plan only).

		Full access means every lesson in the track is accessible.
		Does NOT consider free content — that's handled separately by
		_get_accessible_lessons (for session start) and _track_has_free_content
		(for hierarchy browsing).

		Access is granted if:
		1. Subject-level grant (SUB-{subject_id})
		2. Plan membership (subject free in plan)
		3. Track-level grant (TRK-{track_id})
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

	@staticmethod
	def _track_has_free_content(hier: SubjectHierarchy, track_id: str) -> bool:
		"""Check if a track contains any free units or topics.

		Used by the hierarchy endpoint to decide whether to show the track
		as browsable (with units visible) even without an explicit grant.
		Does NOT grant full access to all lessons — only signals "some
		content is viewable."
		"""
		free_units = set(hier.free_units)
		free_topics = set(hier.free_topics)
		for track_obj in hier.tracks:
			if track_obj.track_id != track_id:
				continue
			for unit in track_obj.units:
				if unit.unit_id in free_units or unit.is_free:
					return True
				for topic in unit.topics:
					if topic.topic_id in free_topics or topic.is_free:
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
		questions, total_available, any_repeat = await self._select_questions(
			player_id=player_id,
			subject_id=subject_id,
			accessible_lessons=lesson_ids,
			selected_topics=selected_topic_ids,
			served_item_ids=[],
			batch_size=batch_size,
		)

		all_seen = any_repeat
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
			"schema_version": str(PRACTICE_SESSION_SCHEMA_VERSION),
			"batch_seq": "0",
			"served_item_ids": json.dumps(served_ids),
			"batch_0_item_ids": json.dumps(served_ids),
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
		The marker stores the original response JSON so duplicate submissions
		return the exact same result regardless of payload changes.

		Validates that submitted item_ids were actually served in the batch
		(stored in served_item_ids). Off-batch items are rejected.

		Raises:
			NoActiveSessionError: If no active session exists
			BatchSeqMismatchError: If batch_seq is ahead of current
			OffBatchItemError: If submitted items weren't served in the batch
			InvalidSessionStateError: If a current-format session is malformed
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

		# Check for duplicate submission — return cached original response
		submitted_marker = f"submitted_{batch_seq}"
		cached_result = session.get(submitted_marker)
		if cached_result:
			log.info("practice_batch_duplicate")
			try:
				cached = json.loads(cached_result)
			except (json.JSONDecodeError, TypeError):
				cached = None

			# Legacy sessions stored "1" as the marker (not JSON), which
			# parses as the integer 1 — not a dict.  Treat any non-dict
			# marker as "already submitted but no cached stats".
			if isinstance(cached, dict) and "correct_count" in cached:
				return PracticeSubmitResponse(
					accepted=True,
					batch_seq=batch_seq,
					correct_count=cached["correct_count"],
					total_count=cached["total_count"],
					accuracy_percent=cached["accuracy_percent"],
					is_duplicate=True,
				)

			# Legacy marker (pre-deploy "1") — no cached stats available.
			# Recompute from submitted results to preserve API contract.
			# May differ from original submit if client tampered, but old
			# code had the same behavior and these sessions expire in ≤1h.
			legacy_correct = sum(1 for r in results if r.get("is_correct"))
			legacy_total = len(results)
			return PracticeSubmitResponse(
				accepted=True,
				batch_seq=batch_seq,
				correct_count=legacy_correct,
				total_count=legacy_total,
				accuracy_percent=round(legacy_correct / legacy_total * 100, 1) if legacy_total > 0 else 0.0,
				is_duplicate=True,
			)

		# Validate submitted items were actually served in THIS specific batch.
		# Legacy sessions (pre-deploy) lack both schema_version and per-batch
		# keys. Only those sessions skip validation to preserve rollout safety.
		# Current-format sessions must fail closed if required state is missing.
		batch_items_key = f"batch_{batch_seq}_item_ids"
		raw_batch_ids = session.get(batch_items_key)
		raw_schema_version = session.get("schema_version")
		try:
			schema_version = int(raw_schema_version) if raw_schema_version is not None else 1
		except (TypeError, ValueError):
			schema_version = 1
		is_legacy_session = schema_version < PRACTICE_SESSION_SCHEMA_VERSION
		submitted_ids = [r.get("item_id", "") for r in results]
		if raw_batch_ids is None:
			if is_legacy_session:
				log.info(
					"practice_legacy_session_skip_validation",
					batch_seq=batch_seq,
					reason="no_per_batch_key",
				)
			else:
				log.error(
					"practice_session_missing_batch_key",
					batch_seq=batch_seq,
					missing_field=batch_items_key,
				)
				raise InvalidSessionStateError(batch_items_key)
		else:
			batch_item_ids = set(json.loads(raw_batch_ids))
			off_batch = [iid for iid in submitted_ids if iid not in batch_item_ids]
			if off_batch:
				log.warning(
					"practice_off_batch_items",
					off_batch_count=len(off_batch),
					off_batch_ids=off_batch[:5],
				)
				raise OffBatchItemError(off_batch)

		# UPSERT Practice Log for each result
		now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()  # ISO string for JSON serialization
		correct_count = 0
		total_count = 0

		if self.frappe and results:
			# Validate item_ids still exist (items may be deleted during active session)
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
				# Let exception propagate — do NOT mark as submitted on DB failure
				await self.frappe.call(
					"memora_admin.api.practice.execute_practice_log_upsert",
					{"sql": sql, "params": params},
				)
		else:
			# Count without DB write (no frappe client)
			for r in results:
				total_count += 1
				if r.get("is_correct"):
					correct_count += 1

		accuracy = round(correct_count / total_count * 100, 1) if total_count > 0 else 0.0

		# Cache the result in the submitted marker so duplicates return identical response
		cached_payload = json.dumps(
			{
				"correct_count": correct_count,
				"total_count": total_count,
				"accuracy_percent": accuracy,
			}
		)

		# Set submitted marker in session hash + reset TTL
		pipe = self.redis.pipeline()
		pipe.hset(session_key, submitted_marker, cached_payload)
		pipe.expire(session_key, self.config.practice_session_ttl)
		await pipe.execute()

		log.info(
			"practice_batch_submitted",
			correct_count=correct_count,
			total_count=total_count,
			accuracy_percent=accuracy,
		)

		return PracticeSubmitResponse(
			accepted=True,
			batch_seq=batch_seq,
			correct_count=correct_count,
			total_count=total_count,
			accuracy_percent=accuracy,
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

		# Count once up front so we can short-circuit the guaranteed-empty
		# pre-wrap query when this session has already seen the full pool.
		topic_counts = await self._count_items_per_topic(
			subject_id,
			accessible_lessons,
			selected_topics,
		)
		total_available = sum(topic_counts.values())

		# Select next batch of questions
		batch_size = self.config.practice_session_size
		served_unique_ids = set(served_item_ids)
		should_wrap = total_available > 0 and len(served_unique_ids) >= total_available
		if should_wrap and served_unique_ids:
			# Deleted Review Items can leave stale IDs in session history.
			# Re-check only when we are about to short-circuit into wrap-around,
			# so deleted items do not force premature repeats.
			valid_served_ids = await self._get_valid_item_ids(list(served_unique_ids))
			should_wrap = len(valid_served_ids) >= total_available

		if should_wrap:
			questions, _, _ = await self._select_questions(
				player_id=player_id,
				subject_id=subject_id,
				accessible_lessons=accessible_lessons,
				selected_topics=selected_topics,
				served_item_ids=[],  # Clear dedup to allow re-serve
				batch_size=batch_size,
				topic_counts=topic_counts,
			)
			all_seen = True  # Always true when wrapping around
		else:
			questions, _, any_repeat = await self._select_questions(
				player_id=player_id,
				subject_id=subject_id,
				accessible_lessons=accessible_lessons,
				selected_topics=selected_topics,
				served_item_ids=served_item_ids,
				batch_size=batch_size,
				topic_counts=topic_counts,
			)

			all_seen = any_repeat

			# If all items exhausted, re-serve from the full pool (wrap around)
			if not questions and total_available > 0:
				questions, _, _ = await self._select_questions(
					player_id=player_id,
					subject_id=subject_id,
					accessible_lessons=accessible_lessons,
					selected_topics=selected_topics,
					served_item_ids=[],  # Clear dedup to allow re-serve
					batch_size=batch_size,
					topic_counts=topic_counts,
				)
				all_seen = True  # Always true when wrapping around

		# Update served_item_ids with new batch
		batch_ids = [q.item_id for q in questions]
		new_served_ids = served_item_ids + batch_ids

		# Update session in Redis
		pipe = self.redis.pipeline()
		pipe.hset(
			session_key,
			mapping={
				"batch_seq": str(next_seq),
				"served_item_ids": json.dumps(new_served_ids),
				f"batch_{next_seq}_item_ids": json.dumps(batch_ids),
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

			# Check full access to this track (grants/plan only — free content handled below)
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

	async def _count_items_per_topic(
		self,
		subject_id: str,
		accessible_lessons: list[str],
		selected_topics: list[str],
	) -> dict[str, int]:
		"""Count available Review Items per topic via SQL COUNT grouped by topic.

		Returns:
			Mapping of topic_id → item count (topics with 0 items omitted).
		"""
		if not accessible_lessons or not self.frappe:
			return {}

		lesson_placeholders = ", ".join(["%s"] * len(accessible_lessons))
		where_clause = f"ri.subject = %s AND ri.lesson IN ({lesson_placeholders})"
		params: list = [subject_id, *accessible_lessons]

		if selected_topics:
			topic_placeholders = ", ".join(["%s"] * len(selected_topics))
			where_clause += f" AND ri.topic IN ({topic_placeholders})"
			params.extend(selected_topics)

		sql = f"""
			SELECT ri.topic, COUNT(*) as cnt
			FROM `tabMemora Review Item` ri
			WHERE {where_clause}
			GROUP BY ri.topic
		"""

		try:
			rows = await self.frappe.call(
				"memora_admin.api.practice.execute_practice_query",
				{"sql": sql, "params": params},
			)
		except Exception as e:
			logger.error("practice_count_per_topic_failed", error=str(e))
			return {}

		if not rows:
			return {}

		return {row["topic"]: row["cnt"] for row in rows}

	async def _select_questions(
		self,
		player_id: str,
		subject_id: str,
		accessible_lessons: list[str],
		selected_topics: list[str],
		served_item_ids: list[str],
		batch_size: int,
		topic_counts: dict[str, int] | None = None,
	) -> tuple[list[PracticeQuestion], int, bool]:
		"""Select questions with priority ordering and proportional topic distribution.

		Priority:
		0 = never seen (no Practice Log entry)
		1 = seen before (has Practice Log entry)

		Proportional distribution: Questions are distributed across topics
		proportionally by available item count. A single-topic selection
		collapses to a single quota automatically.

		Two-pass approach:
		1. Fetch a per-topic candidate pool (batched by default, legacy per-topic
		   loop still available behind a config fallback).
		2. Fill each topic's quota from that pool.
		3. Redistribute any unfilled slots from leftover in-memory candidates.

		Returns:
			Tuple of (questions, total_available_items, any_repeat).
			any_repeat is True if ANY returned question has priority > 0
			(i.e., has been seen before by this student).
		"""
		if not accessible_lessons or not self.frappe:
			return [], 0, False

		# Get per-topic counts for proportional distribution
		if topic_counts is None:
			topic_counts = await self._count_items_per_topic(
				subject_id,
				accessible_lessons,
				selected_topics,
			)

		total_available = sum(topic_counts.values())

		if total_available == 0:
			return [], 0, False

		# Compute per-topic quotas
		quotas = _compute_topic_quotas(topic_counts, batch_size)
		if not quotas:
			return [], total_available, False

		if not self.config.practice_batched_topic_select_enabled:
			questions, any_repeat = await self._select_questions_legacy(
				player_id=player_id,
				subject_id=subject_id,
				accessible_lessons=accessible_lessons,
				served_item_ids=served_item_ids,
				topic_counts=topic_counts,
				quotas=quotas,
			)
			return questions, total_available, any_repeat

		batched_result = await self._select_questions_batched(
			player_id=player_id,
			subject_id=subject_id,
			accessible_lessons=accessible_lessons,
			served_item_ids=served_item_ids,
			topic_counts=topic_counts,
			quotas=quotas,
			batch_size=batch_size,
		)
		if batched_result is not None:
			questions, any_repeat = batched_result
			return questions, total_available, any_repeat

		questions, any_repeat = await self._select_questions_legacy(
			player_id=player_id,
			subject_id=subject_id,
			accessible_lessons=accessible_lessons,
			served_item_ids=served_item_ids,
			topic_counts=topic_counts,
			quotas=quotas,
		)
		return questions, total_available, any_repeat

	async def _select_questions_batched(
		self,
		player_id: str,
		subject_id: str,
		accessible_lessons: list[str],
		served_item_ids: list[str],
		topic_counts: dict[str, int],
		quotas: dict[str, int],
		batch_size: int,
	) -> tuple[list[PracticeQuestion], bool] | None:
		"""Fetch per-topic candidates in one query, then preserve allocation in Python."""
		candidate_rows = await self._select_candidates_for_topics(
			player_id=player_id,
			subject_id=subject_id,
			accessible_lessons=accessible_lessons,
			topic_ids=list(quotas.keys()),
			served_item_ids=served_item_ids,
			per_topic_limit=batch_size,
		)
		if candidate_rows is None:
			return None

		return self._allocate_questions_from_candidates(
			candidate_rows=candidate_rows,
			quotas=quotas,
			topic_counts=topic_counts,
		)

	async def _select_questions_legacy(
		self,
		player_id: str,
		subject_id: str,
		accessible_lessons: list[str],
		served_item_ids: list[str],
		topic_counts: dict[str, int],
		quotas: dict[str, int],
	) -> tuple[list[PracticeQuestion], bool]:
		"""Legacy per-topic selection loop kept as a rollout fallback."""
		all_questions: list[PracticeQuestion] = []
		any_repeat = False
		topic_returned: dict[str, int] = {}
		topic_exhausted: set[str] = set()

		# Accumulate served IDs including newly selected items for exclusion
		current_served = list(served_item_ids)

		for topic_id, quota in quotas.items():
			questions, has_repeat = await self._select_for_topic(
				player_id=player_id,
				subject_id=subject_id,
				accessible_lessons=accessible_lessons,
				topic_id=topic_id,
				served_item_ids=current_served,
				limit=quota,
			)
			all_questions.extend(questions)
			any_repeat = any_repeat or has_repeat
			topic_returned[topic_id] = len(questions)
			if len(questions) < quota:
				topic_exhausted.add(topic_id)
			current_served.extend(q.item_id for q in questions)

		# Redistribution pass: fill unfilled slots from non-exhausted topics
		unfilled = sum(quotas[t] - topic_returned[t] for t in topic_exhausted)
		if unfilled > 0:
			donors = [t for t in quotas if t not in topic_exhausted]
			if donors:
				donors.sort(key=lambda t: topic_counts.get(t, 0), reverse=True)
				for donor_id in donors:
					if unfilled <= 0:
						break
					extra_questions, has_repeat = await self._select_for_topic(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=accessible_lessons,
						topic_id=donor_id,
						served_item_ids=current_served,
						limit=unfilled,
					)
					all_questions.extend(extra_questions)
					any_repeat = any_repeat or has_repeat
					unfilled -= len(extra_questions)
					current_served.extend(q.item_id for q in extra_questions)

		return all_questions, any_repeat

	async def _select_candidates_for_topics(
		self,
		player_id: str,
		subject_id: str,
		accessible_lessons: list[str],
		topic_ids: list[str],
		served_item_ids: list[str],
		per_topic_limit: int,
	) -> list[dict] | None:
		"""Fetch the top N candidate rows per topic in a single SQL round-trip."""
		if not topic_ids:
			return []

		lesson_placeholders = ", ".join(["%s"] * len(accessible_lessons))
		topic_placeholders = ", ".join(["%s"] * len(topic_ids))
		where_clause = (
			f"ri.subject = %s AND ri.lesson IN ({lesson_placeholders}) "
			f"AND ri.topic IN ({topic_placeholders})"
		)
		params: list = [subject_id, *accessible_lessons, *topic_ids]

		if served_item_ids:
			served_placeholders = ", ".join(["%s"] * len(served_item_ids))
			where_clause += f" AND ri.item_id NOT IN ({served_placeholders})"
			params.extend(served_item_ids)

		priority_case = """
			CASE
				WHEN pl.item_id IS NULL THEN 0
				ELSE 1
			END
		"""
		sort_seen_expr = "COALESCE(pl.last_seen_at, '1970-01-01')"

		select_sql = f"""
			SELECT candidates.item_id, candidates.question_text, candidates.choice_1, candidates.choice_2,
				   candidates.choice_3, candidates.choice_4, candidates.correct_choice, candidates.content_json,
				   candidates.stage_type, candidates.topic, candidates.priority, candidates.sort_seen
			FROM (
				SELECT ri.item_id, ri.question_text, ri.choice_1, ri.choice_2,
					   ri.choice_3, ri.choice_4, ri.correct_choice, ri.content_json,
					   ri.stage_type, ri.topic,
					   {priority_case} AS priority,
					   {sort_seen_expr} AS sort_seen,
					   ROW_NUMBER() OVER (
						   PARTITION BY ri.topic
						   ORDER BY {priority_case} ASC, {sort_seen_expr} ASC, ri.item_id ASC
					   ) AS topic_rank
				FROM `tabMemora Review Item` ri
				LEFT JOIN `tabMemora Practice Log` pl
					ON pl.item_id = ri.item_id AND pl.player_id = %s
				WHERE {where_clause}
			) candidates
			WHERE candidates.topic_rank <= %s
			ORDER BY candidates.topic, candidates.topic_rank
		"""

		select_params = [player_id, *params, per_topic_limit]

		try:
			return await self.frappe.call(
				"memora_admin.api.practice.execute_practice_query",
				{"sql": select_sql, "params": select_params},
			)
		except Exception as e:
			logger.error(
				"practice_batched_select_failed",
				player_id=player_id,
				topic_count=len(topic_ids),
				error=str(e),
			)
			return None

	def _allocate_questions_from_candidates(
		self,
		candidate_rows: list[dict],
		quotas: dict[str, int],
		topic_counts: dict[str, int],
	) -> tuple[list[PracticeQuestion], bool]:
		"""Apply the existing quota-first then redistribution logic in memory."""
		if not candidate_rows:
			return [], False

		candidates_by_topic: dict[str, list[dict]] = {topic_id: [] for topic_id in quotas}
		for row in candidate_rows:
			topic_id = row.get("topic")
			if topic_id in candidates_by_topic:
				candidates_by_topic[topic_id].append(row)

		selected_rows: list[dict] = []
		topic_offsets: dict[str, int] = {}
		topic_exhausted: set[str] = set()

		for topic_id, quota in quotas.items():
			topic_rows = candidates_by_topic.get(topic_id, [])
			chosen = topic_rows[:quota]
			selected_rows.extend(chosen)
			topic_offsets[topic_id] = len(chosen)
			if len(chosen) < quota:
				topic_exhausted.add(topic_id)

		unfilled = sum(quotas[t] - topic_offsets[t] for t in topic_exhausted)
		if unfilled > 0:
			donors = [t for t in quotas if t not in topic_exhausted]
			donors.sort(key=lambda t: topic_counts.get(t, 0), reverse=True)

			for donor_id in donors:
				if unfilled <= 0:
					break
				topic_rows = candidates_by_topic.get(donor_id, [])
				start = topic_offsets.get(donor_id, 0)
				extra_rows = topic_rows[start : start + unfilled]
				selected_rows.extend(extra_rows)
				topic_offsets[donor_id] = start + len(extra_rows)
				unfilled -= len(extra_rows)

		questions: list[PracticeQuestion] = []
		any_repeat = False
		for row in selected_rows:
			if row.get("priority", 0) > 0:
				any_repeat = True
			questions.append(self._build_practice_question(row))

		return questions, any_repeat

	def _build_practice_question(self, row: dict) -> PracticeQuestion:
		"""Convert a SQL row into the PracticeQuestion response model."""
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

		return PracticeQuestion(
			item_id=row["item_id"],
			stage_type=row.get("stage_type", ""),
			question_text=row.get("question_text"),
			choices=choices,
			correct_choice=row.get("correct_choice"),
			content_json=content_json,
		)

	async def _select_for_topic(
		self,
		player_id: str,
		subject_id: str,
		accessible_lessons: list[str],
		topic_id: str,
		served_item_ids: list[str],
		limit: int,
	) -> tuple[list[PracticeQuestion], bool]:
		"""Select questions for a single topic with priority ordering.

		Served items are EXCLUDED (not just ranked lower) to prevent premature
		repeats while other topics still have unseen items. The caller
		(continue_session) handles wrap-around by clearing served_item_ids
		when all items are exhausted.

		Returns:
			Tuple of (questions, has_repeat). has_repeat is True if any
			returned row has priority > 0 (previously seen).
		"""
		lesson_placeholders = ", ".join(["%s"] * len(accessible_lessons))
		where_clause = f"ri.subject = %s AND ri.lesson IN ({lesson_placeholders}) AND ri.topic = %s"
		params: list = [subject_id, *accessible_lessons, topic_id]

		# Exclude already-served items from this session
		served_exclude_params: list = []
		if served_item_ids:
			served_placeholders = ", ".join(["%s"] * len(served_item_ids))
			where_clause += f" AND ri.item_id NOT IN ({served_placeholders})"
			served_exclude_params = list(served_item_ids)

		priority_case = """
			CASE
				WHEN pl.item_id IS NULL THEN 0
				ELSE 1
			END
		"""

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

		select_params = [player_id, *params, *served_exclude_params, limit]

		try:
			rows = await self.frappe.call(
				"memora_admin.api.practice.execute_practice_query",
				{"sql": select_sql, "params": select_params},
			)
		except Exception as e:
			logger.error(
				"practice_topic_select_failed",
				player_id=player_id,
				topic_id=topic_id,
				error=str(e),
			)
			return [], False

		if not rows:
			return [], False

		has_repeat = any(row.get("priority", 0) > 0 for row in rows)
		questions = [self._build_practice_question(row) for row in rows]
		return questions, has_repeat

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
