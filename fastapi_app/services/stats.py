"""Stats caching service for pre-computed progress statistics."""

import asyncio
import random

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import stats_key as _stats_key_fn
from fastapi_app.models.progress import SubjectHierarchy

logger = structlog.get_logger()

# Process-local semaphore. With N uvicorn workers, effective system-wide
# limit is N x MAX_CONCURRENT_STATS_RECOMPUTES (e.g., 4 workers x 30 = 120).
# Sufficient for single-server deployment.
MAX_CONCURRENT_STATS_RECOMPUTES = 30
_stats_recompute_semaphore: asyncio.Semaphore | None = None

# Process-local per-key locks for stats recompute coalescing.
# Prevents thundering herd when multiple requests trigger cold-start recompute for the same key.
# Soft-bounded to _MAX_COMPUTE_LOCKS: unlocked entries are pruned on overflow. The dict may
# temporarily exceed the cap by the number of concurrent unique in-flight keys (each holds its
# per-key lock before reaching the semaphore). In practice this is bounded by uvicorn's worker
# concurrency. The dict shrinks back on the next insertion once those requests complete.
_compute_locks: dict[str, asyncio.Lock] = {}
_MAX_COMPUTE_LOCKS = 10_000


def _get_compute_lock(key: str) -> asyncio.Lock:
	"""Get or create a per-key asyncio.Lock for compute coalescing.

	Uses setdefault for atomicity: concurrent callers for the same key
	always get the same Lock instance. Pruning of unlocked entries runs
	after insertion to keep the dict near _MAX_COMPUTE_LOCKS. The new
	key is excluded from pruning so same-key coalescing is never broken.
	The dict may temporarily exceed the cap by the number of concurrent
	unique in-flight keys (per-key locks are acquired before the
	semaphore). Bounded by uvicorn worker concurrency in practice.
	"""
	lock = _compute_locks.get(key)
	if lock is not None:
		return lock
	# setdefault is atomic: if another call raced us, we get their lock
	lock = _compute_locks.setdefault(key, asyncio.Lock())
	# Prune unlocked entries if over capacity, but never the key we just inserted
	if len(_compute_locks) > _MAX_COMPUTE_LOCKS:
		to_remove = [k for k, v in _compute_locks.items() if k != key and not v.locked()]
		for k in to_remove:
			_compute_locks.pop(k, None)
	return lock


def get_stats_recompute_semaphore() -> asyncio.Semaphore:
	"""Get or create the per-process stats recompute semaphore."""
	global _stats_recompute_semaphore
	if _stats_recompute_semaphore is None:
		_stats_recompute_semaphore = asyncio.Semaphore(MAX_CONCURRENT_STATS_RECOMPUTES)
	return _stats_recompute_semaphore


class StatsService:
	"""Manages pre-computed progress statistics in Redis hash.

	Key pattern: memora:stats:{user_id}:{subject_id}:v{version}

	Fields stored:
	- completed: total lessons completed
	- total: total lessons in subject
	- {track_id}:completed, {track_id}:total
	- {unit_id}:completed, {unit_id}:total
	- {topic_id}:completed, {topic_id}:total

	Operations:
	- get_stats: HGETALL O(N) on fields, but fields count is small
	- set_stats: HSET O(N) on fields for batch init
	- increment_completion_stats: Pipeline HINCRBY O(1) per field, O(4) total
	- invalidate_stats: DELETE O(1)

	Per RESEARCH.md:
	- Cache with 1 hour TTL (matches HierarchyService)
	- Atomic HINCRBY updates on lesson completion
	- Lazy initialization from bitmap on first access
	"""

	CACHE_TTL = 3600  # 1 hour, matches HierarchyService
	JITTER_RANGE = 120  # +0-120s spread to prevent synchronized TTL expiry
	RECOMPUTE_TIMEOUT = 2.0  # seconds to wait for semaphore before bypassing

	def __init__(self, redis_client: redis.Redis):
		self.redis = redis_client

	def _stats_key(self, user_id: str, subject_id: str, version: int) -> str:
		"""Generate Redis key for stats hash.

		Args:
			user_id: Player's user ID
			subject_id: Subject identifier (e.g., "MATH-G5")
			version: Bitmap version for structural changes

		Returns:
			Redis key string
		"""
		return _stats_key_fn(user_id, subject_id, version)

	async def increment_completion_stats(
		self,
		user_id: str,
		subject_id: str,
		version: int,
		track_id: str,
		unit_id: str,
		topic_id: str,
	) -> None:
		"""Atomically increment all completion counters.

		Uses pipeline with HINCRBY for atomic increment of all 4 counters
		(subject + track + unit + topic completed).

		O(1) per field, O(4) total. Refreshes TTL on update.

		Args:
			user_id: Player's user ID
			subject_id: Subject identifier
			version: Bitmap version
			track_id: Track that contains the completed lesson
			unit_id: Unit that contains the completed lesson
			topic_id: Topic that contains the completed lesson
		"""
		key = self._stats_key(user_id, subject_id, version)

		pipe = self.redis.pipeline()
		pipe.hincrby(key, "completed", 1)
		pipe.hincrby(key, f"{track_id}:completed", 1)
		pipe.hincrby(key, f"{unit_id}:completed", 1)
		pipe.hincrby(key, f"{topic_id}:completed", 1)
		# Refresh TTL on update
		pipe.expire(key, self.CACHE_TTL)
		await pipe.execute()

	async def get_stats(
		self,
		user_id: str,
		subject_id: str,
		version: int,
	) -> dict[str, str] | None:
		"""Retrieve all stats from Redis hash.

		Uses HGETALL to retrieve all fields.
		Returns None if key doesn't exist (signals need for lazy init).

		Args:
			user_id: Player's user ID
			subject_id: Subject identifier
			version: Bitmap version

		Returns:
			Dict of field->value (strings) or None if not cached
		"""
		key = self._stats_key(user_id, subject_id, version)

		# HGETALL returns empty dict if key doesn't exist
		result = await self.redis.hgetall(key)

		if not result:
			return None

		# Handle bytes decoding for Redis response
		decoded: dict[str, str] = {}
		for k, v in result.items():
			decoded_key = k.decode() if isinstance(k, bytes) else k
			decoded_val = v.decode() if isinstance(v, bytes) else v
			decoded[decoded_key] = decoded_val

		return decoded

	async def set_stats(
		self,
		user_id: str,
		subject_id: str,
		version: int,
		stats: dict[str, str],
	) -> None:
		"""Initialize stats hash from computed values.

		Uses HSET with mapping for batch initialization.
		Sets TTL via EXPIRE.

		Args:
			user_id: Player's user ID
			subject_id: Subject identifier
			version: Bitmap version
			stats: Dict of field->value (strings) to store
		"""
		key = self._stats_key(user_id, subject_id, version)

		# HSET with mapping for batch initialization
		await self.redis.hset(key, mapping=stats)
		await self.redis.expire(key, self.CACHE_TTL + random.randint(0, self.JITTER_RANGE))

	async def invalidate_stats(
		self,
		user_id: str,
		subject_id: str,
		version: int,
	) -> None:
		"""Delete the stats key for cache invalidation.

		Args:
			user_id: Player's user ID
			subject_id: Subject identifier
			version: Bitmap version
		"""
		key = self._stats_key(user_id, subject_id, version)
		await self.redis.delete(key)

	async def get_or_recompute(
		self,
		user_id: str,
		subject_id: str,
		version: int,
		content_hash: str,
		completed_bits: set[int],
		hierarchy: SubjectHierarchy,
	) -> dict[str, str]:
		"""Get cached stats or recompute under semaphore throttle.

		Fast path: returns cached stats if hash matches (no semaphore).
		Slow path: acquires semaphore → double-checks cache → recomputes.
		Timeout: if semaphore can't be acquired within RECOMPUTE_TIMEOUT,
		proceeds without throttle (a few extra concurrent recomputes are
		better than blocking requests for seconds).

		Args:
			user_id: Player's user ID
			subject_id: Subject identifier
			version: Bitmap version
			content_hash: Current hierarchy content hash
			completed_bits: Set of completed bit indexes
			hierarchy: Subject hierarchy for recompute

		Returns:
			Dict of field->value stats (always valid)
		"""
		key = self._stats_key(user_id, subject_id, version)

		# Fast path: cache hit with matching hash (no lock, no semaphore)
		stats = await self.get_stats(user_id, subject_id, version)
		if stats is not None and "total" in stats and stats.get("_content_hash") == content_hash:
			return stats

		# Slow path: per-key lock prevents thundering herd for same key
		lock = _get_compute_lock(key)
		async with lock:
			# Double-check after acquiring per-key lock
			stats = await self.get_stats(user_id, subject_id, version)
			if stats is not None and "total" in stats and stats.get("_content_hash") == content_hash:
				return stats

			# System-wide semaphore limits total concurrent recomputes
			sem = get_stats_recompute_semaphore()
			acquired = False
			try:
				await asyncio.wait_for(sem.acquire(), timeout=self.RECOMPUTE_TIMEOUT)
				acquired = True
			except asyncio.TimeoutError:
				logger.warning(
					"stats_recompute_semaphore_timeout",
					user_id=user_id,
					subject_id=subject_id,
					version=version,
				)

			try:
				stats = compute_stats_from_hierarchy(hierarchy, completed_bits)
				await self.set_stats(user_id, subject_id, version, stats)
				return stats
			finally:
				if acquired:
					sem.release()


def compute_stats_from_hierarchy(
	hierarchy: SubjectHierarchy,
	completed_bits: set[int],
) -> dict[str, str]:
	"""Compute all stats from hierarchy and bitmap.

	Walks hierarchy tree, counting completed/total at each level.
	Used for cold start initialization.

	Args:
		hierarchy: Subject hierarchy with tracks/units/topics/lessons
		completed_bits: Set of bit indexes that are completed

	Returns:
		Dict suitable for HSET mapping (all values as strings)
	"""
	stats: dict[str, str] = {}

	subject_completed = 0
	subject_total = 0

	for track in hierarchy.tracks:
		track_completed = 0
		track_total = 0

		for unit in track.units:
			unit_completed = 0
			unit_total = 0

			for topic in unit.topics:
				topic_completed = sum(
					1 for lesson in topic.lessons if lesson.bit_index in completed_bits
				)
				topic_total = len(topic.lessons)

				stats[f"{topic.topic_id}:completed"] = str(topic_completed)
				stats[f"{topic.topic_id}:total"] = str(topic_total)

				unit_completed += topic_completed
				unit_total += topic_total

			stats[f"{unit.unit_id}:completed"] = str(unit_completed)
			stats[f"{unit.unit_id}:total"] = str(unit_total)

			track_completed += unit_completed
			track_total += unit_total

		stats[f"{track.track_id}:completed"] = str(track_completed)
		stats[f"{track.track_id}:total"] = str(track_total)

		subject_completed += track_completed
		subject_total += track_total

	stats["completed"] = str(subject_completed)
	stats["total"] = str(subject_total)
	stats["_content_hash"] = hierarchy.content_hash

	return stats
