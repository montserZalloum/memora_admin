"""Tests for review endpoints.

Tests verify review endpoints:
- GET /api/v1/reviews/ - Get review overview
- GET /api/v1/reviews/{subject} - Get due items
- POST /api/v1/reviews/{subject}/submit - Submit review results

Reference: contracts/endpoint-test-contracts.md §6
"""

import pytest


@pytest.mark.asyncio
class TestReviewEndpoints:
	"""Review transaction tests."""

	async def test_review_overview_success(self, authed_client, redis_client, mock_frappe):
		"""Get review overview returns subject list."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock ReviewService.get_overview()
			mock_frappe.call.return_value = {
				"subjects": [
					{"subject_id": "SUB-MATH", "due_count": 5},
					{"subject_id": "SUB-SCIENCE", "due_count": 3},
				]
			}

			resp = await client.get("/api/v1/reviews")

			assert resp.status_code == 200
			data = resp.json()
			assert "subjects" in data
			assert isinstance(data["subjects"], list)
		finally:
			pass

	async def test_review_due_items_success(self, authed_client, redis_client, mock_frappe):
		"""Get due items for subject returns items list."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock ReviewService.get_due_items()
			mock_frappe.call.return_value = {
				"items": [
					{
						"item_id": "uuid-1",
						"lesson_id": "LSN-001",
					},
					{
						"item_id": "uuid-2",
						"lesson_id": "LSN-002",
					},
				],
				"has_more": False,
			}

			resp = await client.get("/api/v1/reviews/SUB-MATH")

			assert resp.status_code == 200
			data = resp.json()
			assert "items" in data
			assert "has_more" in data
		finally:
			pass

	async def test_review_submit_success(self, authed_client, redis_client, mock_frappe):
		"""Submit review results returns success."""
		client, token, player_id, family_id = authed_client

		try:
			# Mock ReviewService.submit_reviews()
			mock_frappe.call.return_value = {
				"processed": 3,
				"xp_awarded": 150,
				"remaining_due": 2,
			}

			resp = await client.post(
				"/api/v1/reviews/SUB-MATH/submit",
				json={
					"items": [
						{"item_id": "uuid-1", "fail_count": 0},
						{"item_id": "uuid-2", "fail_count": 1},
						{"item_id": "uuid-3", "fail_count": 0},
					]
				},
			)

			assert resp.status_code == 200
			data = resp.json()
			assert "processed" in data
			assert "xp_awarded" in data
		finally:
			pass

	async def test_review_unauthenticated_401(self, app_client):
		"""Unauthenticated request returns 401."""
		resp = await app_client.get("/api/v1/reviews")

		assert resp.status_code == 401

	async def test_review_submit_empty_items_422(self, authed_client, redis_client):
		"""Submit with empty items list returns 422."""
		client, token, player_id, family_id = authed_client

		resp = await client.post(
			"/api/v1/reviews/SUB-MATH/submit",
			json={"items": []},
		)

		assert resp.status_code == 422
