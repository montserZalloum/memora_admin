"""Tests for StatsService - Pre-computed progress statistics."""

import pytest

from fastapi_app.services.stats import StatsService, compute_stats_from_hierarchy
from fastapi_app.models.progress import SubjectHierarchy, TrackInfo, UnitInfo, TopicInfo, LessonInfo

# Test constants
TEST_USER = "USER-TEST-STS-001"
TEST_SUBJECT = "SUBJ-TEST-STS-001"
TEST_VERSION = 1


@pytest.fixture
async def stats_svc(redis_client, test_prefix, mock_frappe):
	"""StatsService with test dependencies."""
	return StatsService(redis_client, key_prefix=test_prefix)


class TestCacheHit:
	"""Cache hit returns cached stats without Frappe."""

	async def test_tc_sts_01_get_stats_cache_hit(self, stats_svc, redis_client, test_prefix):
		"""TC-STS-01: Cache hit returns pre-seeded stats."""
		# Setup: pre-seed stats in Redis
		key = f"{test_prefix}stats:{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		stats_data = {
			"completed": "5",
			"total": "10",
			"TRACK-001:completed": "2",
			"TRACK-001:total": "5",
		}
		await redis_client.hset(key, mapping=stats_data)
		await redis_client.expire(key, 3600)

		# Action: fetch stats
		result = await stats_svc.get_stats(TEST_USER, TEST_SUBJECT, TEST_VERSION)

		# Assert: returns cached data
		assert result == stats_data


class TestCacheMiss:
	"""Cache miss returns None, signals need for initialization."""

	async def test_tc_sts_02_get_stats_cache_miss(self, stats_svc, redis_client, test_prefix):
		"""TC-STS-02: Cache miss returns None."""
		# Setup: no pre-seeded data

		# Action: fetch stats (should miss)
		result = await stats_svc.get_stats(TEST_USER, TEST_SUBJECT, TEST_VERSION)

		# Assert: returns None
		assert result is None


class TestSetStats:
	"""set_stats stores stats in Redis with TTL."""

	async def test_tc_sts_03_set_stats_stores_with_ttl(self, stats_svc, redis_client, test_prefix):
		"""TC-STS-03: set_stats stores hash with TTL."""
		# Setup: stats to store
		stats_data = {
			"completed": "3",
			"total": "8",
			"TRACK-001:completed": "1",
			"TRACK-001:total": "3",
		}

		# Action: store stats
		await stats_svc.set_stats(TEST_USER, TEST_SUBJECT, TEST_VERSION, stats_data)

		# Assert: key exists with correct data
		key = f"{test_prefix}stats:{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		stored = await redis_client.hgetall(key)
		assert stored == stats_data

		# Assert: TTL set (roughly 3600)
		ttl = await redis_client.ttl(key)
		assert ttl > 0 and ttl <= 3600


class TestIncrementStats:
	"""increment_completion_stats atomically increments all counters."""

	async def test_tc_sts_04_increment_completion_stats(self, stats_svc, redis_client, test_prefix):
		"""TC-STS-04: increment_completion_stats increments counters atomically."""
		# Setup: initial stats
		key = f"{test_prefix}stats:{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		initial_stats = {
			"completed": "5",
			"total": "10",
			"TRACK-001:completed": "2",
			"TRACK-001:total": "5",
			"UNIT-001:completed": "1",
			"UNIT-001:total": "2",
			"TOPIC-001:completed": "1",
			"TOPIC-001:total": "2",
		}
		await redis_client.hset(key, mapping=initial_stats)

		# Action: increment stats
		await stats_svc.increment_completion_stats(
			TEST_USER, TEST_SUBJECT, TEST_VERSION, "TRACK-001", "UNIT-001", "TOPIC-001"
		)

		# Assert: all counters incremented
		result = await redis_client.hgetall(key)
		assert result["completed"] == "6"
		assert result["TRACK-001:completed"] == "3"
		assert result["UNIT-001:completed"] == "2"
		assert result["TOPIC-001:completed"] == "2"


class TestComputeStats:
	"""compute_stats_from_hierarchy computes stats from hierarchy tree."""

	async def test_tc_sts_05_compute_stats_from_hierarchy(self):
		"""TC-STS-05: compute_stats_from_hierarchy calculates all stats."""
		# Setup: minimal hierarchy with 1 track, 1 unit, 1 topic, 2 lessons
		hierarchy = SubjectHierarchy(
			subject_id=TEST_SUBJECT,
			version=1,
			bit_range=2,
			tracks=[
				TrackInfo(
					track_id="TRACK-001",
					units=[
						UnitInfo(
							unit_id="UNIT-001",
							topics=[
								TopicInfo(
									topic_id="TOPIC-001",
									lessons=[
										LessonInfo(lesson_id="LESSON-001", bit_index=0),
										LessonInfo(lesson_id="LESSON-002", bit_index=1),
									],
								)
							],
						)
					],
				)
			],
		)

		# Action: compute stats with first lesson completed
		stats = compute_stats_from_hierarchy(hierarchy, completed_bits={0})

		# Assert: correct counts
		assert stats["completed"] == "1"
		assert stats["total"] == "2"
		assert stats["TRACK-001:completed"] == "1"
		assert stats["TRACK-001:total"] == "2"
		assert stats["UNIT-001:completed"] == "1"
		assert stats["UNIT-001:total"] == "2"
		assert stats["TOPIC-001:completed"] == "1"
		assert stats["TOPIC-001:total"] == "2"


class TestEdgeCases:
	"""Edge cases and boundary conditions."""

	async def test_tc_sts_06_compute_stats_empty_hierarchy(self):
		"""TC-STS-06: compute_stats_from_hierarchy with empty hierarchy."""
		# Setup: empty hierarchy
		hierarchy = SubjectHierarchy(
			subject_id=TEST_SUBJECT,
			version=1,
			bit_range=0,
			tracks=[],
		)

		# Action: compute stats
		stats = compute_stats_from_hierarchy(hierarchy, completed_bits=set())

		# Assert: all zeros
		assert stats["completed"] == "0"
		assert stats["total"] == "0"

	async def test_tc_sts_07_invalidate_stats(self, stats_svc, redis_client, test_prefix):
		"""TC-STS-07: invalidate_stats deletes cache key."""
		# Setup: pre-seeded stats
		key = f"{test_prefix}stats:{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		await redis_client.hset(key, mapping={"completed": "5"})

		# Action: invalidate
		await stats_svc.invalidate_stats(TEST_USER, TEST_SUBJECT, TEST_VERSION)

		# Assert: key deleted
		exists = await redis_client.exists(key)
		assert exists == 0
