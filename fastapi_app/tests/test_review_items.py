"""Tests for enriched review item response (Phase 3 — US1).

Tests verify the GET /api/v1/reviews/{subject} endpoint returns
question data (question_text, choices, correct_choice) from the
Review Item table, and that items without Review Item records
degrade gracefully with null question fields.

Reference: specs/024-review-item-table/contracts/review-items-api.md
"""

import pytest

import fastapi_app.api.deps as deps_module


@pytest.mark.asyncio
class TestEnrichedReviewItems:
	"""Tests for enriched due items with question data."""

	async def test_question_stage_returns_mcq_fields(self, authed_client, mock_frappe):
		"""QUESTION stage items include question_text, choices, correct_choice."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "a40e97dd-dbae-4d4d-9a5b-7b41af641ca1",
					"lesson_id": "LES-00042",
					"question_text": "كم عظمة في جسم الانسان",
					"choices": ["10", "12", "14"],
					"correct_choice": 1,
				}
			],
			"has_more": False,
		}

		resp = await client.get("/api/v1/reviews/SUB-00001")

		assert resp.status_code == 200
		data = resp.json()
		assert len(data["items"]) == 1

		item = data["items"][0]
		assert item["item_id"] == "a40e97dd-dbae-4d4d-9a5b-7b41af641ca1"
		assert item["lesson_id"] == "LES-00042"
		assert item["question_text"] == "كم عظمة في جسم الانسان"
		assert item["choices"] == ["10", "12", "14"]
		assert item["correct_choice"] == 1
		assert "stage_type" not in item
		assert "stage_id" not in item
		assert "content_json" not in item

	async def test_graceful_degradation_null_question_fields(self, authed_client, mock_frappe):
		"""Items without Review Item records return null question fields."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "orphan-uuid-no-review-item",
					"lesson_id": "LES-99999",
					"question_text": None,
					"choices": [],
					"correct_choice": None,
				}
			],
			"has_more": False,
		}

		resp = await client.get("/api/v1/reviews/SUB-00001")

		assert resp.status_code == 200
		item = resp.json()["items"][0]
		assert item["question_text"] is None
		assert item["choices"] == []
		assert item["correct_choice"] is None

	async def test_stability_difficulty_not_in_response(self, authed_client, mock_frappe):
		"""FR-011: stability and difficulty fields are NOT present in response."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "test-uuid",
					"lesson_id": "LES-00001",
					"question_text": "Test question",
					"choices": ["A", "B"],
					"correct_choice": 1,
				}
			],
			"has_more": False,
		}

		resp = await client.get("/api/v1/reviews/SUB-00001")

		assert resp.status_code == 200
		item = resp.json()["items"][0]
		assert "stability" not in item
		assert "difficulty" not in item

	async def test_multiple_question_items_in_single_response(self, authed_client, mock_frappe):
		"""Response can contain multiple QUESTION items from different lessons."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "uuid-q1",
					"lesson_id": "LES-001",
					"question_text": "What is 2+2?",
					"choices": ["3", "4", "5"],
					"correct_choice": 2,
				},
				{
					"item_id": "uuid-q2",
					"lesson_id": "LES-001",
					"question_text": "What is 3+3?",
					"choices": ["5", "6", "7", "8"],
					"correct_choice": 2,
				},
				{
					"item_id": "uuid-q3",
					"lesson_id": "LES-002",
					"question_text": None,
					"choices": [],
					"correct_choice": None,
				},
			],
			"has_more": True,
		}

		resp = await client.get("/api/v1/reviews/SUB-00001")

		assert resp.status_code == 200
		data = resp.json()
		assert len(data["items"]) == 3
		assert data["has_more"] is True

		# First QUESTION item has MCQ fields
		assert data["items"][0]["choices"] == ["3", "4", "5"]
		assert data["items"][0]["correct_choice"] == 2

		# Second QUESTION item has 4 choices
		assert data["items"][1]["choices"] == ["5", "6", "7", "8"]
		assert data["items"][1]["lesson_id"] == "LES-001"

		# Third item degrades gracefully (no review item record)
		assert data["items"][2]["question_text"] is None
		assert data["items"][2]["choices"] == []
		assert data["items"][2]["correct_choice"] is None
