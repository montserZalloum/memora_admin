"""Tests for PlanService - Plan manifest caching."""

import json
from datetime import datetime
import pytest

from fastapi_app.core.redis_keys import plan_manifest_key
from fastapi_app.services.plan import PlanService
from fastapi_app.models.plan import PlanManifest, PlanSubject

# Test constants
TEST_PLAN = "PLAN-TEST-PLN-001"


@pytest.fixture
async def plan_svc(redis_client, test_prefix, mock_frappe):
	"""PlanService with test dependencies."""
	return PlanService(redis_client, frappe_client=mock_frappe)


class TestCacheHit:
	"""Cache hit returns cached manifest without Frappe call."""

	async def test_tc_pln_01_cache_hit_no_frappe_call(self, plan_svc, redis_client, test_prefix, mock_frappe):
		"""TC-PLN-01: Cache hit - Frappe NOT called."""
		# Setup: pre-seed manifest in Redis
		manifest = PlanManifest(
			version=1,
			generated_at=datetime.now(),
			plan_id=TEST_PLAN,
			title="Test Plan",
			subjects=[],
		)
		await redis_client.set(plan_manifest_key(TEST_PLAN), manifest.model_dump_json())

		# Action: get manifest
		result = await plan_svc.get_manifest(TEST_PLAN)

		# Assert: returns cached data
		assert result is not None
		assert result.plan_id == TEST_PLAN

		# Assert: Frappe NOT called
		mock_frappe.call.assert_not_called()


class TestCacheMiss:
	"""Cache miss fetches from Frappe and caches result."""

	async def test_tc_pln_02_cache_miss_fetches_and_caches(self, plan_svc, redis_client, test_prefix, mock_frappe):
		"""TC-PLN-02: Cache miss - fetches from Frappe and caches."""
		# Setup: configure mock
		manifest_dict = {
			"version": 1,
			"generated_at": datetime.now().isoformat(),
			"plan_id": TEST_PLAN,
			"title": "Test Plan",
			"subjects": [],
		}
		mock_frappe.call.return_value = manifest_dict

		# Action: get manifest
		result = await plan_svc.get_manifest(TEST_PLAN)

		# Assert: returns Frappe data
		assert result is not None
		assert result.plan_id == TEST_PLAN

		# Assert: Frappe called
		mock_frappe.call.assert_called_once()

		# Assert: cached in Redis with TTL
		cached = await redis_client.get(plan_manifest_key(TEST_PLAN))
		assert cached is not None

		# Assert: TTL set
		ttl = await redis_client.ttl(plan_manifest_key(TEST_PLAN))
		assert ttl > 0 and ttl <= 3600


class TestInvalidation:
	"""Invalidate removes cache key."""

	async def test_tc_pln_03_invalidate_deletes_key(self, plan_svc, redis_client, test_prefix):
		"""TC-PLN-03: invalidate deletes Redis key."""
		# Setup: pre-seed manifest
		manifest = PlanManifest(
			version=1,
			generated_at=datetime.now(),
			plan_id=TEST_PLAN,
			title="Test Plan",
			subjects=[],
		)
		await redis_client.set(plan_manifest_key(TEST_PLAN), manifest.model_dump_json())

		# Action: invalidate
		await plan_svc.invalidate(TEST_PLAN)

		# Assert: key deleted
		exists = await redis_client.exists(plan_manifest_key(TEST_PLAN))
		assert exists == 0
