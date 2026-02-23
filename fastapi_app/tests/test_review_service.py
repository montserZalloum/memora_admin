"""Tests for ReviewService - Review caching and delegation."""

import json
import pytest

from fastapi_app.core.redis_keys import reviews_overview_key
from fastapi_app.services.review import ReviewService

# Test constants
TEST_PLAYER = "PLAYER-TEST-REV-001"
TEST_SUBJECT = "SUBJ-TEST-REV-001"


@pytest.fixture
async def review_svc(redis_client, mock_frappe):
	"""ReviewService with test dependencies."""
	return ReviewService(redis_client, frappe_client=mock_frappe)


@pytest.fixture(autouse=True)
async def cleanup_review_keys(redis_client):
	"""Auto-cleanup review overview keys after each test."""
	yield
	# SCAN and delete all memora:reviews_overview:* keys
	cursor = 0
	while True:
		cursor, keys = await redis_client.scan(cursor, match="memora:reviews_overview:*", count=1000)
		if keys:
			await redis_client.delete(*keys)
		if cursor == 0:
			break


class TestOverviewCacheHit:
	"""Cache hit returns cached overview without Frappe call."""

	async def test_tc_rev_01_get_overview_cache_hit(self, review_svc, redis_client, mock_frappe):
		"""TC-REV-01: get_overview cache hit - Frappe NOT called."""
		# Setup: pre-seed overview in Redis
		overview = [{"subject_id": TEST_SUBJECT, "due_count": 5}]
		key = reviews_overview_key(TEST_PLAYER)
		await redis_client.set(key, json.dumps(overview))

		# Action: get overview
		result = await review_svc.get_overview(TEST_PLAYER)

		# Assert: returns cached data
		assert len(result) == 1
		assert result[0]["due_count"] == 5

		# Assert: Frappe NOT called
		mock_frappe.call.assert_not_called()


class TestOverviewCacheMiss:
	"""Cache miss fetches from Frappe and caches result."""

	async def test_tc_rev_02_get_overview_cache_miss(self, review_svc, redis_client, mock_frappe):
		"""TC-REV-02: get_overview cache miss - fetches from Frappe and caches."""
		# Setup: configure mock
		overview = [
			{"subject_id": TEST_SUBJECT, "due_count": 3},
			{"subject_id": "OTHER-SUBJECT", "due_count": 1},
		]
		mock_frappe.call.return_value = overview

		# Action: get overview
		result = await review_svc.get_overview(TEST_PLAYER)

		# Assert: returns Frappe data
		assert len(result) == 2
		assert result[0]["due_count"] == 3

		# Assert: Frappe called
		mock_frappe.call.assert_called_once_with(
			"memora_admin.api.reviews.get_review_overview",
			{"player_id": TEST_PLAYER},
		)

		# Assert: cached in Redis with TTL
		key = reviews_overview_key(TEST_PLAYER)
		cached = await redis_client.get(key)
		assert cached is not None
		cached_data = json.loads(cached)
		assert cached_data == overview

		# Assert: TTL set (~300 seconds)
		ttl = await redis_client.ttl(key)
		assert ttl > 0 and ttl <= 300


class TestDueItems:
	"""get_due_items always fresh - no cache."""

	async def test_tc_rev_03_get_due_items_always_fresh(self, review_svc, redis_client, mock_frappe):
		"""TC-REV-03: get_due_items - always delegates to Frappe (no cache)."""
		# Setup: configure mock
		due_items = {
			"items": [
				{"item_id": "ITEM-001", "interval": 1},
				{"item_id": "ITEM-002", "interval": 3},
			],
			"has_more": False,
		}
		mock_frappe.call.return_value = due_items

		# Action: get due items
		result = await review_svc.get_due_items(TEST_PLAYER, TEST_SUBJECT)

		# Assert: returns Frappe data
		assert len(result["items"]) == 2
		assert result["has_more"] is False

		# Assert: Frappe called
		mock_frappe.call.assert_called_once_with(
			"memora_admin.api.reviews.get_due_items",
			{"player_id": TEST_PLAYER, "subject_id": TEST_SUBJECT},
		)


class TestSubmitReviews:
	"""submit_reviews invalidates cache after Frappe call."""

	async def test_tc_rev_04_submit_reviews_invalidates_cache(self, review_svc, redis_client, mock_frappe):
		"""TC-REV-04: submit_reviews - Frappe called, cache DELETED."""
		# Setup: pre-seed overview cache
		overview = [{"subject_id": TEST_SUBJECT, "due_count": 3}]
		key = reviews_overview_key(TEST_PLAYER)
		await redis_client.set(key, json.dumps(overview))

		# Setup: configure mock for submit
		mock_frappe.call.return_value = {"processed": 2, "remaining_due": 1}

		# Action: submit reviews
		items = [{"item_id": "ITEM-001"}, {"item_id": "ITEM-002"}]
		result = await review_svc.submit_reviews(TEST_PLAYER, TEST_SUBJECT, items)

		# Assert: returns success response
		assert result["processed"] == 2

		# Assert: Frappe called
		mock_frappe.call.assert_called_once()

		# Assert: cache key DELETED
		exists = await redis_client.exists(key)
		assert exists == 0
