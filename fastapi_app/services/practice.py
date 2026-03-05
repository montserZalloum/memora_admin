"""Practice Arena service for hierarchy browsing and session management."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog
from redis.exceptions import LockError

from fastapi_app.core.coalesce import CoalescingLockPool
from fastapi_app.core.config import Settings
from fastapi_app.core.redis_keys import (
	practice_hierarchy_meta_key,
	practice_scope_cache_key,
	practice_session_lock_key,
	practice_session_key,
	practice_served_items_key,
)
from fastapi_app.models.practice import (
	PracticeBatchResponse,
	PracticeHierarchyResponse,
	PracticeQuestion,
	PracticeSubmitAndContinueResponse,
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
PRACTICE_SESSION_SCHEMA_VERSION = 4
PRACTICE_SCOPE_CACHE_TTL = 60
PREFETCHED_NEXT_BATCH_FIELD = "prefetched_next_batch"
SESSION_LOCK_TIMEOUT = 30
SESSION_LOCK_BLOCKING_TIMEOUT = 5

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


class PracticeSessionBusyError(Exception):
	"""Raised when another request is mutating the active practice session."""


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


class DuplicateBatchItemsError(Exception):
	"""Raised when a submit payload contains the same item_id more than once."""

	def __init__(self, duplicate_ids: list[str]):
		self.duplicate_ids = duplicate_ids
		super().__init__(f"Duplicate item_ids in submit payload: {duplicate_ids[:5]}")


class PracticeSelectionUnavailableError(Exception):
	"""Raised when question selection cannot reach the backing data store."""


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
	if not counts or batch_size <= 0:
		return {}

	positive_counts = {topic_id: count for topic_id, count in counts.items() if count > 0}
	if not positive_counts:
		return {}

	# Single topic — skip proportional logic
	if len(positive_counts) == 1:
		topic_id = next(iter(positive_counts))
		return {topic_id: min(batch_size, positive_counts[topic_id])}

	sorted_topics = sorted(positive_counts.items(), key=lambda item: (-item[1], item[0]))

	# When the selection spans more topics than the batch can hold, choose the
	# largest topics first and cap the batch at one item per topic.
	if len(sorted_topics) >= batch_size:
		return {topic_id: 1 for topic_id, _count in sorted_topics[:batch_size]}

	quotas: dict[str, int] = {topic_id: 1 for topic_id, _count in sorted_topics}
	base_remaining = batch_size - len(sorted_topics)
	if base_remaining <= 0:
		return quotas

	extra_capacity = {topic_id: max(0, count - 1) for topic_id, count in sorted_topics}
	total_extra_capacity = sum(extra_capacity.values())
	if total_extra_capacity == 0:
		return quotas

	extra_allocations: dict[str, int] = {}
	remainders: list[tuple[float, int, str]] = []
	allocated = 0

	for topic_id, count in sorted_topics:
		capacity = extra_capacity[topic_id]
		if capacity <= 0:
			extra_allocations[topic_id] = 0
			continue
		exact_share = base_remaining * capacity / total_extra_capacity
		extra = min(capacity, int(exact_share))
		extra_allocations[topic_id] = extra
		allocated += extra
		remainders.append((exact_share - extra, count, topic_id))

	for topic_id, extra in extra_allocations.items():
		quotas[topic_id] += extra

	remaining = base_remaining - allocated

	if remaining > 0 and remainders:
		for _fraction, _count, topic_id in sorted(
			remainders,
			key=lambda item: (-item[0], -item[1], item[2]),
		):
			if remaining <= 0:
				break
			room = positive_counts[topic_id] - quotas[topic_id]
			if room <= 0:
				continue
			quotas[topic_id] += 1
			remaining -= 1

	if remaining > 0:
		for topic_id, _count in sorted_topics:
			if remaining <= 0:
				break
			room = positive_counts[topic_id] - quotas[topic_id]
			if room <= 0:
				continue
			take = min(room, remaining)
			quotas[topic_id] += take
			remaining -= take

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
		self._active_session_guards: set[str] = set()

	@staticmethod
	def _parse_session_schema_version(raw_schema_version: str | None) -> int:
		"""Parse a stored schema version, defaulting to the oldest legacy format."""
		try:
			return int(raw_schema_version) if raw_schema_version is not None else 1
		except (TypeError, ValueError):
			return 1

	@staticmethod
	def _parse_session_counter(raw_value: str | None) -> int:
		"""Parse a non-negative counter stored in Redis session state."""
		try:
			return max(int(raw_value or "0"), 0)
		except (TypeError, ValueError):
			return 0

	@staticmethod
	def _parse_session_topic_counts(raw_value: str | None) -> dict[str, int] | None:
		"""Parse cached per-topic counts from Redis, returning None when unavailable."""
		if not raw_value:
			return None

		try:
			loaded = json.loads(raw_value)
		except (json.JSONDecodeError, TypeError):
			return None

		if not isinstance(loaded, dict):
			return None

		topic_counts: dict[str, int] = {}
		for topic_id, raw_count in loaded.items():
			if not isinstance(topic_id, str) or not topic_id:
				continue
			try:
				count = int(raw_count)
			except (TypeError, ValueError):
				continue
			if count > 0:
				topic_counts[topic_id] = count

		return topic_counts or None

	@staticmethod
	def _build_scope_cache_key(
		player_id: str,
		subject_id: str,
		accessible_lessons: list[str],
		selected_topics: list[str],
	) -> str:
		"""Build a stable Redis key for short-lived start-scope count caching."""
		digest = hashlib.sha1()
		for value in (player_id, subject_id):
			digest.update(value.encode("utf-8"))
			digest.update(b"\0")
		for lesson_id in sorted(set(accessible_lessons)):
			digest.update(lesson_id.encode("utf-8"))
			digest.update(b"\0")
		digest.update(b"|")
		for topic_id in sorted(set(selected_topics)):
			digest.update(topic_id.encode("utf-8"))
			digest.update(b"\0")
		return practice_scope_cache_key(player_id, digest.hexdigest())

	async def _load_scope_cache(
		self,
		cache_key: str,
	) -> tuple[dict[str, int], int] | None:
		"""Load cached topic counts for a resolved practice start scope."""
		raw_payload = await self.redis.get(cache_key)
		if not raw_payload:
			return None

		try:
			payload = json.loads(raw_payload)
		except (json.JSONDecodeError, TypeError):
			return None

		if not isinstance(payload, dict):
			return None

		raw_topic_counts = payload.get("topic_counts")
		if not isinstance(raw_topic_counts, dict):
			return None

		topic_counts = self._parse_session_topic_counts(json.dumps(raw_topic_counts))
		if not topic_counts:
			return None

		total_available = self._parse_session_counter(str(payload.get("total_available", 0)))
		if total_available <= 0:
			total_available = sum(topic_counts.values())

		return topic_counts, total_available

	async def _store_scope_cache(
		self,
		cache_key: str,
		topic_counts: dict[str, int] | None,
		total_available: int,
	) -> None:
		"""Cache short-lived topic counts for a resolved practice start scope."""
		if not topic_counts:
			return

		await self.redis.set(
			cache_key,
			json.dumps(
				{
					"topic_counts": topic_counts,
					"total_available": max(total_available, 0),
				}
			),
			ex=PRACTICE_SCOPE_CACHE_TTL,
		)

	@staticmethod
	def _serialize_prefetched_batch(
		batch_response: PracticeBatchResponse,
		session_all_seen_mode: bool,
		topic_counts: dict[str, int] | None,
		session_total_available: int,
	) -> str:
		"""Serialize a computed next batch so /continue can consume it without Frappe."""
		return json.dumps(
			{
				"batch_seq": batch_response.batch_seq,
				"questions": [q.model_dump() for q in batch_response.questions],
				"total_available": batch_response.total_available,
				"all_seen_warning": batch_response.all_seen_warning,
				"all_seen_mode": session_all_seen_mode,
				"topic_counts": topic_counts or {},
				"session_total_available": max(session_total_available, 0),
			}
		)

	def _parse_prefetched_batch(
		self,
		raw_payload: str | None,
	) -> tuple[PracticeBatchResponse, bool, dict[str, int] | None, int] | None:
		"""Parse a prefetched next-batch payload stored in the session hash."""
		if not raw_payload:
			return None

		try:
			payload = json.loads(raw_payload)
		except (json.JSONDecodeError, TypeError):
			return None

		if not isinstance(payload, dict):
			return None

		raw_questions = payload.get("questions", [])
		if not isinstance(raw_questions, list):
			return None

		try:
			questions = [PracticeQuestion.model_validate(question) for question in raw_questions]
			batch_response = PracticeBatchResponse(
				session_active=True,
				batch_seq=max(int(payload.get("batch_seq", 0)), 0),
				questions=questions,
				total_available=max(int(payload.get("total_available", 0)), 0),
				all_seen_warning=bool(payload.get("all_seen_warning", False)),
			)
		except (TypeError, ValueError):
			return None

		topic_counts = self._parse_session_topic_counts(json.dumps(payload.get("topic_counts", {})))
		session_total_available = self._parse_session_counter(str(payload.get("session_total_available", 0)))
		if topic_counts and session_total_available <= 0:
			session_total_available = sum(topic_counts.values())

		return batch_response, bool(payload.get("all_seen_mode", False)), topic_counts, session_total_available

	@staticmethod
	def _parse_cached_submit_and_continue(
		raw_payload: str | None,
	) -> PracticeSubmitAndContinueResponse | None:
		"""Parse a cached submit+continue response."""
		if not raw_payload:
			return None

		try:
			payload = json.loads(raw_payload)
		except (json.JSONDecodeError, TypeError):
			return None

		if not isinstance(payload, dict):
			return None

		try:
			return PracticeSubmitAndContinueResponse.model_validate(payload)
		except Exception:
			return None

	async def _activate_next_batch(
		self,
		player_id: str,
		session_key: str,
		served_items_key: str,
		batch_response: PracticeBatchResponse,
		session_all_seen_mode: bool,
		topic_counts: dict[str, int] | None,
		session_total_available: int,
		source: str = "computed",
	) -> PracticeBatchResponse:
		"""Promote a prepared next batch into the active session."""
		batch_ids = [q.item_id for q in batch_response.questions]
		pipe = self.redis.pipeline()
		pipe.hset(
			session_key,
			mapping={
				"batch_seq": str(batch_response.batch_seq),
				"all_seen_mode": "1" if session_all_seen_mode else "0",
				f"batch_{batch_response.batch_seq}_item_ids": json.dumps(batch_ids),
			},
		)
		if topic_counts is not None:
			pipe.hset(
				session_key,
				mapping={
					"topic_counts": json.dumps(topic_counts),
					"total_available": str(session_total_available),
				},
			)
		pipe.hdel(session_key, PREFETCHED_NEXT_BATCH_FIELD)
		if batch_ids:
			pipe.sadd(served_items_key, *batch_ids)
		pipe.expire(session_key, self.config.practice_session_ttl)
		pipe.expire(served_items_key, self.config.practice_session_ttl)
		await pipe.execute()

		logger.bind(player_id=player_id).info(
			"practice_next_batch_activated",
			batch_seq=batch_response.batch_seq,
			item_count=len(batch_response.questions),
			total_available=batch_response.total_available,
			all_seen=batch_response.all_seen_warning,
			source=source,
		)
		return batch_response

	async def _load_served_item_ids(
		self,
		player_id: str,
		schema_version: int,
		raw_legacy_value: str | None = None,
	) -> list[str]:
		"""Load served item history from the active storage format.

		Schema v3+ stores served IDs in a dedicated Redis SET.
		Older sessions still fall back to the hash field for compatibility.
		"""
		if schema_version >= 3:
			served_ids = await self.redis.smembers(practice_served_items_key(player_id))
			return [item_id for item_id in served_ids if item_id]

		if not raw_legacy_value:
			return []

		try:
			loaded_ids = json.loads(raw_legacy_value)
		except (json.JSONDecodeError, TypeError):
			return []

		if not isinstance(loaded_ids, list):
			return []

		return [item_id for item_id in loaded_ids if isinstance(item_id, str) and item_id]

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

	@asynccontextmanager
	async def _session_mutation_guard(self, player_id: str):
		"""Serialize session mutations per player to keep submit/continue idempotent."""
		if player_id in self._active_session_guards:
			yield
			return

		lock = self.redis.lock(
			practice_session_lock_key(player_id),
			timeout=SESSION_LOCK_TIMEOUT,
			blocking_timeout=SESSION_LOCK_BLOCKING_TIMEOUT,
		)
		acquired = False
		try:
			acquired = await lock.acquire()
		except LockError as e:
			logger.warning("practice_session_lock_acquire_failed", player_id=player_id, error=str(e))
			raise PracticeSessionBusyError() from e

		if not acquired:
			raise PracticeSessionBusyError()

		self._active_session_guards.add(player_id)
		try:
			yield
		finally:
			self._active_session_guards.discard(player_id)
			try:
				await lock.release()
			except LockError:
				logger.warning("practice_session_lock_release_failed", player_id=player_id)

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
		async with self._session_mutation_guard(player_id):
			# Load hierarchy
			hier = await self.hierarchy.get_hierarchy(subject_id)
			if not hier:
				raise PracticeSubjectNotFoundError(subject_id)

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
			scope_cache_key = self._build_scope_cache_key(
				player_id,
				subject_id,
				lesson_ids,
				selected_topic_ids,
			)

			# Select first batch of questions
			batch_size = self.config.practice_session_size
			session_topic_counts: dict[str, int] | None = None
			cached_scope = await self._load_scope_cache(scope_cache_key)
			if cached_scope is not None:
				session_topic_counts, total_available = cached_scope
				questions, _, any_repeat = await self._select_questions(
					player_id=player_id,
					subject_id=subject_id,
					accessible_lessons=lesson_ids,
					selected_topics=selected_topic_ids,
					served_item_ids=[],
					batch_size=batch_size,
					topic_counts=session_topic_counts,
				)
				if not questions and total_available > 0:
					await self.redis.delete(scope_cache_key)
					session_topic_counts = None

			if session_topic_counts is None and self.config.practice_batched_topic_select_enabled:
				prepared_batch = await self._prepare_batched_question_data(
					player_id=player_id,
					subject_id=subject_id,
					accessible_lessons=lesson_ids,
					selected_topics=selected_topic_ids,
					served_item_ids=[],
					batch_size=batch_size,
				)
				if prepared_batch is not None:
					session_topic_counts, candidate_rows, _session_served_count = prepared_batch
					questions, total_available, any_repeat = self._allocate_batched_questions(
						topic_counts=session_topic_counts,
						candidate_rows=candidate_rows,
						batch_size=batch_size,
					)
				else:
					session_topic_counts = await self._count_items_per_topic(
						subject_id,
						lesson_ids,
						selected_topic_ids,
					)
					questions, total_available, any_repeat = await self._select_questions(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=lesson_ids,
						selected_topics=selected_topic_ids,
						served_item_ids=[],
						batch_size=batch_size,
						topic_counts=session_topic_counts,
					)
			elif session_topic_counts is None:
				session_topic_counts = await self._count_items_per_topic(
					subject_id,
					lesson_ids,
					selected_topic_ids,
				)
				questions, total_available, any_repeat = await self._select_questions(
					player_id=player_id,
					subject_id=subject_id,
					accessible_lessons=lesson_ids,
					selected_topics=selected_topic_ids,
					served_item_ids=[],
					batch_size=batch_size,
					topic_counts=session_topic_counts,
				)

			await self._store_scope_cache(
				scope_cache_key,
				session_topic_counts,
				total_available,
			)

			all_seen = any_repeat
			served_ids = [q.item_id for q in questions]

			# Create Redis session (overwrites any existing session)
			session_key = practice_session_key(player_id)
			served_items_key = practice_served_items_key(player_id)
			now = datetime.now(timezone.utc)
			created_at = now.isoformat()
			session_started_at = now.replace(tzinfo=None).isoformat()

			session_data = {
				"subject_id": subject_id,
				"filter": filter_mode,
				"tracks": json.dumps(tracks),
				"units": json.dumps(units),
				"topics": json.dumps(topics),
				"schema_version": str(PRACTICE_SESSION_SCHEMA_VERSION),
				"batch_seq": "0",
				"batch_0_item_ids": json.dumps(served_ids),
				"accessible_lessons": json.dumps(lesson_ids),
				"selected_topics": json.dumps(selected_topic_ids),
				"created_at": created_at,
				"session_started_at": session_started_at,
				"all_seen_mode": "0",
				"session_served_count": "0",
			}
			if session_topic_counts is not None:
				session_data["topic_counts"] = json.dumps(session_topic_counts)
				session_data["total_available"] = str(total_available)

			pipe = self.redis.pipeline()
			pipe.delete(session_key)
			pipe.delete(served_items_key)
			pipe.hset(session_key, mapping=session_data)
			if served_ids:
				pipe.sadd(served_items_key, *served_ids)
			pipe.expire(session_key, self.config.practice_session_ttl)
			pipe.expire(served_items_key, self.config.practice_session_ttl)
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

	async def _compute_next_batch_response(
		self,
		player_id: str,
		current_seq: int,
		schema_version: int,
		accessible_lessons: list[str],
		selected_topics: list[str],
		subject_id: str,
		raw_legacy_served_item_ids: str | None,
		session_started_at: str,
		session_all_seen_mode: bool,
		cached_topic_counts: dict[str, int] | None,
		cached_total_available: int,
		session_served_count: int,
	) -> tuple[PracticeBatchResponse, bool, dict[str, int] | None, int]:
		"""Compute the next batch without mutating Redis session state."""
		next_seq = current_seq + 1
		batch_size = self.config.practice_session_size
		questions: list[PracticeQuestion] = []
		total_available = 0
		all_seen = False
		used_session_exclusion = False
		next_topic_counts = cached_topic_counts
		next_total_available = cached_total_available

		if schema_version >= PRACTICE_SESSION_SCHEMA_VERSION and not session_all_seen_mode:
			prefetched_batch: tuple[dict[str, int], list[dict], int] | None = None
			if self.config.practice_batched_topic_select_enabled and session_started_at and cached_topic_counts:
				cached_quotas = _compute_topic_quotas(cached_topic_counts, batch_size)
				total_available = cached_total_available
				if cached_quotas:
					cached_batched_result = await self._select_questions_batched(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=accessible_lessons,
						served_item_ids=[],
						topic_counts=cached_topic_counts,
						quotas=cached_quotas,
						batch_size=batch_size,
						session_started_at=session_started_at,
					)
					if cached_batched_result is not None:
						questions, any_repeat = cached_batched_result
						if questions:
							all_seen = any_repeat
							used_session_exclusion = True
						elif total_available > 0 and session_served_count >= total_available:
							questions, _, _ = await self._select_questions(
								player_id=player_id,
								subject_id=subject_id,
								accessible_lessons=accessible_lessons,
								selected_topics=selected_topics,
								served_item_ids=[],
								batch_size=batch_size,
								topic_counts=cached_topic_counts,
							)
							all_seen = True
							session_all_seen_mode = True
							used_session_exclusion = True
				elif total_available == 0:
					used_session_exclusion = True

			if not used_session_exclusion and self.config.practice_batched_topic_select_enabled and session_started_at:
				prefetched_batch = await self._prepare_batched_question_data(
					player_id=player_id,
					subject_id=subject_id,
					accessible_lessons=accessible_lessons,
					selected_topics=selected_topics,
					served_item_ids=[],
					batch_size=batch_size,
					session_started_at=session_started_at,
				)

			if prefetched_batch is not None:
				topic_counts, candidate_rows, session_served_count = prefetched_batch
				total_available = sum(topic_counts.values())
				next_topic_counts = topic_counts
				next_total_available = total_available
				used_session_exclusion = True

				if total_available > 0 and session_served_count >= total_available:
					questions, _, _ = await self._select_questions(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=accessible_lessons,
						selected_topics=selected_topics,
						served_item_ids=[],
						batch_size=batch_size,
						topic_counts=topic_counts,
					)
					all_seen = True
					session_all_seen_mode = True
				else:
					questions, _, any_repeat = self._allocate_batched_questions(
						topic_counts=topic_counts,
						candidate_rows=candidate_rows,
						batch_size=batch_size,
					)
					all_seen = any_repeat

					if not questions and total_available > 0:
						questions, _, _ = await self._select_questions(
							player_id=player_id,
							subject_id=subject_id,
							accessible_lessons=accessible_lessons,
							selected_topics=selected_topics,
							served_item_ids=[],
							batch_size=batch_size,
							topic_counts=topic_counts,
						)
						all_seen = True
						session_all_seen_mode = True

		if not used_session_exclusion:
			if schema_version >= PRACTICE_SESSION_SCHEMA_VERSION and session_all_seen_mode:
				if cached_topic_counts:
					questions, total_available, _ = await self._select_questions(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=accessible_lessons,
						selected_topics=selected_topics,
						served_item_ids=[],
						batch_size=batch_size,
						topic_counts=cached_topic_counts,
					)
				else:
					questions, total_available, _ = await self._select_questions(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=accessible_lessons,
						selected_topics=selected_topics,
						served_item_ids=[],
						batch_size=batch_size,
					)
				all_seen = True
			else:
				served_item_ids = await self._load_served_item_ids(
					player_id,
					schema_version,
					raw_legacy_served_item_ids,
				)
				prefetched_batch = None
				if self.config.practice_batched_topic_select_enabled and cached_topic_counts:
					topic_counts = cached_topic_counts
					total_available = cached_total_available
					candidate_rows = await self._select_candidates_for_topics(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=accessible_lessons,
						topic_ids=list(_compute_topic_quotas(topic_counts, batch_size).keys()),
						served_item_ids=served_item_ids,
						per_topic_limit=batch_size,
					)
				elif self.config.practice_batched_topic_select_enabled:
					prefetched_batch = await self._prepare_batched_question_data(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=accessible_lessons,
						selected_topics=selected_topics,
						served_item_ids=served_item_ids,
						batch_size=batch_size,
					)

				if prefetched_batch is not None:
					topic_counts, candidate_rows, _ignored_session_served_count = prefetched_batch
					total_available = sum(topic_counts.values())
					next_topic_counts = topic_counts
					next_total_available = total_available
				else:
					topic_counts = cached_topic_counts
					if topic_counts is None:
						topic_counts = await self._count_items_per_topic(
							subject_id,
							accessible_lessons,
							selected_topics,
						)
					total_available = sum(topic_counts.values())
					next_topic_counts = topic_counts
					next_total_available = total_available
					if not self.config.practice_batched_topic_select_enabled:
						candidate_rows = None

				served_unique_ids = set(served_item_ids)
				should_wrap = total_available > 0 and len(served_unique_ids) >= total_available
				if should_wrap and served_unique_ids:
					valid_served_ids = await self._get_valid_item_ids(list(served_unique_ids))
					should_wrap = len(valid_served_ids) >= total_available

				if should_wrap:
					questions, _, _ = await self._select_questions(
						player_id=player_id,
						subject_id=subject_id,
						accessible_lessons=accessible_lessons,
						selected_topics=selected_topics,
						served_item_ids=[],
						batch_size=batch_size,
						topic_counts=topic_counts,
					)
					all_seen = True
				else:
					if candidate_rows is not None:
						questions, _, any_repeat = self._allocate_batched_questions(
							topic_counts=topic_counts,
							candidate_rows=candidate_rows,
							batch_size=batch_size,
						)
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

					if not questions and total_available > 0:
						questions, _, _ = await self._select_questions(
							player_id=player_id,
							subject_id=subject_id,
							accessible_lessons=accessible_lessons,
							selected_topics=selected_topics,
							served_item_ids=[],
							batch_size=batch_size,
							topic_counts=topic_counts,
						)
						all_seen = True

		return (
			PracticeBatchResponse(
				session_active=True,
				batch_seq=next_seq,
				questions=questions,
				total_available=total_available,
				all_seen_warning=all_seen,
			),
			session_all_seen_mode,
			next_topic_counts,
			next_total_available,
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

		Validates that submitted item_ids were actually served in the batch.
		Off-batch items are rejected.

		Raises:
			NoActiveSessionError: If no active session exists
			BatchSeqMismatchError: If batch_seq is ahead of current
			OffBatchItemError: If submitted items weren't served in the batch
			InvalidSessionStateError: If a current-format session is malformed
		"""
		log = logger.bind(player_id=player_id, batch_seq=batch_seq)
		session_key = practice_session_key(player_id)
		async with self._session_mutation_guard(player_id):
			submit_started = time.perf_counter()
			submitted_marker = f"submitted_{batch_seq}"
			batch_items_key = f"batch_{batch_seq}_item_ids"
			session_read_started = time.perf_counter()
			raw_session = await self.redis.hmget(
				session_key,
				"batch_seq",
				"schema_version",
				batch_items_key,
				submitted_marker,
				"accessible_lessons",
				"selected_topics",
				"subject_id",
				"served_item_ids",
				"session_started_at",
				"all_seen_mode",
				"topic_counts",
				"total_available",
				"session_served_count",
			)
			session_read_ms = round((time.perf_counter() - session_read_started) * 1000, 2)

			if not raw_session or all(value is None for value in raw_session):
				log.info("practice_session_expired")
				raise NoActiveSessionError()

			(
				raw_batch_seq,
				raw_schema_version,
				raw_batch_ids,
				cached_result,
				raw_accessible_lessons,
				raw_selected_topics,
				raw_subject_id,
				raw_legacy_served_item_ids,
				raw_session_started_at,
				raw_all_seen_mode,
				raw_topic_counts,
				raw_total_available,
				raw_session_served_count,
			) = raw_session
			current_seq = int(raw_batch_seq or "0")

			# Check for future batch_seq (skipping batches)
			if batch_seq > current_seq:
				raise BatchSeqMismatchError(expected=current_seq, received=batch_seq)

			# Check for duplicate submission — return cached original response
			if cached_result:
				log.info(
					"practice_batch_duplicate",
					session_read_ms=session_read_ms,
					total_ms=round((time.perf_counter() - submit_started) * 1000, 2),
				)
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
					accuracy_percent=round(legacy_correct / legacy_total * 100, 1)
					if legacy_total > 0
					else 0.0,
					is_duplicate=True,
				)

			# Validate submitted items were actually served in THIS specific batch.
			# Legacy sessions (pre-deploy) lack both schema_version and per-batch
			# keys. Only those sessions skip validation to preserve rollout safety.
			# Current-format sessions must fail closed if required state is missing.
			validation_started = time.perf_counter()
			schema_version = self._parse_session_schema_version(raw_schema_version)
			is_legacy_session = schema_version < PRACTICE_SESSION_SCHEMA_VERSION
			submitted_ids = [r.get("item_id", "") for r in results]
			seen_ids: set[str] = set()
			duplicate_ids: list[str] = []
			for item_id in submitted_ids:
				if item_id in seen_ids and item_id not in duplicate_ids:
					duplicate_ids.append(item_id)
				seen_ids.add(item_id)

			if duplicate_ids:
				log.warning(
					"practice_duplicate_submit_items",
					duplicate_count=len(duplicate_ids),
					duplicate_ids=duplicate_ids[:5],
				)
				raise DuplicateBatchItemsError(duplicate_ids)

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
			validation_ms = round((time.perf_counter() - validation_started) * 1000, 2)

			# UPSERT Practice Log for each result
			now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
			correct_count = 0
			total_count = 0
			upsert_ms = 0.0

			if self.frappe and results:
				# The Frappe-side upsert returns the persisted item_ids.
				upsert_started = time.perf_counter()
				accepted_result = await self.frappe.call(
					"memora_admin.api.practice.upsert_practice_results",
					{"player_id": player_id, "results": results, "seen_at": now},
				)
				upsert_ms = round((time.perf_counter() - upsert_started) * 1000, 2)
				if accepted_result is None:
					accepted_ids = {item_id for item_id in submitted_ids if item_id}
				elif isinstance(accepted_result, list) and accepted_result and isinstance(
					accepted_result[0], dict
				):
					accepted_ids = {
						row["item_id"] for row in accepted_result if isinstance(row, dict) and row.get("item_id")
					}
				else:
					accepted_ids = {item_id for item_id in accepted_result if item_id}

				skipped_ids = set(submitted_ids) - accepted_ids

				if skipped_ids:
					log.warning(
						"practice_items_deleted_during_session",
						skipped_count=len(skipped_ids),
						skipped_ids=list(skipped_ids),
					)

				for r in results:
					if r.get("item_id", "") not in accepted_ids:
						continue
					is_correct = r.get("is_correct", False)

					if is_correct:
						correct_count += 1
					total_count += 1
			else:
				# Count without DB write (no frappe client)
				for r in results:
					total_count += 1
					if r.get("is_correct"):
						correct_count += 1

			accuracy = round(correct_count / total_count * 100, 1) if total_count > 0 else 0.0
			prefetched_payload: str | None = None

			if raw_accessible_lessons is not None and raw_selected_topics is not None and raw_subject_id:
				accessible_lessons = json.loads(raw_accessible_lessons or "[]")
				selected_topics = json.loads(raw_selected_topics or "[]")
				subject_id = raw_subject_id or ""
				session_started_at = raw_session_started_at or ""
				session_all_seen_mode = raw_all_seen_mode == "1"
				cached_topic_counts = self._parse_session_topic_counts(raw_topic_counts)
				cached_total_available = self._parse_session_counter(raw_total_available)
				if cached_topic_counts and cached_total_available <= 0:
					cached_total_available = sum(cached_topic_counts.values())
				next_session_served_count = self._parse_session_counter(raw_session_served_count) + total_count
				try:
					(
						prefetched_batch,
						prefetched_all_seen_mode,
						prefetched_topic_counts,
						prefetched_total_available,
					) = await self._compute_next_batch_response(
						player_id=player_id,
						current_seq=current_seq,
						schema_version=schema_version,
						accessible_lessons=accessible_lessons,
						selected_topics=selected_topics,
						subject_id=subject_id,
						raw_legacy_served_item_ids=raw_legacy_served_item_ids,
						session_started_at=session_started_at,
						session_all_seen_mode=session_all_seen_mode,
						cached_topic_counts=cached_topic_counts,
						cached_total_available=cached_total_available,
						session_served_count=next_session_served_count,
					)
					prefetched_payload = self._serialize_prefetched_batch(
						batch_response=prefetched_batch,
						session_all_seen_mode=prefetched_all_seen_mode,
						topic_counts=prefetched_topic_counts,
						session_total_available=prefetched_total_available,
					)
				except PracticeSelectionUnavailableError:
					log.warning("practice_prefetch_next_batch_failed")

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
			cache_write_started = time.perf_counter()
			pipe.hset(session_key, submitted_marker, cached_payload)
			pipe.hdel(session_key, PREFETCHED_NEXT_BATCH_FIELD)
			if prefetched_payload is not None:
				pipe.hset(session_key, PREFETCHED_NEXT_BATCH_FIELD, prefetched_payload)
			if total_count > 0:
				pipe.hincrby(session_key, "session_served_count", total_count)
			pipe.expire(session_key, self.config.practice_session_ttl)
			await pipe.execute()
			cache_write_ms = round((time.perf_counter() - cache_write_started) * 1000, 2)

			log.info(
				"practice_batch_submitted",
				correct_count=correct_count,
				total_count=total_count,
				accuracy_percent=accuracy,
				session_read_ms=session_read_ms,
				validation_ms=validation_ms,
				upsert_ms=upsert_ms,
				cache_write_ms=cache_write_ms,
				total_ms=round((time.perf_counter() - submit_started) * 1000, 2),
			)

			return PracticeSubmitResponse(
				accepted=True,
				batch_seq=batch_seq,
				correct_count=correct_count,
				total_count=total_count,
				accuracy_percent=accuracy,
				is_duplicate=False,
			)

	async def submit_and_continue_batch(
		self,
		player_id: str,
		batch_seq: int,
		results: list[dict],
	) -> PracticeSubmitAndContinueResponse:
		"""Submit the current batch and advance to the next batch atomically."""
		session_key = practice_session_key(player_id)
		combined_marker = f"submitted_continue_{batch_seq}"

		async with self._session_mutation_guard(player_id):
			cached_combined = self._parse_cached_submit_and_continue(
				await self.redis.hget(session_key, combined_marker)
			)
			if cached_combined is not None:
				cached_combined.submit.is_duplicate = True
				return cached_combined

			submit_response = await self.submit_batch(player_id, batch_seq, results)

			if submit_response.is_duplicate:
				cached_combined = self._parse_cached_submit_and_continue(
					await self.redis.hget(session_key, combined_marker)
				)
				if cached_combined is not None:
					cached_combined.submit.is_duplicate = True
					return cached_combined

			next_batch = await self.continue_session(player_id)
			response = PracticeSubmitAndContinueResponse(submit=submit_response, next_batch=next_batch)
			await self.redis.hset(session_key, combined_marker, response.model_dump_json())
			await self.redis.expire(session_key, self.config.practice_session_ttl)
			return response

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
		served_items_key = practice_served_items_key(player_id)
		async with self._session_mutation_guard(player_id):
			raw_session = await self.redis.hmget(
				session_key,
				"batch_seq",
				"schema_version",
				"accessible_lessons",
				"selected_topics",
				"subject_id",
				"served_item_ids",
				"session_started_at",
				"all_seen_mode",
				"topic_counts",
				"total_available",
				"session_served_count",
				PREFETCHED_NEXT_BATCH_FIELD,
			)

			if not raw_session or all(value is None for value in raw_session):
				log.info("practice_session_expired")
				raise NoActiveSessionError()

			(
				raw_batch_seq,
				raw_schema_version,
				raw_accessible_lessons,
				raw_selected_topics,
				subject_id,
				raw_legacy_served_item_ids,
				raw_session_started_at,
				raw_all_seen_mode,
				raw_topic_counts,
				raw_total_available,
				raw_session_served_count,
				raw_prefetched_next_batch,
			) = raw_session
			schema_version = self._parse_session_schema_version(raw_schema_version)
			current_seq = int(raw_batch_seq or "0")

			# Verify current batch was submitted before serving the next one.
			submitted_marker = f"submitted_{current_seq}"
			if not await self.redis.hget(session_key, submitted_marker):
				raise PreviousBatchNotSubmittedError(current_seq)

			prefetched_batch = self._parse_prefetched_batch(raw_prefetched_next_batch)
			if prefetched_batch is not None:
				(
					batch_response,
					prefetched_all_seen_mode,
					prefetched_topic_counts,
					prefetched_total_available,
				) = prefetched_batch
				if batch_response.batch_seq == current_seq + 1:
					activated_batch = await self._activate_next_batch(
						player_id=player_id,
						session_key=session_key,
						served_items_key=served_items_key,
						batch_response=batch_response,
						session_all_seen_mode=prefetched_all_seen_mode,
						topic_counts=prefetched_topic_counts,
						session_total_available=prefetched_total_available,
						source="prefetched",
					)
					log.info(
						"practice_session_continued",
						batch_seq=activated_batch.batch_seq,
						item_count=len(activated_batch.questions),
						total_available=activated_batch.total_available,
						all_seen=activated_batch.all_seen_warning,
					)
					return activated_batch
				await self.redis.hdel(session_key, PREFETCHED_NEXT_BATCH_FIELD)

			accessible_lessons = json.loads(raw_accessible_lessons or "[]")
			selected_topics = json.loads(raw_selected_topics or "[]")
			subject_id = subject_id or ""
			session_started_at = raw_session_started_at or ""
			session_all_seen_mode = raw_all_seen_mode == "1"
			cached_topic_counts = self._parse_session_topic_counts(raw_topic_counts)
			cached_total_available = self._parse_session_counter(raw_total_available)
			if cached_topic_counts and cached_total_available <= 0:
				cached_total_available = sum(cached_topic_counts.values())
			session_served_count = self._parse_session_counter(raw_session_served_count)
			(
				batch_response,
				next_session_all_seen_mode,
				next_topic_counts,
				next_total_available,
			) = await self._compute_next_batch_response(
				player_id=player_id,
				current_seq=current_seq,
				schema_version=schema_version,
				accessible_lessons=accessible_lessons,
				selected_topics=selected_topics,
				subject_id=subject_id,
				raw_legacy_served_item_ids=raw_legacy_served_item_ids,
				session_started_at=session_started_at,
				session_all_seen_mode=session_all_seen_mode,
				cached_topic_counts=cached_topic_counts,
				cached_total_available=cached_total_available,
				session_served_count=session_served_count,
			)
			activated_batch = await self._activate_next_batch(
				player_id=player_id,
				session_key=session_key,
				served_items_key=served_items_key,
				batch_response=batch_response,
				session_all_seen_mode=next_session_all_seen_mode,
				topic_counts=next_topic_counts,
				session_total_available=next_total_available,
				source="computed",
			)
			log.info(
				"practice_session_continued",
				batch_seq=activated_batch.batch_seq,
				item_count=len(activated_batch.questions),
				total_available=activated_batch.total_available,
				all_seen=activated_batch.all_seen_warning,
			)
			return activated_batch

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
		"""Count available Review Items per topic for the selected lesson scope."""
		if not accessible_lessons or not self.frappe:
			return {}

		try:
			counts = await self.frappe.call(
				"memora_admin.api.practice.count_practice_items_per_topic",
				{
					"subject_id": subject_id,
					"accessible_lessons": accessible_lessons,
					"selected_topics": selected_topics,
				},
			)
		except Exception as e:
			logger.error("practice_count_per_topic_failed", error=str(e))
			raise PracticeSelectionUnavailableError() from e

		if not counts:
			return {}

		if isinstance(counts, dict):
			return {topic_id: int(count) for topic_id, count in counts.items()}

		return {row["topic"]: row["cnt"] for row in counts}

	async def _prepare_batched_question_data(
		self,
		player_id: str,
		subject_id: str,
		accessible_lessons: list[str],
		selected_topics: list[str],
		served_item_ids: list[str],
		batch_size: int,
		session_started_at: str | None = None,
	) -> tuple[dict[str, int], list[dict], int] | None:
		"""Fetch per-topic counts plus candidate rows in one Frappe round-trip."""
		if not accessible_lessons or not self.frappe:
			return {}, [], 0

		try:
			result = await self.frappe.call(
				"memora_admin.api.practice.prepare_practice_batch",
				{
					"player_id": player_id,
					"subject_id": subject_id,
					"accessible_lessons": accessible_lessons,
					"selected_topics": selected_topics,
					"served_item_ids": served_item_ids,
					"per_topic_limit": batch_size,
					"max_topics": batch_size,
					"session_started_at": session_started_at,
				},
			)
		except Exception as e:
			logger.error(
				"practice_batched_prepare_failed",
				player_id=player_id,
				topic_count=len(selected_topics),
				error=str(e),
			)
			return None

		if result is None:
			return None
		if not result:
			return {}, [], 0

		raw_topic_counts = result.get("topic_counts", {}) if isinstance(result, dict) else {}
		if isinstance(raw_topic_counts, dict):
			topic_counts = {topic_id: int(count) for topic_id, count in raw_topic_counts.items()}
		else:
			topic_counts = {
				row["topic"]: int(row["cnt"])
				for row in raw_topic_counts
				if isinstance(row, dict) and row.get("topic") and row.get("cnt") is not None
			}

		raw_candidate_rows = result.get("candidate_rows", []) if isinstance(result, dict) else []
		candidate_rows = raw_candidate_rows if isinstance(raw_candidate_rows, list) else []
		raw_session_served_count = result.get("session_served_count", 0) if isinstance(result, dict) else 0
		try:
			session_served_count = int(raw_session_served_count or 0)
		except (TypeError, ValueError):
			session_served_count = 0
		return topic_counts, candidate_rows, session_served_count

	def _allocate_batched_questions(
		self,
		topic_counts: dict[str, int],
		candidate_rows: list[dict],
		batch_size: int,
	) -> tuple[list[PracticeQuestion], int, bool]:
		"""Build a batch from a pre-fetched batched candidate pool."""
		total_available = sum(topic_counts.values())
		if total_available == 0:
			return [], 0, False

		quotas = _compute_topic_quotas(topic_counts, batch_size)
		if not quotas:
			return [], total_available, False

		questions, any_repeat = self._allocate_questions_from_candidates(
			candidate_rows=candidate_rows,
			quotas=quotas,
			topic_counts=topic_counts,
		)
		return questions, total_available, any_repeat

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

		if self.config.practice_batched_topic_select_enabled and topic_counts is None:
			prepared_batch = await self._prepare_batched_question_data(
				player_id=player_id,
				subject_id=subject_id,
				accessible_lessons=accessible_lessons,
				selected_topics=selected_topics,
				served_item_ids=served_item_ids,
				batch_size=batch_size,
			)
			if prepared_batch is not None:
				prepared_counts, candidate_rows, _session_served_count = prepared_batch
				return self._allocate_batched_questions(
					topic_counts=prepared_counts,
					candidate_rows=candidate_rows,
					batch_size=batch_size,
				)

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
		session_started_at: str | None = None,
	) -> tuple[list[PracticeQuestion], bool] | None:
		"""Fetch per-topic candidates in one query, then preserve allocation in Python."""
		candidate_rows = await self._select_candidates_for_topics(
			player_id=player_id,
			subject_id=subject_id,
			accessible_lessons=accessible_lessons,
			topic_ids=list(quotas.keys()),
			served_item_ids=served_item_ids,
			per_topic_limit=batch_size,
			session_started_at=session_started_at,
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
		session_started_at: str | None = None,
	) -> list[dict] | None:
		"""Fetch the top N candidate rows per topic in a single SQL round-trip."""
		if not topic_ids:
			return []

		params = {
			"player_id": player_id,
			"subject_id": subject_id,
			"accessible_lessons": accessible_lessons,
			"topic_ids": topic_ids,
			"served_item_ids": served_item_ids,
			"per_topic_limit": per_topic_limit,
		}
		if session_started_at:
			params["session_started_at"] = session_started_at

		try:
			return await self.frappe.call(
				"memora_admin.api.practice.select_practice_candidates",
				params,
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
		try:
			rows = await self.frappe.call(
				"memora_admin.api.practice.select_practice_questions_for_topic",
				{
					"player_id": player_id,
					"subject_id": subject_id,
					"accessible_lessons": accessible_lessons,
					"topic_id": topic_id,
					"served_item_ids": served_item_ids,
					"limit": limit,
				},
			)
		except Exception as e:
			logger.error(
				"practice_topic_select_failed",
				player_id=player_id,
				topic_id=topic_id,
				error=str(e),
			)
			raise PracticeSelectionUnavailableError() from e

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
		try:
			result = await self.frappe.call(
				"memora_admin.api.practice.get_existing_practice_item_ids",
				{"item_ids": list(item_ids)},
			)
			if not result:
				return set()
			if isinstance(result, list) and result and isinstance(result[0], dict):
				return {row["item_id"] for row in result if row.get("item_id")}
			return {item_id for item_id in result if item_id}
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
