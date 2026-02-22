# Copyright (c) 2026, corex and Contributors
# See license.txt

import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from memora_admin.api.review_items import (
	extract_items_from_stage,
	sync_review_items,
)


def _make_stage(stage_type, config_json, name="test-stage-001", is_skippable=0):
	"""Create a mock stage object for testing extraction."""
	return SimpleNamespace(
		name=name,
		stage_type=stage_type,
		config_json=json.dumps(config_json) if isinstance(config_json, dict) else config_json,
		is_skippable=is_skippable,
	)


class TestItemExtraction(FrappeTestCase):
	"""T014: Unit tests for item extraction functions."""

	def test_question_stage_extracts_mcq_fields(self):
		"""QUESTION stage → MCQ fields populated, content_json is None."""
		stage = _make_stage("QUESTION", {
			"instruction": "اختر الإجابة الصحيحة",
			"question": "كم عظمة في جسم الانسان",
			"answers": [
				{"text": "10", "is_correct": True, "item_id": "aaaa0000-0000-0000-0000-000000000001"},
				{"text": "12", "is_correct": False, "item_id": "aaaa0000-0000-0000-0000-000000000002"},
				{"text": "14", "is_correct": False, "item_id": "aaaa0000-0000-0000-0000-000000000003"},
			],
		})

		items = extract_items_from_stage(stage)

		self.assertEqual(len(items), 3)
		for item in items:
			self.assertEqual(item["stage_type"], "QUESTION")
			self.assertEqual(item["question_text"], "كم عظمة في جسم الانسان")
			self.assertEqual(item["choice_1"], "10")
			self.assertEqual(item["choice_2"], "12")
			self.assertEqual(item["choice_3"], "14")
			self.assertIsNone(item["choice_4"])
			self.assertEqual(item["correct_choice"], 1)
			self.assertIsNone(item["content_json"])

		# Each answer has its own item_id
		ids = {i["item_id"] for i in items}
		self.assertEqual(len(ids), 3)

	def test_fill_blank_stage_extracts_content_json(self):
		"""FILL_BLANK stage → content_json with blank data, MCQ fields are None."""
		stage = _make_stage("FILL_BLANK", {
			"instruction": "أكمل الفراغات التالية",
			"text": "مرحب كيفك",
			"blanks": [
				{"from": 5, "to": 9, "item_id": "bbbb0000-0000-0000-0000-000000000001"},
			],
			"distractors": ["طيب"],
		})

		items = extract_items_from_stage(stage)

		self.assertEqual(len(items), 1)
		item = items[0]
		self.assertEqual(item["item_id"], "bbbb0000-0000-0000-0000-000000000001")
		self.assertEqual(item["stage_type"], "FILL_BLANK")
		self.assertEqual(item["question_text"], "مرحب كيفك")
		self.assertIsNone(item["choice_1"])
		self.assertIsNone(item["correct_choice"])

		cj = json.loads(item["content_json"])
		self.assertEqual(cj["blank_from"], 5)
		self.assertEqual(cj["blank_to"], 9)
		self.assertEqual(cj["correct_word"], "كيفك")
		self.assertEqual(cj["distractors"], ["طيب"])

	def test_matching_stage_extracts_content_json(self):
		"""MATCHING stage → content_json with pair data."""
		stage = _make_stage("MATCHING", {
			"instruction": "طابق العناصر",
			"pairs": [
				{"id": "1", "left": "cat", "right": "قطة", "item_id": "cccc0000-0000-0000-0000-000000000001"},
				{"id": "2", "left": "dog", "right": "كلب", "item_id": "cccc0000-0000-0000-0000-000000000002"},
			],
		})

		items = extract_items_from_stage(stage)

		self.assertEqual(len(items), 2)
		for item in items:
			self.assertEqual(item["stage_type"], "MATCHING")
			self.assertEqual(item["question_text"], "طابق العناصر")
			self.assertIsNone(item["choice_1"])
			cj = json.loads(item["content_json"])
			self.assertIn("left", cj)
			self.assertIn("right", cj)

	def test_unknown_stage_type_uses_content_json_fallback(self):
		"""Unknown stage type with item_ids → content_json fallback."""
		stage = _make_stage("REVEAL", {
			"text": "Some reveal content",
			"items": [
				{"item_id": "dddd0000-0000-0000-0000-000000000001", "data": "reveal1"},
			],
		})

		items = extract_items_from_stage(stage)

		self.assertEqual(len(items), 1)
		item = items[0]
		self.assertEqual(item["stage_type"], "REVEAL")
		self.assertEqual(item["question_text"], "Some reveal content")
		self.assertIsNotNone(item["content_json"])

	def test_empty_config_json_returns_no_items(self):
		"""Empty or null config_json → no items extracted."""
		stage_empty = _make_stage("QUESTION", "")
		self.assertEqual(extract_items_from_stage(stage_empty), [])

		stage_none = _make_stage("QUESTION", None)
		stage_none.config_json = None
		self.assertEqual(extract_items_from_stage(stage_none), [])

	def test_invalid_json_returns_no_items(self):
		"""Malformed JSON → no items extracted (no crash)."""
		stage = _make_stage("QUESTION", "not valid json")
		stage.config_json = "not valid json"
		self.assertEqual(extract_items_from_stage(stage), [])

	def test_question_with_four_choices(self):
		"""QUESTION with 4 answers → all 4 choice fields populated."""
		stage = _make_stage("QUESTION", {
			"question": "Test?",
			"answers": [
				{"text": "A", "is_correct": False, "item_id": "eeee0000-0000-0000-0000-000000000001"},
				{"text": "B", "is_correct": True, "item_id": "eeee0000-0000-0000-0000-000000000002"},
				{"text": "C", "is_correct": False, "item_id": "eeee0000-0000-0000-0000-000000000003"},
				{"text": "D", "is_correct": False, "item_id": "eeee0000-0000-0000-0000-000000000004"},
			],
		})

		items = extract_items_from_stage(stage)
		self.assertEqual(len(items), 4)
		self.assertEqual(items[0]["choice_4"], "D")
		self.assertEqual(items[0]["correct_choice"], 2)


class TestSyncReviewItems(FrappeTestCase):
	"""T015: Integration tests for sync_review_items orchestrator."""

	def setUp(self):
		super().setUp()
		self._cleanup_items = []

	def tearDown(self):
		# Clean up any items created during tests
		for item_id in self._cleanup_items:
			if frappe.db.exists("Memora Review Item", item_id):
				frappe.delete_doc("Memora Review Item", item_id, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def _make_lesson_doc(self, stages):
		"""Create a mock lesson doc with given stages."""
		# Use a real lesson for hierarchy fields
		real = frappe.db.get_value(
			"Memora Lesson", {}, ["name", "subject", "track", "unit", "topic"], as_dict=True
		)
		doc = SimpleNamespace(
			name=real.name,
			subject=real.subject,
			track=real.track,
			unit=real.unit,
			topic=real.topic,
			stages=stages,
		)
		return doc

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_sync_creates_items_from_stages(self, mock_skip):
		"""Save lesson with stages → Review Items created with correct hierarchy refs."""
		mock_skip.return_value = {"INFORMATION", "MINDMAP", "SENTENCE_BUILDER"}

		stages = [
			_make_stage("QUESTION", {
				"question": "Test Q?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "11110000-0000-0000-0000-000000000001"},
					{"text": "B", "is_correct": False, "item_id": "11110000-0000-0000-0000-000000000002"},
				],
			}, name="stage-q1"),
			_make_stage("FILL_BLANK", {
				"text": "Hello World",
				"blanks": [{"from": 6, "to": 11, "item_id": "11110000-0000-0000-0000-000000000003"}],
				"distractors": ["Earth"],
			}, name="stage-fb1"),
		]

		doc = self._make_lesson_doc(stages)

		# Track for cleanup
		self._cleanup_items.extend([
			"11110000-0000-0000-0000-000000000001",
			"11110000-0000-0000-0000-000000000002",
			"11110000-0000-0000-0000-000000000003",
		])

		result = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result["created"], 3)
		self.assertEqual(result["updated"], 0)
		self.assertEqual(result["deleted"], 0)

		# Verify items exist with correct hierarchy
		items = frappe.get_all("Memora Review Item", filters={"lesson": doc.name}, fields=["*"])
		created_ids = {i.item_id for i in items}
		self.assertIn("11110000-0000-0000-0000-000000000001", created_ids)
		self.assertIn("11110000-0000-0000-0000-000000000003", created_ids)

		# Check hierarchy fields
		for item in items:
			self.assertEqual(item.subject, doc.subject)
			self.assertEqual(item.track, doc.track)
			self.assertEqual(item.unit, doc.unit)
			self.assertEqual(item.topic, doc.topic)
			self.assertEqual(item.lesson, doc.name)

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_resync_deletes_orphans(self, mock_skip):
		"""Re-save with item removed → orphan deleted."""
		mock_skip.return_value = {"INFORMATION", "MINDMAP", "SENTENCE_BUILDER"}

		# First sync: 2 items
		stages = [
			_make_stage("QUESTION", {
				"question": "Q?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "22220000-0000-0000-0000-000000000001"},
					{"text": "B", "is_correct": False, "item_id": "22220000-0000-0000-0000-000000000002"},
				],
			}, name="stage-q2"),
		]
		doc = self._make_lesson_doc(stages)
		self._cleanup_items.extend([
			"22220000-0000-0000-0000-000000000001",
			"22220000-0000-0000-0000-000000000002",
		])

		sync_review_items(doc)
		frappe.db.commit()

		# Second sync: only 1 answer (removed the second)
		stages2 = [
			_make_stage("QUESTION", {
				"question": "Q?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "22220000-0000-0000-0000-000000000001"},
				],
			}, name="stage-q2"),
		]
		doc.stages = stages2

		result = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result["deleted"], 1)
		# Orphan should be gone
		self.assertFalse(frappe.db.exists("Memora Review Item", "22220000-0000-0000-0000-000000000002"))
		# Remaining item still exists
		self.assertTrue(frappe.db.exists("Memora Review Item", "22220000-0000-0000-0000-000000000001"))

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_skippable_stage_items_not_synced(self, mock_skip):
		"""Stage switched to skippable → its items are not synced (deleted if existed)."""
		mock_skip.return_value = {"INFORMATION", "MINDMAP", "SENTENCE_BUILDER"}

		# First sync: create items from a non-skippable stage
		stages = [
			_make_stage("MATCHING", {
				"instruction": "Match",
				"pairs": [{"left": "a", "right": "b", "item_id": "33330000-0000-0000-0000-000000000001"}],
			}, name="stage-m1", is_skippable=0),
		]
		doc = self._make_lesson_doc(stages)
		self._cleanup_items.append("33330000-0000-0000-0000-000000000001")

		sync_review_items(doc)
		frappe.db.commit()
		self.assertTrue(frappe.db.exists("Memora Review Item", "33330000-0000-0000-0000-000000000001"))

		# Second sync: stage is now skippable
		stages[0].is_skippable = 1
		result = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result["deleted"], 1)
		self.assertFalse(frappe.db.exists("Memora Review Item", "33330000-0000-0000-0000-000000000001"))


class TestDeleteReviewItems(FrappeTestCase):
	"""T017: Integration tests for cascade deletion (US3)."""

	def setUp(self):
		super().setUp()
		self._cleanup_items = []

	def tearDown(self):
		for item_id in self._cleanup_items:
			if frappe.db.exists("Memora Review Item", item_id):
				frappe.delete_doc("Memora Review Item", item_id, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def _make_lesson_doc(self, stages):
		"""Create a mock lesson doc with given stages."""
		real = frappe.db.get_value(
			"Memora Lesson", {}, ["name", "subject", "track", "unit", "topic"], as_dict=True
		)
		return SimpleNamespace(
			name=real.name,
			subject=real.subject,
			track=real.track,
			unit=real.unit,
			topic=real.topic,
			stages=stages,
		)

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_delete_review_items_for_lesson(self, mock_skip):
		"""Delete a lesson → all its Review Items are removed."""
		from memora_admin.api.review_items import delete_review_items_for_lesson

		mock_skip.return_value = {"INFORMATION", "MINDMAP", "SENTENCE_BUILDER"}

		# Create items via sync
		stages = [
			_make_stage("QUESTION", {
				"question": "Delete test?",
				"answers": [
					{"text": "Yes", "is_correct": True, "item_id": "44440000-0000-0000-0000-000000000001"},
					{"text": "No", "is_correct": False, "item_id": "44440000-0000-0000-0000-000000000002"},
				],
			}, name="stage-del1"),
		]
		doc = self._make_lesson_doc(stages)
		self._cleanup_items.extend([
			"44440000-0000-0000-0000-000000000001",
			"44440000-0000-0000-0000-000000000002",
		])

		sync_review_items(doc)
		frappe.db.commit()

		# Verify items exist
		self.assertTrue(frappe.db.exists("Memora Review Item", "44440000-0000-0000-0000-000000000001"))
		self.assertTrue(frappe.db.exists("Memora Review Item", "44440000-0000-0000-0000-000000000002"))

		# Delete all items for this lesson
		count = delete_review_items_for_lesson(doc.name)
		frappe.db.commit()

		self.assertEqual(count, 2)
		self.assertFalse(frappe.db.exists("Memora Review Item", "44440000-0000-0000-0000-000000000001"))
		self.assertFalse(frappe.db.exists("Memora Review Item", "44440000-0000-0000-0000-000000000002"))

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_on_lesson_trash_calls_delete(self, mock_skip):
		"""on_trash hook triggers deletion of Review Items."""
		from memora_admin.events.review_item_sync import on_lesson_trash

		mock_skip.return_value = {"INFORMATION", "MINDMAP", "SENTENCE_BUILDER"}

		stages = [
			_make_stage("MATCHING", {
				"instruction": "Match pairs",
				"pairs": [
					{"left": "x", "right": "y", "item_id": "55550000-0000-0000-0000-000000000001"},
				],
			}, name="stage-trash1"),
		]
		doc = self._make_lesson_doc(stages)
		self._cleanup_items.append("55550000-0000-0000-0000-000000000001")

		sync_review_items(doc)
		frappe.db.commit()

		self.assertTrue(frappe.db.exists("Memora Review Item", "55550000-0000-0000-0000-000000000001"))

		# Simulate on_trash hook
		on_lesson_trash(doc, "on_trash")
		frappe.db.commit()

		self.assertFalse(frappe.db.exists("Memora Review Item", "55550000-0000-0000-0000-000000000001"))

	def test_delete_nonexistent_lesson_returns_zero(self):
		"""Deleting items for a lesson with no Review Items returns 0."""
		from memora_admin.api.review_items import delete_review_items_for_lesson

		count = delete_review_items_for_lesson("NONEXISTENT-LESSON-99999")
		self.assertEqual(count, 0)

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_stage_removal_via_sync_cleans_up_orphans(self, mock_skip):
		"""Removing a stage from a lesson and re-saving cleans up orphaned items."""
		mock_skip.return_value = {"INFORMATION", "MINDMAP", "SENTENCE_BUILDER"}

		# Sync with 2 stages
		stages = [
			_make_stage("QUESTION", {
				"question": "Keep?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "66660000-0000-0000-0000-000000000001"},
				],
			}, name="stage-keep"),
			_make_stage("FILL_BLANK", {
				"text": "Remove this",
				"blanks": [{"from": 7, "to": 11, "item_id": "66660000-0000-0000-0000-000000000002"}],
				"distractors": [],
			}, name="stage-remove"),
		]
		doc = self._make_lesson_doc(stages)
		self._cleanup_items.extend([
			"66660000-0000-0000-0000-000000000001",
			"66660000-0000-0000-0000-000000000002",
		])

		sync_review_items(doc)
		frappe.db.commit()
		self.assertEqual(frappe.db.count("Memora Review Item", {"lesson": doc.name}), 2)

		# Re-sync with only the first stage (second removed)
		doc.stages = [stages[0]]
		result = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result["deleted"], 1)
		self.assertTrue(frappe.db.exists("Memora Review Item", "66660000-0000-0000-0000-000000000001"))
		self.assertFalse(frappe.db.exists("Memora Review Item", "66660000-0000-0000-0000-000000000002"))
