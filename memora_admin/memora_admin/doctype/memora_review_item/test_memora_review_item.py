# Copyright (c) 2026, corex and Contributors
# See license.txt

import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from memora_admin.api.review_items import (
	_compute_lesson_content_hash,
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

	def _make_lesson_doc(self, stages, is_reviewable=1, content_hash=None):
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
			is_reviewable=is_reviewable,
			content_hash=content_hash,
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

	def _make_lesson_doc(self, stages, is_reviewable=1, content_hash=None):
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
			is_reviewable=is_reviewable,
			content_hash=content_hash,
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


# ---------------------------------------------------------------------------
# Phase 6: Tests — Review Item Gap-Filling (T031–T034)
# ---------------------------------------------------------------------------


class TestIsReviewableFiltering(FrappeTestCase):
	"""T031: is_reviewable filtering — lessons with is_reviewable=0 should
	produce no Review Items, and toggling to 0 should delete existing items."""

	def setUp(self):
		super().setUp()
		self._cleanup_items = []

	def tearDown(self):
		for item_id in self._cleanup_items:
			if frappe.db.exists("Memora Review Item", item_id):
				frappe.delete_doc("Memora Review Item", item_id, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def _make_lesson_doc(self, stages, is_reviewable=1, content_hash=None):
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
			is_reviewable=is_reviewable,
			content_hash=content_hash,
		)

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_is_reviewable_zero_creates_no_items(self, mock_skip):
		"""Lesson with is_reviewable=0 → sync returns created=0, no items in DB."""
		mock_skip.return_value = set()

		stages = [
			_make_stage("QUESTION", {
				"question": "Should not appear?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "a0310000-0000-0000-0000-000000000001"},
				],
			}, name="stage-031a"),
		]
		doc = self._make_lesson_doc(stages, is_reviewable=0)
		self._cleanup_items.append("a0310000-0000-0000-0000-000000000001")

		result = sync_review_items(doc)

		self.assertEqual(result["created"], 0)
		self.assertEqual(result["updated"], 0)
		self.assertFalse(frappe.db.exists("Memora Review Item", "a0310000-0000-0000-0000-000000000001"))

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_is_reviewable_one_creates_items(self, mock_skip):
		"""Lesson with is_reviewable=1 → items created normally."""
		mock_skip.return_value = set()

		stages = [
			_make_stage("QUESTION", {
				"question": "Should appear?",
				"answers": [
					{"text": "Yes", "is_correct": True, "item_id": "a0310000-0000-0000-0000-000000000002"},
				],
			}, name="stage-031b"),
		]
		doc = self._make_lesson_doc(stages, is_reviewable=1)
		self._cleanup_items.append("a0310000-0000-0000-0000-000000000002")

		result = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result["created"], 1)
		self.assertTrue(frappe.db.exists("Memora Review Item", "a0310000-0000-0000-0000-000000000002"))

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_toggle_is_reviewable_deletes_existing(self, mock_skip):
		"""Create items with is_reviewable=1, toggle to 0 → all items deleted."""
		mock_skip.return_value = set()

		stages = [
			_make_stage("QUESTION", {
				"question": "Toggle test?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "a0310000-0000-0000-0000-000000000003"},
					{"text": "B", "is_correct": False, "item_id": "a0310000-0000-0000-0000-000000000004"},
				],
			}, name="stage-031c"),
		]
		doc = self._make_lesson_doc(stages, is_reviewable=1)
		self._cleanup_items.extend([
			"a0310000-0000-0000-0000-000000000003",
			"a0310000-0000-0000-0000-000000000004",
		])

		# First sync: items created
		result1 = sync_review_items(doc)
		frappe.db.commit()
		self.assertEqual(result1["created"], 2)

		# Toggle to non-reviewable
		doc.is_reviewable = 0
		result2 = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result2["created"], 0)
		self.assertEqual(result2["deleted"], 2)
		self.assertFalse(frappe.db.exists("Memora Review Item", "a0310000-0000-0000-0000-000000000003"))
		self.assertFalse(frappe.db.exists("Memora Review Item", "a0310000-0000-0000-0000-000000000004"))


class TestContentHashDebounce(FrappeTestCase):
	"""T032: content_hash debounce — duplicate saves with unchanged content
	are skipped; changed content triggers re-extraction."""

	def setUp(self):
		super().setUp()
		self._cleanup_items = []

	def tearDown(self):
		for item_id in self._cleanup_items:
			if frappe.db.exists("Memora Review Item", item_id):
				frappe.delete_doc("Memora Review Item", item_id, force=True, ignore_permissions=True)
		# Reset content_hash on the lesson so it doesn't interfere with other tests
		real = frappe.db.get_value("Memora Lesson", {}, "name")
		if real:
			frappe.db.set_value("Memora Lesson", real, "content_hash", None, update_modified=False)
		frappe.db.commit()
		super().tearDown()

	def _make_lesson_doc(self, stages, content_hash=None):
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
			is_reviewable=1,
			content_hash=content_hash,
		)

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_first_sync_runs_with_null_content_hash(self, mock_skip):
		"""First sync with content_hash=None → extraction runs, items created."""
		mock_skip.return_value = set()

		stages = [
			_make_stage("QUESTION", {
				"question": "Debounce test?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "a0320000-0000-0000-0000-000000000001"},
				],
			}, name="stage-032a"),
		]
		doc = self._make_lesson_doc(stages, content_hash=None)
		self._cleanup_items.append("a0320000-0000-0000-0000-000000000001")

		result = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result["created"], 1)
		self.assertTrue(frappe.db.exists("Memora Review Item", "a0320000-0000-0000-0000-000000000001"))

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_same_content_debounced(self, mock_skip):
		"""Second sync with matching content_hash → skipped (returns all zeros)."""
		mock_skip.return_value = set()

		stages = [
			_make_stage("QUESTION", {
				"question": "Debounce test?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "a0320000-0000-0000-0000-000000000002"},
				],
			}, name="stage-032b"),
		]

		# First sync: content_hash=None → runs extraction
		doc = self._make_lesson_doc(stages, content_hash=None)
		self._cleanup_items.append("a0320000-0000-0000-0000-000000000002")
		sync_review_items(doc)
		frappe.db.commit()

		# Compute the hash that was saved by the first sync
		computed_hash = _compute_lesson_content_hash(stages)

		# Second sync: same content, matching hash → debounced
		doc.content_hash = computed_hash
		result = sync_review_items(doc)

		self.assertEqual(result["created"], 0)
		self.assertEqual(result["updated"], 0)
		self.assertEqual(result["deleted"], 0)

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_changed_content_triggers_reextraction(self, mock_skip):
		"""Changed stages with stale content_hash → re-extraction runs."""
		mock_skip.return_value = set()

		stages_v1 = [
			_make_stage("QUESTION", {
				"question": "Version 1?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "a0320000-0000-0000-0000-000000000003"},
				],
			}, name="stage-032c"),
		]

		# First sync
		doc = self._make_lesson_doc(stages_v1, content_hash=None)
		self._cleanup_items.extend([
			"a0320000-0000-0000-0000-000000000003",
			"a0320000-0000-0000-0000-000000000004",
		])
		sync_review_items(doc)
		frappe.db.commit()

		old_hash = _compute_lesson_content_hash(stages_v1)

		# Change stages (different question + item_id)
		stages_v2 = [
			_make_stage("QUESTION", {
				"question": "Version 2?",
				"answers": [
					{"text": "B", "is_correct": True, "item_id": "a0320000-0000-0000-0000-000000000004"},
				],
			}, name="stage-032c"),
		]
		doc.stages = stages_v2
		doc.content_hash = old_hash  # stale hash from v1

		result = sync_review_items(doc)
		frappe.db.commit()

		# Old item should be deleted, new item created
		self.assertEqual(result["created"], 1)
		self.assertEqual(result["deleted"], 1)
		self.assertFalse(frappe.db.exists("Memora Review Item", "a0320000-0000-0000-0000-000000000003"))
		self.assertTrue(frappe.db.exists("Memora Review Item", "a0320000-0000-0000-0000-000000000004"))


class TestMindmapExtraction(FrappeTestCase):
	"""T033: MINDMAP recursive extraction — nested children[] with item_ids
	at every depth level are all extracted into Review Items."""

	def setUp(self):
		super().setUp()
		self._cleanup_items = []

	def tearDown(self):
		for item_id in self._cleanup_items:
			if frappe.db.exists("Memora Review Item", item_id):
				frappe.delete_doc("Memora Review Item", item_id, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def test_mindmap_unit_extraction_nested_children(self):
		"""MINDMAP with 3 levels of nesting → all item_ids extracted."""
		stage = _make_stage("MINDMAP", {
			"instruction": "أكمل خريطة المفاهيم",
			"central": "الموضوع",
			"children": [
				{
					"text": "فرع1",
					"item_id": "a0330000-0000-0000-0000-000000000001",
					"children": [
						{
							"text": "فرع1.1",
							"item_id": "a0330000-0000-0000-0000-000000000002",
							"children": [
								{
									"text": "فرع1.1.1",
									"item_id": "a0330000-0000-0000-0000-000000000003",
								}
							],
						}
					],
				},
				{
					"text": "فرع2",
					"item_id": "a0330000-0000-0000-0000-000000000004",
				},
			],
		})

		items = extract_items_from_stage(stage)

		self.assertEqual(len(items), 4)
		extracted_ids = {i["item_id"] for i in items}
		self.assertEqual(extracted_ids, {
			"a0330000-0000-0000-0000-000000000001",
			"a0330000-0000-0000-0000-000000000002",
			"a0330000-0000-0000-0000-000000000003",
			"a0330000-0000-0000-0000-000000000004",
		})

		# All items should have the instruction as question_text
		for item in items:
			self.assertEqual(item["stage_type"], "MINDMAP")
			self.assertEqual(item["question_text"], "أكمل خريطة المفاهيم")
			self.assertIsNotNone(item["content_json"])

	def test_mindmap_nodes_without_item_id_skipped(self):
		"""Nodes without item_id are traversed but not extracted."""
		stage = _make_stage("MINDMAP", {
			"central": "Root",
			"children": [
				{
					"text": "No ID node",
					"children": [
						{"text": "Leaf with ID", "item_id": "a0330000-0000-0000-0000-000000000005"},
					],
				},
			],
		})

		items = extract_items_from_stage(stage)

		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]["item_id"], "a0330000-0000-0000-0000-000000000005")

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_mindmap_integration_via_sync(self, mock_skip):
		"""MINDMAP extraction works end-to-end via sync_review_items."""
		mock_skip.return_value = set()

		real = frappe.db.get_value(
			"Memora Lesson", {}, ["name", "subject", "track", "unit", "topic"], as_dict=True
		)
		stages = [
			_make_stage("MINDMAP", {
				"instruction": "خريطة ذهنية",
				"children": [
					{
						"text": "A",
						"item_id": "a0330000-0000-0000-0000-000000000006",
						"children": [
							{"text": "A1", "item_id": "a0330000-0000-0000-0000-000000000007"},
						],
					},
				],
			}, name="stage-033mm"),
		]
		doc = SimpleNamespace(
			name=real.name,
			subject=real.subject,
			track=real.track,
			unit=real.unit,
			topic=real.topic,
			stages=stages,
			is_reviewable=1,
			content_hash=None,
		)
		self._cleanup_items.extend([
			"a0330000-0000-0000-0000-000000000006",
			"a0330000-0000-0000-0000-000000000007",
		])

		result = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result["created"], 2)
		self.assertTrue(frappe.db.exists("Memora Review Item", "a0330000-0000-0000-0000-000000000006"))
		self.assertTrue(frappe.db.exists("Memora Review Item", "a0330000-0000-0000-0000-000000000007"))


class TestPracticeLogCascade(FrappeTestCase):
	"""T034: Practice Log cascade deletion — deleting Review Items also
	deletes associated Practice Log rows."""

	def setUp(self):
		super().setUp()
		self._cleanup_items = []

	def tearDown(self):
		for item_id in self._cleanup_items:
			if frappe.db.exists("Memora Review Item", item_id):
				frappe.delete_doc("Memora Review Item", item_id, force=True, ignore_permissions=True)
			# Also clean up any leftover Practice Log rows
			frappe.db.sql(
				"DELETE FROM `tabMemora Practice Log` WHERE item_id = %s",
				(item_id,),
			)
		frappe.db.commit()
		super().tearDown()

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_cascade_deletes_practice_log_rows(self, mock_skip):
		"""Delete Review Items → associated Practice Log rows also deleted."""
		mock_skip.return_value = set()

		real = frappe.db.get_value(
			"Memora Lesson", {}, ["name", "subject", "track", "unit", "topic"], as_dict=True
		)
		stages = [
			_make_stage("QUESTION", {
				"question": "Cascade test?",
				"answers": [
					{"text": "A", "is_correct": True, "item_id": "a0340000-0000-0000-0000-000000000001"},
					{"text": "B", "is_correct": False, "item_id": "a0340000-0000-0000-0000-000000000002"},
				],
			}, name="stage-034a"),
		]
		doc = SimpleNamespace(
			name=real.name,
			subject=real.subject,
			track=real.track,
			unit=real.unit,
			topic=real.topic,
			stages=stages,
			is_reviewable=1,
			content_hash=None,
		)
		self._cleanup_items.extend([
			"a0340000-0000-0000-0000-000000000001",
			"a0340000-0000-0000-0000-000000000002",
		])

		# Create Review Items
		sync_review_items(doc)
		frappe.db.commit()
		self.assertTrue(frappe.db.exists("Memora Review Item", "a0340000-0000-0000-0000-000000000001"))
		self.assertTrue(frappe.db.exists("Memora Review Item", "a0340000-0000-0000-0000-000000000002"))

		# Insert Practice Log rows for these items
		frappe.db.sql("""
			INSERT INTO `tabMemora Practice Log`
				(player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
			VALUES
				('PLAYER-TEST-034', 'a0340000-0000-0000-0000-000000000001', NOW(), NOW(), 'Correct', 1, 1),
				('PLAYER-TEST-034', 'a0340000-0000-0000-0000-000000000002', NOW(), NOW(), 'Incorrect', 2, 0)
		""")
		frappe.db.commit()

		# Verify Practice Log rows exist
		pl_count = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabMemora Practice Log` WHERE item_id IN ('a0340000-0000-0000-0000-000000000001', 'a0340000-0000-0000-0000-000000000002')"
		)[0][0]
		self.assertEqual(pl_count, 2)

		# Delete Review Items for this lesson (triggers cascade)
		from memora_admin.api.review_items import delete_review_items_for_lesson

		count = delete_review_items_for_lesson(real.name)
		frappe.db.commit()

		self.assertEqual(count, 2)

		# Verify Practice Log rows are also deleted
		pl_count_after = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabMemora Practice Log` WHERE item_id IN ('a0340000-0000-0000-0000-000000000001', 'a0340000-0000-0000-0000-000000000002')"
		)[0][0]
		self.assertEqual(pl_count_after, 0)

	@patch("memora_admin.api.review_items._get_globally_skippable_types")
	def test_orphan_removal_cascades_to_practice_log(self, mock_skip):
		"""Removing an item via re-sync (orphan deletion) also cascades to Practice Log."""
		mock_skip.return_value = set()

		real = frappe.db.get_value(
			"Memora Lesson", {}, ["name", "subject", "track", "unit", "topic"], as_dict=True
		)
		stages = [
			_make_stage("QUESTION", {
				"question": "Orphan cascade?",
				"answers": [
					{"text": "Keep", "is_correct": True, "item_id": "a0340000-0000-0000-0000-000000000003"},
					{"text": "Remove", "is_correct": False, "item_id": "a0340000-0000-0000-0000-000000000004"},
				],
			}, name="stage-034b"),
		]
		doc = SimpleNamespace(
			name=real.name,
			subject=real.subject,
			track=real.track,
			unit=real.unit,
			topic=real.topic,
			stages=stages,
			is_reviewable=1,
			content_hash=None,
		)
		self._cleanup_items.extend([
			"a0340000-0000-0000-0000-000000000003",
			"a0340000-0000-0000-0000-000000000004",
		])

		# Create both Review Items
		sync_review_items(doc)
		frappe.db.commit()

		# Add Practice Log row for the item that will become an orphan
		frappe.db.sql("""
			INSERT INTO `tabMemora Practice Log`
				(player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
			VALUES
				('PLAYER-TEST-034B', 'a0340000-0000-0000-0000-000000000004', NOW(), NOW(), 'Correct', 3, 2)
		""")
		frappe.db.commit()

		# Re-sync with only the first answer (second becomes orphan)
		stages_v2 = [
			_make_stage("QUESTION", {
				"question": "Orphan cascade?",
				"answers": [
					{"text": "Keep", "is_correct": True, "item_id": "a0340000-0000-0000-0000-000000000003"},
				],
			}, name="stage-034b"),
		]
		doc.stages = stages_v2
		doc.content_hash = None  # force re-extraction

		result = sync_review_items(doc)
		frappe.db.commit()

		self.assertEqual(result["deleted"], 1)

		# Orphan Review Item gone
		self.assertFalse(frappe.db.exists("Memora Review Item", "a0340000-0000-0000-0000-000000000004"))

		# Practice Log row for orphan also gone
		pl_count = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabMemora Practice Log` WHERE item_id = 'a0340000-0000-0000-0000-000000000004'"
		)[0][0]
		self.assertEqual(pl_count, 0)

		# Kept item still exists
		self.assertTrue(frappe.db.exists("Memora Review Item", "a0340000-0000-0000-0000-000000000003"))
