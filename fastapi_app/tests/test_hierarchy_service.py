"""Tests for HierarchyService - Subject hierarchy caching."""

from unittest.mock import patch

import pytest

from fastapi_app.core.redis_keys import hierarchy_key, subjects_with_free_content_key
from fastapi_app.models.progress import LessonInfo, SubjectHierarchy, TopicInfo, TrackInfo, UnitInfo
from fastapi_app.services.hierarchy import HierarchyService, _local_hierarchy_cache

# Test constants
TEST_SUBJECT = "SUBJ-TEST-HIR-001"


@pytest.fixture
async def hierarchy_svc(redis_client, test_prefix, mock_frappe):
	"""HierarchyService with test dependencies."""
	_local_hierarchy_cache.clear()
	yield HierarchyService(redis_client, frappe_client=mock_frappe)
	_local_hierarchy_cache.clear()


def _make_test_hierarchy() -> SubjectHierarchy:
	"""Create a test hierarchy."""
	return SubjectHierarchy(
		subject_id=TEST_SUBJECT,
		version=1,
		bit_range=1,
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
								],
							)
						],
					)
				],
			)
		],
	)


class TestCacheHit:
	"""Cache hit returns cached hierarchy without Frappe call."""

	async def test_tc_hir_01_cache_hit_no_frappe_call(self, hierarchy_svc, redis_client, test_prefix, mock_frappe):
		"""TC-HIR-01: Cache hit - Frappe NOT called."""
		# Setup: pre-seed hierarchy in Redis
		hierarchy = _make_test_hierarchy()
		key = hierarchy_key(TEST_SUBJECT)
		await redis_client.set(key, hierarchy.model_dump_json())

		# Action: get hierarchy
		result = await hierarchy_svc.get_hierarchy(TEST_SUBJECT)

		# Assert: returns cached data
		assert result is not None
		assert result.subject_id == TEST_SUBJECT

		# Assert: Frappe NOT called
		mock_frappe.call.assert_not_called()


class TestCacheMiss:
	"""Cache miss fetches from Frappe and caches result."""

	async def test_tc_hir_02_cache_miss_fetches_and_caches(self, hierarchy_svc, redis_client, test_prefix, mock_frappe):
		"""TC-HIR-02: Cache miss - fetches from Frappe and caches."""
		# Setup: configure mock
		hierarchy_dict = _make_test_hierarchy().model_dump()
		mock_frappe.call.return_value = hierarchy_dict

		# Action: get hierarchy
		result = await hierarchy_svc.get_hierarchy(TEST_SUBJECT)

		# Assert: returns Frappe data
		assert result is not None
		assert result.subject_id == TEST_SUBJECT

		# Assert: Frappe called
		mock_frappe.call.assert_called_once_with(
			"memora_admin.api.hierarchy.get_subject_hierarchy",
			{"subject_id": TEST_SUBJECT},
		)

		# Assert: cached in Redis with TTL
		key = hierarchy_key(TEST_SUBJECT)
		cached = await redis_client.get(key)
		assert cached is not None
		cached_obj = SubjectHierarchy.model_validate_json(cached)
		assert cached_obj.subject_id == TEST_SUBJECT

		# Assert: TTL set
		ttl = await redis_client.ttl(key)
		assert ttl > 0 and ttl <= 3600


class TestLocalCache:
	"""In-process local cache for parsed SubjectHierarchy objects."""

	async def test_local_cache_hit_skips_redis(self, hierarchy_svc, redis_client, test_prefix):
		"""T005: Second get_hierarchy() call uses local cache — Redis NOT called again."""
		# Setup: pre-seed hierarchy in Redis
		hierarchy = _make_test_hierarchy()
		key = hierarchy_key(TEST_SUBJECT)
		await redis_client.set(key, hierarchy.model_dump_json())

		# First call — should hit Redis and populate local cache
		result1 = await hierarchy_svc.get_hierarchy(TEST_SUBJECT)
		assert result1 is not None
		assert result1.subject_id == TEST_SUBJECT

		# Spy on redis.get to track subsequent calls
		original_get = hierarchy_svc.redis.get
		call_count = 0

		async def counting_get(*args, **kwargs):
			nonlocal call_count
			call_count += 1
			return await original_get(*args, **kwargs)

		hierarchy_svc.redis.get = counting_get

		# Second call — should use local cache, NOT Redis
		result2 = await hierarchy_svc.get_hierarchy(TEST_SUBJECT)
		assert result2 is not None
		assert result2.subject_id == TEST_SUBJECT
		assert call_count == 0, f"Expected 0 Redis get calls on cache hit, got {call_count}"

	async def test_local_cache_ttl_expiry_refetches_from_redis(self, hierarchy_svc, redis_client, test_prefix):
		"""T006: After LOCAL_TTL expires, get_hierarchy() re-fetches from Redis."""
		# Setup: pre-seed hierarchy in Redis
		hierarchy = _make_test_hierarchy()
		key = hierarchy_key(TEST_SUBJECT)
		await redis_client.set(key, hierarchy.model_dump_json())

		# First call at t=1000 — populates local cache
		with patch("fastapi_app.services.hierarchy.time") as mock_time:
			mock_time.monotonic.return_value = 1000.0
			result1 = await hierarchy_svc.get_hierarchy(TEST_SUBJECT)
			assert result1 is not None

		# Verify local cache is populated
		assert TEST_SUBJECT in _local_hierarchy_cache

		# Spy on redis.get for the second call
		original_get = hierarchy_svc.redis.get
		call_count = 0

		async def counting_get(*args, **kwargs):
			nonlocal call_count
			call_count += 1
			return await original_get(*args, **kwargs)

		hierarchy_svc.redis.get = counting_get

		# Second call at t=1400 (400s later, still within 300s TTL? No — 400 > 300, expired)
		with patch("fastapi_app.services.hierarchy.time") as mock_time:
			mock_time.monotonic.return_value = 1400.0
			result2 = await hierarchy_svc.get_hierarchy(TEST_SUBJECT)
			assert result2 is not None
			assert result2.subject_id == TEST_SUBJECT

		# Redis should have been called because local cache expired
		assert call_count > 0, "Expected Redis get to be called after local TTL expiry"


class TestInvalidation:
	"""Invalidate removes cache key."""

	async def test_tc_hir_03_invalidate_deletes_key(self, hierarchy_svc, redis_client, test_prefix):
		"""TC-HIR-03: invalidate deletes Redis key and local cache entry."""
		# Setup: pre-seed hierarchy in Redis
		hierarchy = _make_test_hierarchy()
		key = hierarchy_key(TEST_SUBJECT)
		await redis_client.set(key, hierarchy.model_dump_json())

		# Pre-populate local cache (simulate a previous get_hierarchy call)
		_local_hierarchy_cache[TEST_SUBJECT] = (hierarchy, float("inf"))

		# Action: invalidate
		await hierarchy_svc.invalidate(TEST_SUBJECT)

		# Assert: Redis key deleted
		exists = await redis_client.exists(key)
		assert exists == 0

		# Assert: local cache entry removed (T007)
		assert TEST_SUBJECT not in _local_hierarchy_cache


class TestFreeContent:
	"""Cache miss updates free content set."""

	async def test_tc_hir_04_free_content_set_updated(self, hierarchy_svc, redis_client, test_prefix, mock_frappe):
		"""TC-HIR-04: Cache miss with free content updates set."""
		# Setup: hierarchy with free content using free_units array
		hierarchy = _make_test_hierarchy()
		hierarchy.free_units = ["UNIT-001"]  # Mark unit as free
		hierarchy_dict = hierarchy.model_dump()
		mock_frappe.call.return_value = hierarchy_dict

		# Action: get hierarchy
		await hierarchy_svc.get_hierarchy(TEST_SUBJECT)

		# Assert: subject added to free content set
		free_set_key = subjects_with_free_content_key()
		is_member = await redis_client.sismember(free_set_key, TEST_SUBJECT)
		assert bool(is_member) is True
