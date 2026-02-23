"""Tests for HierarchyService - Subject hierarchy caching."""

import json
import pytest

from fastapi_app.core.redis_keys import hierarchy_key, subjects_with_free_content_key
from fastapi_app.models.progress import SubjectHierarchy, TrackInfo, UnitInfo, TopicInfo, LessonInfo
from fastapi_app.services.hierarchy import HierarchyService

# Test constants
TEST_SUBJECT = "SUBJ-TEST-HIR-001"


@pytest.fixture
async def hierarchy_svc(redis_client, test_prefix, mock_frappe):
	"""HierarchyService with test dependencies."""
	return HierarchyService(redis_client, frappe_client=mock_frappe)


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


class TestInvalidation:
	"""Invalidate removes cache key."""

	async def test_tc_hir_03_invalidate_deletes_key(self, hierarchy_svc, redis_client, test_prefix):
		"""TC-HIR-03: invalidate deletes Redis key."""
		# Setup: pre-seed hierarchy
		hierarchy = _make_test_hierarchy()
		key = hierarchy_key(TEST_SUBJECT)
		await redis_client.set(key, hierarchy.model_dump_json())

		# Action: invalidate
		await hierarchy_svc.invalidate(TEST_SUBJECT)

		# Assert: key deleted
		exists = await redis_client.exists(key)
		assert exists == 0


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
