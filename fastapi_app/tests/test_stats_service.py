"""Tests for StatsService - Pre-computed progress statistics."""

import asyncio

import pytest

from fastapi_app.core.redis_keys import stats_key
from fastapi_app.models.progress import SubjectHierarchy, TrackInfo, UnitInfo, TopicInfo, LessonInfo
from fastapi_app.services.stats import StatsService, compute_stats_from_hierarchy, get_stats_recompute_semaphore

# Test constants
TEST_USER = "USER-TEST-STS-001"
TEST_SUBJECT = "SUBJ-TEST-STS-001"
TEST_VERSION = 1


@pytest.fixture
async def stats_svc(redis_client, test_prefix, mock_frappe):
	"""StatsService with test dependencies."""
	return StatsService(redis_client)


class TestCacheHit:
	"""Cache hit returns cached stats without Frappe."""

	async def test_tc_sts_01_get_stats_cache_hit(self, stats_svc, redis_client, test_prefix):
		"""TC-STS-01: Cache hit returns pre-seeded stats."""
		# Setup: pre-seed stats in Redis
		key = stats_key(TEST_USER, TEST_SUBJECT, TEST_VERSION)
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
		key = stats_key(TEST_USER, TEST_SUBJECT, TEST_VERSION)
		stored = await redis_client.hgetall(key)
		assert stored == stats_data

		# Assert: TTL set (3600 + up to 120s jitter)
		ttl = await redis_client.ttl(key)
		assert ttl > 0 and ttl <= 3600 + StatsService.JITTER_RANGE


class TestIncrementStats:
	"""increment_completion_stats atomically increments all counters."""

	async def test_tc_sts_04_increment_completion_stats(self, stats_svc, redis_client, test_prefix):
		"""TC-STS-04: increment_completion_stats increments counters atomically."""
		# Setup: initial stats
		key = stats_key(TEST_USER, TEST_SUBJECT, TEST_VERSION)
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
		key = stats_key(TEST_USER, TEST_SUBJECT, TEST_VERSION)
		await redis_client.hset(key, mapping={"completed": "5"})

		# Action: invalidate
		await stats_svc.invalidate_stats(TEST_USER, TEST_SUBJECT, TEST_VERSION)

		# Assert: key deleted
		exists = await redis_client.exists(key)
		assert exists == 0


# --- Helper for get_or_recompute tests ---


def _make_hierarchy(content_hash: str = "testhash", num_lessons: int = 3) -> SubjectHierarchy:
	"""Build a simple hierarchy for get_or_recompute tests."""
	lessons = [LessonInfo(lesson_id=f"LSN-{i:03d}", bit_index=i) for i in range(num_lessons)]
	return SubjectHierarchy(
		subject_id=TEST_SUBJECT,
		version=TEST_VERSION,
		bit_range=num_lessons,
		content_hash=content_hash,
		tracks=[
			TrackInfo(
				track_id="TRK-001",
				units=[
					UnitInfo(
						unit_id="UNT-001",
						topics=[
							TopicInfo(topic_id="TPC-001", lessons=lessons),
						],
					)
				],
			)
		],
	)


class TestGetOrRecompute:
	"""get_or_recompute returns cached stats or recomputes under semaphore."""

	async def test_get_or_recompute_cache_hit(self, stats_svc):
		"""Matching content hash returns cached stats without recompute."""
		hierarchy = _make_hierarchy(content_hash="match123", num_lessons=3)
		completed_bits = {0}

		# Pre-populate cache with matching hash
		stats = compute_stats_from_hierarchy(hierarchy, completed_bits)
		await stats_svc.set_stats(TEST_USER, TEST_SUBJECT, TEST_VERSION, stats)

		# get_or_recompute should return cached stats (fast path)
		result = await stats_svc.get_or_recompute(
			user_id=TEST_USER, subject_id=TEST_SUBJECT, version=TEST_VERSION,
			content_hash="match123", completed_bits=completed_bits, hierarchy=hierarchy,
		)

		assert result["_content_hash"] == "match123"
		assert result["completed"] == "1"
		assert result["total"] == "3"

	async def test_get_or_recompute_stale_hash(self, stats_svc):
		"""Mismatched content hash triggers recompute."""
		old_hierarchy = _make_hierarchy(content_hash="oldhash", num_lessons=3)
		new_hierarchy = _make_hierarchy(content_hash="newhash", num_lessons=4)
		completed_bits = {0, 1}

		# Seed cache with old hash
		old_stats = compute_stats_from_hierarchy(old_hierarchy, {0})
		await stats_svc.set_stats(TEST_USER, TEST_SUBJECT, TEST_VERSION, old_stats)

		# get_or_recompute with new hash should recompute
		result = await stats_svc.get_or_recompute(
			user_id=TEST_USER, subject_id=TEST_SUBJECT, version=TEST_VERSION,
			content_hash="newhash", completed_bits=completed_bits, hierarchy=new_hierarchy,
		)

		assert result["_content_hash"] == "newhash"
		assert result["completed"] == "2"
		assert result["total"] == "4"

	async def test_get_or_recompute_cache_miss(self, stats_svc):
		"""No cached stats triggers recompute."""
		hierarchy = _make_hierarchy(content_hash="fresh01", num_lessons=3)
		completed_bits = {0, 1, 2}

		# No pre-seeded data — cache miss
		result = await stats_svc.get_or_recompute(
			user_id=TEST_USER, subject_id=TEST_SUBJECT, version=TEST_VERSION,
			content_hash="fresh01", completed_bits=completed_bits, hierarchy=hierarchy,
		)

		assert result["_content_hash"] == "fresh01"
		assert result["completed"] == "3"
		assert result["total"] == "3"


class TestTTLJitter:
	"""set_stats applies TTL jitter to prevent synchronized expiry."""

	async def test_ttl_jitter_applied(self, stats_svc, redis_client, test_prefix):
		"""TTLs across multiple set_stats calls should vary (jitter applied)."""
		hierarchy = _make_hierarchy(content_hash="jitter01", num_lessons=2)

		# Store stats for 20 different users to collect TTL samples
		ttls = []
		for i in range(20):
			user = f"USR-JITTER-{i:03d}"
			stats = compute_stats_from_hierarchy(hierarchy, completed_bits=set())
			await stats_svc.set_stats(user, TEST_SUBJECT, TEST_VERSION, stats)
			key = stats_key(user, TEST_SUBJECT, TEST_VERSION)
			ttl = await redis_client.ttl(key)
			ttls.append(ttl)

		# All TTLs should be in valid range [1, 3720]
		for ttl in ttls:
			assert 1 <= ttl <= StatsService.CACHE_TTL + StatsService.JITTER_RANGE

		# With 20 samples and 120s jitter range, it's extremely unlikely all are identical
		assert len(set(ttls)) > 1, f"Expected TTL variation from jitter, but all TTLs were identical: {ttls[0]}"


class TestSemaphoreTimeoutDegradation:
	"""Semaphore timeout degrades gracefully — request completes instead of hanging."""

	async def test_semaphore_timeout_degrades_gracefully(self, stats_svc):
		"""With saturated semaphore, request still completes with valid stats."""
		import fastapi_app.services.stats as stats_module

		hierarchy = _make_hierarchy(content_hash="timeout01", num_lessons=3)
		completed_bits = {0}

		# Saturate the semaphore by acquiring all permits
		sem = get_stats_recompute_semaphore()
		# Save original and replace with a semaphore of 1 for easier testing
		original_sem = stats_module._stats_recompute_semaphore
		stats_module._stats_recompute_semaphore = asyncio.Semaphore(1)
		test_sem = stats_module._stats_recompute_semaphore

		try:
			# Acquire the single permit (saturation)
			await test_sem.acquire()

			# get_or_recompute should timeout on semaphore but still return valid stats
			result = await stats_svc.get_or_recompute(
				user_id=TEST_USER, subject_id=TEST_SUBJECT, version=TEST_VERSION,
				content_hash="timeout01", completed_bits=completed_bits, hierarchy=hierarchy,
			)

			# Should have valid stats despite semaphore timeout
			assert result is not None
			assert result["_content_hash"] == "timeout01"
			assert result["completed"] == "1"
			assert result["total"] == "3"
		finally:
			# Release the permit and restore original semaphore
			test_sem.release()
			stats_module._stats_recompute_semaphore = original_sem
