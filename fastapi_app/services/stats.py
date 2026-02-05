"""Stats caching service for pre-computed progress statistics."""

import redis.asyncio as redis

from fastapi_app.models.progress import SubjectHierarchy


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

	def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
		self.redis = redis_client
		self.prefix = key_prefix

	def _stats_key(self, user_id: str, subject_id: str, version: int) -> str:
		"""Generate Redis key for stats hash.

		Args:
			user_id: Player's user ID
			subject_id: Subject identifier (e.g., "MATH-G5")
			version: Bitmap version for structural changes

		Returns:
			Redis key string
		"""
		return f"{self.prefix}stats:{user_id}:{subject_id}:v{version}"

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
		await self.redis.expire(key, self.CACHE_TTL)

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

	return stats
