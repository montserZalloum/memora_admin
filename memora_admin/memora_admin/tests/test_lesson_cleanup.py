"""Tests for lesson_cleanup.py — Review Item lifecycle and orphan management.

Covers:
- Review Item creation on QUESTION stage addition
- Review Item update on QUESTION content change
- Review Item survival when QUESTION removed but item group remains
- Review Item deletion when entire item group removed
- MATCHING pair cleanup when referenced item group is removed
- Standalone stage types (MATCHING, MINDMAP, STORY) never count as item owners
- Lesson trash cascading to Review Items
- Edge cases (empty config, malformed JSON, duplicate item_ids, etc.)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase


# ---------------------------------------------------------------------------
# Helpers — build minimal lesson doc and stage objects
# ---------------------------------------------------------------------------

def _make_stage(stage_type: str, config: dict, name: str | None = None) -> MagicMock:
	s = MagicMock()
	s.stage_type = stage_type
	s.config_json = json.dumps(config, ensure_ascii=False)
	s.name = name or frappe.generate_hash(length=10)
	s.db_update = MagicMock()
	return s


def _make_doc(stages: list, name: str = "LESSON-TEST-001", subject: str = "SUBJ-1",
			  track: str = "TRACK-1", unit: str = "UNIT-1", topic: str = "TOPIC-1") -> MagicMock:
	doc = MagicMock()
	doc.name = name
	doc.subject = subject
	doc.track = track
	doc.unit = unit
	doc.topic = topic
	doc.stages = stages
	doc.get_doc_before_save = MagicMock(return_value=None)
	return doc


def _config_question(item_id: str, question: str, answers: list[tuple[str, bool]]) -> dict:
	"""Build a QUESTION stage config_json dict."""
	return {
		"item_id": item_id,
		"question": question,
		"instruction": "اختر الإجابة الصحيحة",
		"answers": [
			{"text": text, "is_correct": correct, "item_id": frappe.generate_hash(length=8)}
			for text, correct in answers
		],
	}


def _config_info(item_id: str, text: str = "معلومة تعليمية") -> dict:
	return {"item_id": item_id, "text": text, "instruction": "اقرأ", "highlights": []}


def _config_fill_blank(item_id: str, text: str = "أكمل الفراغ") -> dict:
	return {"item_id": item_id, "text": text, "instruction": "أكمل", "blanks": []}


def _config_matching(pairs: list[dict]) -> dict:
	return {"instruction": "طابق", "pairs": pairs}


def _config_mindmap(label: str = "خريطة") -> dict:
	return {"label": label, "children": []}


def _config_story(text: str = "قصة") -> dict:
	return {"instruction": "اقرأ", "steps": [{"text": text}]}


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
from memora_admin.events.lesson_cleanup import (
	_clean_matching_refs,
	_extract_stage_item_ids,
	_sync_question_review_items,
	on_lesson_stages_updated,
	on_lesson_trash,
)


class TestExtractStageItemIds(FrappeTestCase):
	"""Tests for _extract_stage_item_ids — which item_ids are considered 'alive'."""

	def test_extracts_top_level_item_id_from_content_stages(self):
		"""INFORMATION, QUESTION, FILL_BLANK, REVEAL, SENTENCE_BUILDER item_ids are collected."""
		doc = _make_doc([
			_make_stage("INFORMATION", _config_info("id-info")),
			_make_stage("QUESTION", _config_question("id-q", "سؤال؟", [("أ", True), ("ب", False)])),
			_make_stage("FILL_BLANK", _config_fill_blank("id-fill")),
		])
		ids = _extract_stage_item_ids(doc)
		self.assertEqual(ids, {"id-info", "id-q", "id-fill"})

	def test_ignores_matching_stage(self):
		"""MATCHING item_ids (top-level or in pairs) must NOT count as ownership."""
		doc = _make_doc([
			_make_stage("INFORMATION", _config_info("id-1")),
			_make_stage("MATCHING", {
				"item_id": "matching-top",
				"pairs": [{"item_id": "id-1", "right": "أ", "left": "ب"}],
			}),
		])
		ids = _extract_stage_item_ids(doc)
		self.assertIn("id-1", ids)
		self.assertNotIn("matching-top", ids)

	def test_ignores_mindmap_stage(self):
		"""MINDMAP stage item_ids must NOT count as ownership."""
		doc = _make_doc([
			_make_stage("MINDMAP", {"item_id": "mindmap-id", "label": "test", "children": []}),
		])
		ids = _extract_stage_item_ids(doc)
		self.assertEqual(ids, set())

	def test_ignores_story_stage(self):
		"""STORY stage item_ids must NOT count as ownership."""
		doc = _make_doc([
			_make_stage("STORY", {"item_id": "story-id", "steps": []}),
		])
		ids = _extract_stage_item_ids(doc)
		self.assertEqual(ids, set())

	def test_handles_empty_stages(self):
		doc = _make_doc([])
		ids = _extract_stage_item_ids(doc)
		self.assertEqual(ids, set())

	def test_handles_malformed_json(self):
		"""Stages with invalid config_json are silently skipped."""
		s = _make_stage("INFORMATION", {})
		s.config_json = "not valid json {"
		doc = _make_doc([s])
		ids = _extract_stage_item_ids(doc)
		self.assertEqual(ids, set())

	def test_handles_missing_item_id(self):
		"""Stages with no item_id in config are skipped."""
		doc = _make_doc([
			_make_stage("INFORMATION", {"text": "no item_id here"}),
		])
		ids = _extract_stage_item_ids(doc)
		self.assertEqual(ids, set())

	def test_deduplicates_shared_item_id(self):
		"""Multiple stages sharing the same item_id produce a single entry."""
		doc = _make_doc([
			_make_stage("INFORMATION", _config_info("shared-id")),
			_make_stage("QUESTION", _config_question("shared-id", "سؤال؟", [("أ", True)])),
			_make_stage("FILL_BLANK", _config_fill_blank("shared-id")),
		])
		ids = _extract_stage_item_ids(doc)
		self.assertEqual(ids, {"shared-id"})


class TestCleanMatchingRefs(FrappeTestCase):
	"""Tests for _clean_matching_refs — removing stale MATCHING pairs."""

	def test_removes_pairs_referencing_deleted_item(self):
		"""Pairs whose item_id is no longer alive are removed."""
		matching = _make_stage("MATCHING", _config_matching([
			{"id": "1", "item_id": "alive-id", "right": "أ", "left": "ب"},
			{"id": "2", "item_id": "dead-id", "right": "ج", "left": "د"},
		]))
		doc = _make_doc([matching])
		current_ids = {"alive-id"}

		_clean_matching_refs(doc, current_ids)

		updated_config = json.loads(matching.config_json)
		self.assertEqual(len(updated_config["pairs"]), 1)
		self.assertEqual(updated_config["pairs"][0]["item_id"], "alive-id")
		matching.db_update.assert_called_once()

	def test_no_change_when_all_pairs_alive(self):
		"""If all pairs reference alive items, config is untouched."""
		matching = _make_stage("MATCHING", _config_matching([
			{"id": "1", "item_id": "id-1", "right": "أ", "left": "ب"},
			{"id": "2", "item_id": "id-2", "right": "ج", "left": "د"},
		]))
		doc = _make_doc([matching])
		current_ids = {"id-1", "id-2"}

		_clean_matching_refs(doc, current_ids)
		matching.db_update.assert_not_called()

	def test_removes_all_pairs_when_all_dead(self):
		"""All pairs removed if all referenced items are gone."""
		matching = _make_stage("MATCHING", _config_matching([
			{"id": "1", "item_id": "dead-1", "right": "أ", "left": "ب"},
			{"id": "2", "item_id": "dead-2", "right": "ج", "left": "د"},
		]))
		doc = _make_doc([matching])

		_clean_matching_refs(doc, set())

		updated_config = json.loads(matching.config_json)
		self.assertEqual(updated_config["pairs"], [])
		matching.db_update.assert_called_once()

	def test_skips_non_matching_stages(self):
		"""Non-MATCHING stages are never touched."""
		info = _make_stage("INFORMATION", _config_info("id-1"))
		doc = _make_doc([info])
		_clean_matching_refs(doc, set())
		info.db_update.assert_not_called()

	def test_handles_matching_with_no_pairs(self):
		"""MATCHING stage with empty or missing pairs is safe."""
		matching = _make_stage("MATCHING", {"instruction": "طابق"})
		doc = _make_doc([matching])
		_clean_matching_refs(doc, set())
		matching.db_update.assert_not_called()

	def test_handles_malformed_matching_config(self):
		"""MATCHING with invalid JSON is silently skipped."""
		matching = _make_stage("MATCHING", {})
		matching.config_json = "bad json"
		doc = _make_doc([matching])
		_clean_matching_refs(doc, {"id-1"})
		matching.db_update.assert_not_called()

	def test_multiple_matching_stages(self):
		"""Each MATCHING stage is cleaned independently."""
		m1 = _make_stage("MATCHING", _config_matching([
			{"id": "1", "item_id": "alive", "right": "أ", "left": "ب"},
			{"id": "2", "item_id": "dead", "right": "ج", "left": "د"},
		]), name="m1")
		m2 = _make_stage("MATCHING", _config_matching([
			{"id": "1", "item_id": "alive", "right": "ه", "left": "و"},
		]), name="m2")
		doc = _make_doc([m1, m2])

		_clean_matching_refs(doc, {"alive"})

		c1 = json.loads(m1.config_json)
		self.assertEqual(len(c1["pairs"]), 1)
		m1.db_update.assert_called_once()
		# m2 unchanged — all pairs alive
		m2.db_update.assert_not_called()


class TestSyncQuestionReviewItems(FrappeTestCase):
	"""Tests for _sync_question_review_items — create/update Review Items."""

	def _make_old_new(self, old_stages, new_stages):
		old_doc = _make_doc(old_stages, name="LESSON-SYNC")
		new_doc = _make_doc(new_stages, name="LESSON-SYNC")
		return old_doc, new_doc

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_creates_review_item_for_new_question(self, mock_frappe):
		"""Adding a new QUESTION stage creates a Review Item keyed on top-level item_id."""
		ri_mock = MagicMock()
		mock_frappe.new_doc.return_value = ri_mock
		mock_frappe.db.exists.return_value = None

		config = _config_question("group-id-1", "ما هو السؤال؟", [("أ", True), ("ب", False)])
		stage = _make_stage("QUESTION", config, name="stage-1")
		old_doc, new_doc = self._make_old_new([], [stage])

		_sync_question_review_items(old_doc, new_doc)

		mock_frappe.new_doc.assert_called_once_with("Memora Review Item")
		self.assertEqual(ri_mock.item_id, "group-id-1")
		self.assertEqual(ri_mock.question_text, "ما هو السؤال؟")
		self.assertEqual(ri_mock.choice_1, "أ")
		self.assertEqual(ri_mock.choice_2, "ب")
		self.assertEqual(ri_mock.correct_choice, 1)
		ri_mock.insert.assert_called_once_with(ignore_permissions=True)

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_updates_existing_review_item_on_question_change(self, mock_frappe):
		"""Changing question text updates the existing Review Item."""
		mock_frappe.db.exists.return_value = "group-id-1"

		old_config = _config_question("group-id-1", "سؤال قديم", [("أ", True), ("ب", False)])
		new_config = _config_question("group-id-1", "سؤال جديد", [("ج", False), ("د", True)])
		old_stage = _make_stage("QUESTION", old_config, name="stage-1")
		new_stage = _make_stage("QUESTION", new_config, name="stage-1")

		old_doc, new_doc = self._make_old_new([old_stage], [new_stage])
		_sync_question_review_items(old_doc, new_doc)

		mock_frappe.db.set_value.assert_called_once()
		call_args = mock_frappe.db.set_value.call_args
		self.assertEqual(call_args[0][0], "Memora Review Item")
		self.assertEqual(call_args[0][1], "group-id-1")
		values = call_args[0][2]
		self.assertEqual(values["question_text"], "سؤال جديد")
		self.assertEqual(values["correct_choice"], 2)

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_skips_unchanged_question(self, mock_frappe):
		"""If QUESTION config_json didn't change, no DB operations occur."""
		config = _config_question("id-1", "سؤال", [("أ", True)])
		stage = _make_stage("QUESTION", config, name="stage-1")
		# Same stage in old and new — identical config_json
		old_stage = _make_stage("QUESTION", config, name="stage-1")
		old_stage.config_json = stage.config_json  # ensure exact match

		old_doc, new_doc = self._make_old_new([old_stage], [stage])
		_sync_question_review_items(old_doc, new_doc)

		mock_frappe.db.exists.assert_not_called()
		mock_frappe.new_doc.assert_not_called()

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_skips_non_question_stages(self, mock_frappe):
		"""INFORMATION, FILL_BLANK, etc. never trigger Review Item sync."""
		old_doc, new_doc = self._make_old_new(
			[],
			[
				_make_stage("INFORMATION", _config_info("id-1")),
				_make_stage("FILL_BLANK", _config_fill_blank("id-2")),
			],
		)
		_sync_question_review_items(old_doc, new_doc)
		mock_frappe.db.exists.assert_not_called()
		mock_frappe.new_doc.assert_not_called()

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_skips_question_without_item_id(self, mock_frappe):
		"""QUESTION with no top-level item_id is skipped."""
		config = {"question": "سؤال", "answers": [{"text": "أ", "is_correct": True}]}
		stage = _make_stage("QUESTION", config, name="stage-1")
		old_doc, new_doc = self._make_old_new([], [stage])
		_sync_question_review_items(old_doc, new_doc)
		mock_frappe.new_doc.assert_not_called()

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_skips_question_without_answers(self, mock_frappe):
		"""QUESTION with item_id but no answers is skipped."""
		config = {"item_id": "id-1", "question": "سؤال", "answers": []}
		stage = _make_stage("QUESTION", config, name="stage-1")
		old_doc, new_doc = self._make_old_new([], [stage])
		_sync_question_review_items(old_doc, new_doc)
		mock_frappe.new_doc.assert_not_called()

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_pads_choices_to_four(self, mock_frappe):
		"""Review Item always gets 4 choices, padded with empty strings."""
		ri_mock = MagicMock()
		mock_frappe.new_doc.return_value = ri_mock
		mock_frappe.db.exists.return_value = None

		config = _config_question("id-1", "سؤال", [("أ", True), ("ب", False)])
		stage = _make_stage("QUESTION", config, name="s1")
		old_doc, new_doc = self._make_old_new([], [stage])
		_sync_question_review_items(old_doc, new_doc)

		self.assertEqual(ri_mock.choice_1, "أ")
		self.assertEqual(ri_mock.choice_2, "ب")
		self.assertEqual(ri_mock.choice_3, "")
		self.assertEqual(ri_mock.choice_4, "")

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_uses_top_level_item_id_not_answer_id(self, mock_frappe):
		"""Review Item is keyed on config.item_id, NOT the answer's item_id."""
		ri_mock = MagicMock()
		mock_frappe.new_doc.return_value = ri_mock
		mock_frappe.db.exists.return_value = None

		config = {
			"item_id": "group-top-level",
			"question": "سؤال",
			"answers": [
				{"text": "أ", "is_correct": True, "item_id": "answer-uuid-123"},
				{"text": "ب", "is_correct": False, "item_id": "answer-uuid-456"},
			],
		}
		stage = _make_stage("QUESTION", config, name="s1")
		old_doc, new_doc = self._make_old_new([], [stage])
		_sync_question_review_items(old_doc, new_doc)

		self.assertEqual(ri_mock.item_id, "group-top-level")

	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_handles_malformed_config(self, mock_frappe):
		"""Malformed config_json is silently skipped."""
		stage = _make_stage("QUESTION", {}, name="s1")
		stage.config_json = "{broken"
		old_doc, new_doc = self._make_old_new([], [stage])
		_sync_question_review_items(old_doc, new_doc)
		mock_frappe.new_doc.assert_not_called()


class TestOrphanCleanup(FrappeTestCase):
	"""Integration scenarios for orphan detection in on_lesson_stages_updated."""

	# -----------------------------------------------------------------------
	# Scenario: Remove QUESTION from item group, INFORMATION remains
	# Expected: Review Item SURVIVES
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup._sync_question_review_items")
	@patch("memora_admin.events.lesson_cleanup._clean_matching_refs")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_review_item_survives_when_question_removed_but_group_remains(
		self, mock_frappe, mock_clean, mock_sync, mock_delete
	):
		item_id = "group-abc"
		old_stages = [
			_make_stage("INFORMATION", _config_info(item_id), name="s1"),
			_make_stage("QUESTION", _config_question(item_id, "Q?", [("A", True)]), name="s2"),
		]
		new_stages = [
			_make_stage("INFORMATION", _config_info(item_id), name="s1"),
			# QUESTION removed
		]

		old_doc = _make_doc(old_stages, name="L1")
		new_doc = _make_doc(new_stages, name="L1")
		new_doc.get_doc_before_save.return_value = old_doc

		mock_frappe.get_all.return_value = [{"name": item_id, "item_id": item_id}]

		on_lesson_stages_updated(new_doc, "on_update")

		# Review Item should NOT be deleted — item_id still on INFORMATION stage
		mock_delete.assert_not_called()

	# -----------------------------------------------------------------------
	# Scenario: Remove entire item group (all stages with that item_id)
	# Expected: Review Item DELETED
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup._sync_question_review_items")
	@patch("memora_admin.events.lesson_cleanup._clean_matching_refs")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_review_item_deleted_when_entire_group_removed(
		self, mock_frappe, mock_clean, mock_sync, mock_delete
	):
		item_id = "group-xyz"
		old_stages = [
			_make_stage("INFORMATION", _config_info(item_id), name="s1"),
			_make_stage("QUESTION", _config_question(item_id, "Q?", [("A", True)]), name="s2"),
		]
		new_stages = []  # entire group deleted

		old_doc = _make_doc(old_stages, name="L1")
		new_doc = _make_doc(new_stages, name="L1")
		new_doc.get_doc_before_save.return_value = old_doc

		mock_frappe.get_all.return_value = [{"name": item_id, "item_id": item_id}]

		on_lesson_stages_updated(new_doc, "on_update")

		mock_delete.assert_called_once_with([item_id])

	# -----------------------------------------------------------------------
	# Scenario: Item group removed but MATCHING still references item_id
	# Expected: Review Item DELETED (MATCHING is not ownership)
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup._sync_question_review_items")
	@patch("memora_admin.events.lesson_cleanup._clean_matching_refs")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_matching_reference_does_not_keep_review_item_alive(
		self, mock_frappe, mock_clean, mock_sync, mock_delete
	):
		item_id = "group-dead"
		old_stages = [
			_make_stage("INFORMATION", _config_info(item_id), name="s1"),
			_make_stage("MATCHING", _config_matching([
				{"id": "1", "item_id": item_id, "right": "أ", "left": "ب"},
			]), name="s2"),
		]
		new_stages = [
			# INFORMATION removed — only MATCHING remains
			_make_stage("MATCHING", _config_matching([
				{"id": "1", "item_id": item_id, "right": "أ", "left": "ب"},
			]), name="s2"),
		]

		old_doc = _make_doc(old_stages, name="L1")
		new_doc = _make_doc(new_stages, name="L1")
		new_doc.get_doc_before_save.return_value = old_doc

		mock_frappe.get_all.return_value = [{"name": item_id, "item_id": item_id}]

		on_lesson_stages_updated(new_doc, "on_update")

		# item_id is only in MATCHING (standalone) → counts as orphaned
		mock_delete.assert_called_once_with([item_id])

	# -----------------------------------------------------------------------
	# Scenario: No stages changed at all
	# Expected: Early return, no DB operations
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_no_change_skips_all_processing(self, mock_frappe, mock_delete):
		config = _config_info("id-1")
		stage = _make_stage("INFORMATION", config, name="s1")
		old_stage = _make_stage("INFORMATION", config, name="s1")
		old_stage.config_json = stage.config_json

		old_doc = _make_doc([old_stage], name="L1")
		new_doc = _make_doc([stage], name="L1")
		new_doc.get_doc_before_save.return_value = old_doc

		on_lesson_stages_updated(new_doc, "on_update")

		mock_frappe.get_all.assert_not_called()
		mock_delete.assert_not_called()

	# -----------------------------------------------------------------------
	# Scenario: New lesson (no old doc)
	# Expected: Early return — nothing to orphan
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_new_lesson_skips_orphan_check(self, mock_frappe):
		new_doc = _make_doc([_make_stage("INFORMATION", _config_info("id-1"))], name="L-NEW")
		new_doc.get_doc_before_save.return_value = None

		on_lesson_stages_updated(new_doc, "on_update")

		mock_frappe.get_all.assert_not_called()

	# -----------------------------------------------------------------------
	# Scenario: Two item groups, only one is removed
	# Expected: Only that group's Review Item is deleted
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup._sync_question_review_items")
	@patch("memora_admin.events.lesson_cleanup._clean_matching_refs")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_only_removed_group_review_item_is_deleted(
		self, mock_frappe, mock_clean, mock_sync, mock_delete
	):
		old_stages = [
			_make_stage("INFORMATION", _config_info("keep-id"), name="s1"),
			_make_stage("INFORMATION", _config_info("remove-id"), name="s2"),
		]
		new_stages = [
			_make_stage("INFORMATION", _config_info("keep-id"), name="s1"),
		]

		old_doc = _make_doc(old_stages, name="L1")
		new_doc = _make_doc(new_stages, name="L1")
		new_doc.get_doc_before_save.return_value = old_doc

		mock_frappe.get_all.return_value = [
			{"name": "keep-id", "item_id": "keep-id"},
			{"name": "remove-id", "item_id": "remove-id"},
		]

		on_lesson_stages_updated(new_doc, "on_update")

		mock_delete.assert_called_once_with(["remove-id"])

	# -----------------------------------------------------------------------
	# Scenario: MATCHING stage cleaned after item group deletion
	# Expected: _clean_matching_refs is called with correct alive set
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup._sync_question_review_items")
	@patch("memora_admin.events.lesson_cleanup._clean_matching_refs")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_matching_refs_cleaned_after_group_deletion(
		self, mock_frappe, mock_clean, mock_sync, mock_delete
	):
		old_stages = [
			_make_stage("INFORMATION", _config_info("alive-id"), name="s1"),
			_make_stage("INFORMATION", _config_info("dead-id"), name="s2"),
			_make_stage("MATCHING", _config_matching([
				{"id": "1", "item_id": "alive-id", "right": "أ", "left": "ب"},
				{"id": "2", "item_id": "dead-id", "right": "ج", "left": "د"},
			]), name="s3"),
		]
		new_stages = [
			_make_stage("INFORMATION", _config_info("alive-id"), name="s1"),
			_make_stage("MATCHING", _config_matching([
				{"id": "1", "item_id": "alive-id", "right": "أ", "left": "ب"},
				{"id": "2", "item_id": "dead-id", "right": "ج", "left": "د"},
			]), name="s3"),
		]

		old_doc = _make_doc(old_stages, name="L1")
		new_doc = _make_doc(new_stages, name="L1")
		new_doc.get_doc_before_save.return_value = old_doc

		mock_frappe.get_all.return_value = []

		on_lesson_stages_updated(new_doc, "on_update")

		# _clean_matching_refs should be called with only alive-id
		mock_clean.assert_called_once()
		call_args = mock_clean.call_args[0]
		self.assertEqual(call_args[1], {"alive-id"})


class TestLessonTrash(FrappeTestCase):
	"""Tests for on_lesson_trash — cascade deletion."""

	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_deletes_all_review_items_on_trash(self, mock_frappe, mock_delete):
		mock_frappe.get_all.return_value = ["ri-1", "ri-2", "ri-3"]
		doc = _make_doc([], name="L-TRASH")

		on_lesson_trash(doc, "on_trash")

		mock_frappe.get_all.assert_called_once_with(
			"Memora Review Item",
			filters={"lesson": "L-TRASH"},
			pluck="name",
		)
		mock_delete.assert_called_once_with(["ri-1", "ri-2", "ri-3"])

	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_no_op_when_no_review_items(self, mock_frappe, mock_delete):
		mock_frappe.get_all.return_value = []
		doc = _make_doc([], name="L-EMPTY")

		on_lesson_trash(doc, "on_trash")

		mock_delete.assert_not_called()


class TestEndToEndScenarios(FrappeTestCase):
	"""Complex multi-step scenarios combining multiple operations."""

	# -----------------------------------------------------------------------
	# Scenario: Admin adds QUESTION, saves, then removes QUESTION, saves again
	# The INFORMATION stage remains — Review Item should survive
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup._delete_review_items_and_memory_state")
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_add_then_remove_question_from_group(self, mock_frappe, mock_delete):
		item_id = "e2e-item"

		# Step 1: Lesson has INFO + QUESTION
		stages_v1 = [
			_make_stage("INFORMATION", _config_info(item_id), name="s1"),
			_make_stage("QUESTION", _config_question(item_id, "Q?", [("A", True)]), name="s2"),
		]
		# Step 2: QUESTION removed, only INFO remains
		stages_v2 = [
			_make_stage("INFORMATION", _config_info(item_id), name="s1"),
		]

		old_doc = _make_doc(stages_v1, name="L1")
		new_doc = _make_doc(stages_v2, name="L1")
		new_doc.get_doc_before_save.return_value = old_doc

		mock_frappe.get_all.return_value = [{"name": item_id, "item_id": item_id}]

		on_lesson_stages_updated(new_doc, "on_update")

		# Review Item survives — item_id still on INFORMATION
		mock_delete.assert_not_called()

	# -----------------------------------------------------------------------
	# Scenario: Item group shared by INFORMATION + FILL_BLANK + QUESTION,
	# referenced by 2 MATCHING stages. Delete entire group.
	# Expected: Review Item deleted, both MATCHING stages cleaned
	# -----------------------------------------------------------------------
	def test_full_group_deletion_with_multiple_matching_refs(self):
		item_id = "full-delete"
		other_id = "other-alive"

		m1 = _make_stage("MATCHING", _config_matching([
			{"id": "1", "item_id": item_id, "right": "أ", "left": "ب"},
			{"id": "2", "item_id": other_id, "right": "ج", "left": "د"},
		]), name="m1")
		m2 = _make_stage("MATCHING", _config_matching([
			{"id": "1", "item_id": item_id, "right": "ه", "left": "و"},
		]), name="m2")

		# After deletion: only other-alive remains
		new_stages = [
			_make_stage("INFORMATION", _config_info(other_id), name="s-other"),
			m1, m2,
		]
		doc = _make_doc(new_stages, name="L1")
		current_ids = _extract_stage_item_ids(doc)

		self.assertEqual(current_ids, {other_id})
		self.assertNotIn(item_id, current_ids)

		# Clean matching refs
		_clean_matching_refs(doc, current_ids)

		c1 = json.loads(m1.config_json)
		self.assertEqual(len(c1["pairs"]), 1)
		self.assertEqual(c1["pairs"][0]["item_id"], other_id)
		m1.db_update.assert_called_once()

		c2 = json.loads(m2.config_json)
		self.assertEqual(c2["pairs"], [])
		m2.db_update.assert_called_once()

	# -----------------------------------------------------------------------
	# Scenario: Two item groups share stages, MATCHING references both.
	# Remove one group. MATCHING keeps only the alive pair.
	# -----------------------------------------------------------------------
	def test_partial_matching_cleanup_with_two_groups(self):
		alive_id = "keep-this"
		dead_id = "remove-this"

		matching = _make_stage("MATCHING", _config_matching([
			{"id": "1", "item_id": alive_id, "right": "أ", "left": "ب"},
			{"id": "2", "item_id": dead_id, "right": "ج", "left": "د"},
			{"id": "3", "item_id": alive_id, "right": "ه", "left": "و"},
		]))
		doc = _make_doc([
			_make_stage("INFORMATION", _config_info(alive_id)),
			matching,
		])

		current_ids = _extract_stage_item_ids(doc)
		self.assertEqual(current_ids, {alive_id})

		_clean_matching_refs(doc, current_ids)

		config = json.loads(matching.config_json)
		self.assertEqual(len(config["pairs"]), 2)
		self.assertTrue(all(p["item_id"] == alive_id for p in config["pairs"]))

	# -----------------------------------------------------------------------
	# Scenario: Lesson with only standalone stages (no item groups)
	# Expected: No item_ids extracted, no crashes
	# -----------------------------------------------------------------------
	def test_lesson_with_only_standalone_stages(self):
		doc = _make_doc([
			_make_stage("MATCHING", _config_matching([])),
			_make_stage("MINDMAP", _config_mindmap()),
			_make_stage("STORY", _config_story()),
		])
		ids = _extract_stage_item_ids(doc)
		self.assertEqual(ids, set())

	# -----------------------------------------------------------------------
	# Scenario: Multiple QUESTION stages with same item_id in one group
	# (admin added 2 QUESTION stages to same item group)
	# Expected: Only one Review Item, updated by last QUESTION
	# -----------------------------------------------------------------------
	@patch("memora_admin.events.lesson_cleanup.frappe")
	def test_duplicate_question_stages_same_item_id(self, mock_frappe):
		"""Two QUESTION stages with same item_id — second one wins."""
		ri_mock = MagicMock()
		mock_frappe.new_doc.return_value = ri_mock
		# First call: doesn't exist; second call: now exists
		mock_frappe.db.exists.side_effect = [None, "group-id"]

		q1 = _make_stage("QUESTION", _config_question("group-id", "سؤال أول", [("أ", True)]), name="q1")
		q2 = _make_stage("QUESTION", _config_question("group-id", "سؤال ثاني", [("ب", True)]), name="q2")

		old_doc, new_doc = _make_doc([], name="L1"), _make_doc([q1, q2], name="L1")
		_sync_question_review_items(old_doc, new_doc)

		# First creates, second updates
		mock_frappe.new_doc.assert_called_once()
		mock_frappe.db.set_value.assert_called_once()
