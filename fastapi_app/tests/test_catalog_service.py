"""Tests for CatalogService - Product catalog per-plan caching."""

import json
import pytest

from fastapi_app.core.redis_keys import access_key as _access_key_fn, catalog_key, pending_key as _pending_key_fn
from fastapi_app.models.catalog import CatalogProduct, CatalogSubject
from fastapi_app.services.catalog import CatalogService

# Test constants
TEST_PLAN = "PLAN-TEST-CAT-001"
TEST_PLAYER = "PLAYER-TEST-CAT-001"
TEST_PRODUCT_1 = "GRANT-CAT-001"
TEST_PRODUCT_2 = "GRANT-CAT-002"


@pytest.fixture
async def catalog_svc(redis_client, test_prefix, mock_frappe):
	"""CatalogService with test dependencies."""
	return CatalogService(redis_client, frappe_client=mock_frappe)


def _make_test_product(grant_id: str, subject_ids: list[str]) -> dict:
	"""Create a test product dict."""
	return {
		"product_grant_id": grant_id,
		"bundle_name": f"Bundle {grant_id}",
		"price": 99.99,
		"subjects": [{"subject_id": sid} for sid in subject_ids],
	}


class TestCacheHit:
	"""Cache hit returns cached catalog without Frappe call."""

	async def test_tc_cat_01_cache_hit_no_frappe_call(self, catalog_svc, redis_client, test_prefix, mock_frappe):
		"""TC-CAT-01: Cache hit - Frappe NOT called."""
		# Setup: pre-seed catalog in Redis
		products = [
			_make_test_product(TEST_PRODUCT_1, ["MATH"]),
			_make_test_product(TEST_PRODUCT_2, ["SCIENCE"]),
		]
		key = catalog_key(TEST_PLAN)
		await redis_client.set(key, json.dumps(products))

		# Action: get catalog
		result = await catalog_svc.get_catalog(TEST_PLAN)

		# Assert: returns cached data
		assert len(result) == 2
		assert result[0].product_grant_id == TEST_PRODUCT_1

		# Assert: Frappe NOT called
		mock_frappe.call.assert_not_called()


class TestCacheMiss:
	"""Cache miss fetches from Frappe and caches result."""

	async def test_tc_cat_02_cache_miss_fetches_and_caches(self, catalog_svc, redis_client, test_prefix, mock_frappe):
		"""TC-CAT-02: Cache miss - fetches from Frappe and caches."""
		# Setup: configure mock
		products = [_make_test_product(TEST_PRODUCT_1, ["MATH"])]
		mock_frappe.call.return_value = products

		# Action: get catalog
		result = await catalog_svc.get_catalog(TEST_PLAN)

		# Assert: returns Frappe data
		assert len(result) == 1
		assert result[0].product_grant_id == TEST_PRODUCT_1

		# Assert: Frappe called
		mock_frappe.call.assert_called_once()

		# Assert: cached in Redis with NO TTL (infinite)
		key = catalog_key(TEST_PLAN)
		cached = await redis_client.get(key)
		assert cached is not None

		# Assert: NO TTL set (infinite cache)
		ttl = await redis_client.ttl(key)
		assert ttl == -1


class TestPlayerFiltering:
	"""get_player_catalog filters by pending and purchased."""

	async def test_tc_cat_03_excludes_pending_products(self, catalog_svc, redis_client, test_prefix):
		"""TC-CAT-03: get_player_catalog excludes pending purchases."""
		# Setup: seed catalog
		products = [
			_make_test_product(TEST_PRODUCT_1, ["MATH"]),
			_make_test_product(TEST_PRODUCT_2, ["SCIENCE"]),
		]
		key = catalog_key(TEST_PLAN)
		await redis_client.set(key, json.dumps(products))

		# Setup: add PRODUCT_1 to pending set
		pkey = _pending_key_fn(TEST_PLAYER)
		await redis_client.sadd(pkey, TEST_PRODUCT_1)

		# Action: get player catalog
		result = await catalog_svc.get_player_catalog(TEST_PLAN, TEST_PLAYER)

		# Assert: PRODUCT_1 excluded (pending), PRODUCT_2 included
		assert len(result) == 1
		assert result[0].product_grant_id == TEST_PRODUCT_2

	async def test_tc_cat_04_excludes_purchased_products(self, catalog_svc, redis_client, test_prefix):
		"""TC-CAT-04: get_player_catalog excludes already purchased."""
		# Setup: seed catalog
		products = [
			_make_test_product(TEST_PRODUCT_1, ["MATH", "SCIENCE"]),
			_make_test_product(TEST_PRODUCT_2, ["HISTORY"]),
		]
		key = catalog_key(TEST_PLAN)
		await redis_client.set(key, json.dumps(products))

		# Setup: add all subjects of PRODUCT_1 to access set
		akey = _access_key_fn(TEST_PLAYER)
		await redis_client.sadd(akey, "SUB-MATH", "SUB-SCIENCE")

		# Action: get player catalog
		result = await catalog_svc.get_player_catalog(TEST_PLAN, TEST_PLAYER)

		# Assert: PRODUCT_1 excluded (all subjects purchased), PRODUCT_2 included
		assert len(result) == 1
		assert result[0].product_grant_id == TEST_PRODUCT_2
