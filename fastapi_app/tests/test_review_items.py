"""Tests for enriched review item response (Phase 3 — US1).

Tests verify the GET /api/v1/reviews/{subject} endpoint returns
question data (question_text, choices, correct_choice, content_json)
from the Review Item table, and that items without Review Item records
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
					"stage_id": "row-abc123",
					"lesson_id": "LES-00042",
					"stage_type": "QUESTION",
					"question_text": "كم عظمة في جسم الانسان",
					"choices": ["10", "12", "14"],
					"correct_choice": 1,
					"content_json": None,
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
		assert item["stage_type"] == "QUESTION"
		assert item["question_text"] == "كم عظمة في جسم الانسان"
		assert item["choices"] == ["10", "12", "14"]
		assert item["correct_choice"] == 1
		assert item["content_json"] is None

	async def test_fill_blank_stage_returns_content_json(self, authed_client, mock_frappe):
		"""FILL_BLANK stage items include content_json with blank data."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "cebaff25-9064-4636-a3c4-c618427f5fef",
					"stage_id": "row-def456",
					"lesson_id": "LES-00043",
					"stage_type": "FILL_BLANK",
					"question_text": "مرحب كيفك",
					"choices": [],
					"correct_choice": None,
					"content_json": {
						"blank_from": 5,
						"blank_to": 9,
						"correct_word": "كيفك",
						"distractors": ["طيب"],
					},
				}
			],
			"has_more": False,
		}

		resp = await client.get("/api/v1/reviews/SUB-00001")

		assert resp.status_code == 200
		item = resp.json()["items"][0]
		assert item["stage_type"] == "FILL_BLANK"
		assert item["question_text"] == "مرحب كيفك"
		assert item["choices"] == []
		assert item["correct_choice"] is None
		assert item["content_json"]["blank_from"] == 5
		assert item["content_json"]["correct_word"] == "كيفك"
		assert item["content_json"]["distractors"] == ["طيب"]

	async def test_matching_stage_returns_content_json(self, authed_client, mock_frappe):
		"""MATCHING stage items include content_json with pair data."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "d1e2f3a4-b5c6-7890-abcd-ef1234567890",
					"stage_id": "row-ghi789",
					"lesson_id": "LES-00044",
					"stage_type": "MATCHING",
					"question_text": "Match the pairs",
					"choices": [],
					"correct_choice": None,
					"content_json": {"left": "cat", "right": "قطة"},
				}
			],
			"has_more": False,
		}

		resp = await client.get("/api/v1/reviews/SUB-00001")

		assert resp.status_code == 200
		item = resp.json()["items"][0]
		assert item["stage_type"] == "MATCHING"
		assert item["content_json"]["left"] == "cat"
		assert item["content_json"]["right"] == "قطة"

	async def test_graceful_degradation_null_question_fields(self, authed_client, mock_frappe):
		"""Items without Review Item records return null question fields."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "orphan-uuid-no-review-item",
					"stage_id": "row-orphan",
					"lesson_id": "LES-99999",
					"stage_type": "QUESTION",
					"question_text": None,
					"choices": [],
					"correct_choice": None,
					"content_json": None,
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
		assert item["content_json"] is None

	async def test_stability_difficulty_not_in_response(self, authed_client, mock_frappe):
		"""FR-011: stability and difficulty fields are NOT present in response."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "test-uuid",
					"stage_id": "row-test",
					"lesson_id": "LES-00001",
					"stage_type": "QUESTION",
					"question_text": "Test question",
					"choices": ["A", "B"],
					"correct_choice": 1,
					"content_json": None,
				}
			],
			"has_more": False,
		}

		resp = await client.get("/api/v1/reviews/SUB-00001")

		assert resp.status_code == 200
		item = resp.json()["items"][0]
		assert "stability" not in item
		assert "difficulty" not in item

	async def test_mixed_stage_types_in_single_response(self, authed_client, mock_frappe):
		"""Response can contain multiple stage types simultaneously."""
		client, token, player_id, family_id = authed_client
		deps_module._frappe_client = mock_frappe

		mock_frappe.call.return_value = {
			"items": [
				{
					"item_id": "uuid-q1",
					"stage_id": "row-1",
					"lesson_id": "LES-001",
					"stage_type": "QUESTION",
					"question_text": "What is 2+2?",
					"choices": ["3", "4", "5"],
					"correct_choice": 2,
					"content_json": None,
				},
				{
					"item_id": "uuid-fb1",
					"stage_id": "row-2",
					"lesson_id": "LES-001",
					"stage_type": "FILL_BLANK",
					"question_text": "Hello ___",
					"choices": [],
					"correct_choice": None,
					"content_json": {
						"blank_from": 6,
						"blank_to": 9,
						"correct_word": "world",
						"distractors": [],
					},
				},
				{
					"item_id": "uuid-orphan",
					"stage_id": "row-3",
					"lesson_id": "LES-002",
					"stage_type": "MATCHING",
					"question_text": None,
					"choices": [],
					"correct_choice": None,
					"content_json": None,
				},
			],
			"has_more": True,
		}

		resp = await client.get("/api/v1/reviews/SUB-00001")

		assert resp.status_code == 200
		data = resp.json()
		assert len(data["items"]) == 3
		assert data["has_more"] is True

		# QUESTION item has MCQ fields
		assert data["items"][0]["choices"] == ["3", "4", "5"]
		assert data["items"][0]["correct_choice"] == 2

		# FILL_BLANK item has content_json
		assert data["items"][1]["content_json"]["correct_word"] == "world"

		# Orphan item degrades gracefully
		assert data["items"][2]["question_text"] is None
		assert data["items"][2]["content_json"] is None
