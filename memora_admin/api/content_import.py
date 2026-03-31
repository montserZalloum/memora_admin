"""Content Import API for bulk-importing AI-generated lesson content.

Provides three endpoints for a 4-step import wizard on the Memora Topic form:
  1. validate_import_json — parse + validate JSON structure
  2. preview_import — dry-run summary (no DB writes)
  3. execute_import — create lessons, stages, and review items
"""

from __future__ import annotations

import json
import re
import uuid as _uuid

import frappe

# =============================================================================
# JSON Validation
# =============================================================================


@frappe.whitelist()
def validate_import_json(topic_name: str, json_data: str) -> dict:
	"""Parse and validate import JSON, returning merged lesson data.

	The JSON is a top-level array of two objects:
	  - Questions object: sub_lessons[].questions (MCQ items)
	  - Stages object: sub_lessons[].stages (lesson stages with config)

	Detection: check for 'questions' vs 'stages' key in sub_lessons[0].

	Returns:
	  {
	    "success": bool,
	    "lessons": [{title, questions: [...], stages: [...]}],
	    "errors": [str],
	    "warnings": [str],
	  }
	"""
	errors: list[str] = []
	warnings: list[str] = []

	# Parse JSON
	try:
		data = json.loads(json_data) if isinstance(json_data, str) else json_data
	except (json.JSONDecodeError, TypeError) as e:
		return {"success": False, "lessons": [], "errors": [f"Invalid JSON: {e}"], "warnings": []}

	if not isinstance(data, list) or len(data) < 2:
		return {
			"success": False,
			"lessons": [],
			"errors": ["JSON must be an array of two objects (questions + stages)"],
			"warnings": [],
		}

	# Detect which object is questions vs stages
	questions_obj = None
	stages_obj = None
	for obj in data:
		if not isinstance(obj, dict) or "sub_lessons" not in obj:
			continue
		subs = obj["sub_lessons"]
		if not isinstance(subs, list) or not subs:
			continue
		first = subs[0]
		if "questions" in first:
			questions_obj = obj
		elif "stages" in first:
			stages_obj = obj

	if not questions_obj:
		errors.append("Could not find questions object (sub_lessons[].questions)")
	if not stages_obj:
		errors.append("Could not find stages object (sub_lessons[].stages)")
	if errors:
		return {"success": False, "lessons": [], "errors": errors, "warnings": []}

	# Load valid stage types from DB (once)
	valid_stage_types = set(frappe.get_all("Memora Lesson Stage Settings", pluck="name"))

	# Build lookup by title for stages
	stages_by_title: dict[str, list] = {}
	for sub in stages_obj["sub_lessons"]:
		title = sub.get("title", "").strip()
		if title:
			stages_by_title[title] = sub.get("stages", [])

	# Collect all question IDs for cross-reference validation
	all_question_ids: set[int] = set()

	# Merge sub_lessons by title
	lessons: list[dict] = []
	for sub in questions_obj["sub_lessons"]:
		title = sub.get("title", "").strip()
		if not title:
			errors.append("Found sub_lesson with empty title in questions object")
			continue

		questions = sub.get("questions", [])
		stages = stages_by_title.pop(title, None)

		if stages is None:
			warnings.append(f"Lesson '{title}': no matching stages found")
			stages = []

		# Validate questions
		for i, q in enumerate(questions):
			q_label = f"Lesson '{title}', question {i + 1}"
			if "id" not in q:
				errors.append(f"{q_label}: missing 'id'")
			else:
				all_question_ids.add(q["id"])
			if "question" not in q:
				errors.append(f"{q_label}: missing 'question'")
			opts = q.get("options", [])
			if not isinstance(opts, list) or len(opts) < 2:
				errors.append(f"{q_label}: needs at least 2 options")
			if "correct_answer" not in q:
				errors.append(f"{q_label}: missing 'correct_answer'")
			elif not isinstance(q["correct_answer"], int) or q["correct_answer"] < 0:
				errors.append(f"{q_label}: 'correct_answer' must be a non-negative integer")
			elif isinstance(opts, list) and q["correct_answer"] >= len(opts):
				errors.append(f"{q_label}: 'correct_answer' index out of range")

		# Validate stages
		for i, s in enumerate(stages):
			s_label = f"Lesson '{title}', stage {i + 1}"
			st = s.get("stage_type", "")
			if st and st not in valid_stage_types:
				errors.append(f"{s_label}: unknown stage_type '{st}'")

			# Parse [[...]] highlight markers for INFORMATION stages
			if st == "INFORMATION":
				config = s.get("config", {})
				if isinstance(config, str):
					try:
						config = json.loads(config)
					except (json.JSONDecodeError, TypeError):
						config = {}
				if isinstance(config, dict) and "[[" in config.get("text", ""):
					s["config"] = _parse_information_highlights(config)

			# Parse [[word|explanation]] markers for REVEAL stages
			elif st == "REVEAL":
				config = s.get("config", {})
				if isinstance(config, str):
					try:
						config = json.loads(config)
					except (json.JSONDecodeError, TypeError):
						config = {}
				if isinstance(config, dict) and "[[" in config.get("sentence", ""):
					s["config"] = _parse_reveal_config(config)

			# Parse [[answer]] or [[blank]] markers for FILL_BLANK stages
			elif st == "FILL_BLANK":
				config = s.get("config", {})
				if isinstance(config, str):
					try:
						config = json.loads(config)
					except (json.JSONDecodeError, TypeError):
						config = {}
				if isinstance(config, dict) and "[[" in config.get("text", ""):
					s["config"] = _parse_fill_blank_config(config, questions)

		# Auto-generate QUESTION stages interleaved after each question's stage group
		covered_ids: set[str] = set()
		for s in stages:
			if s.get("stage_type") == "QUESTION":
				cfg = s.get("config", {})
				if isinstance(cfg, str):
					try:
						cfg = json.loads(cfg)
					except (json.JSONDecodeError, TypeError):
						cfg = {}
				covered_ids.update(_collect_item_ids(cfg))

		# Find the last non-MATCHING stage index for each question's item_id
		q_last_stage: dict[str, int] = {}
		for idx, s in enumerate(stages):
			if s.get("stage_type") == "MATCHING":
				continue
			cfg = s.get("config", {})
			if isinstance(cfg, str):
				try:
					cfg = json.loads(cfg)
				except (json.JSONDecodeError, TypeError):
					cfg = {}
			for iid in _collect_item_ids(cfg):
				q_last_stage[iid] = idx

		# Build map: stage_index → QUESTION stages to insert after it
		insert_after: dict[int, list] = {}
		append_tail: list[dict] = []
		for q in questions:
			q_id = q.get("id")
			if q_id is None or str(q_id) in covered_ids:
				continue
			q_stage = {
				"stage_type": "QUESTION",
				"stage_title": "",
				"config": _parse_question_config(q),
			}
			pos = q_last_stage.get(str(q_id))
			if pos is not None:
				insert_after.setdefault(pos, []).append(q_stage)
			else:
				append_tail.append(q_stage)

		# Rebuild stages list with QUESTION stages interleaved
		new_stages: list[dict] = []
		for idx, s in enumerate(stages):
			new_stages.append(s)
			if idx in insert_after:
				new_stages.extend(insert_after[idx])
		new_stages.extend(append_tail)
		stages = new_stages

		lessons.append({"title": title, "questions": questions, "stages": stages})

	# Warn about unmatched stage sub_lessons
	for unmatched_title in stages_by_title:
		warnings.append(f"Stages for '{unmatched_title}' have no matching questions sub_lesson")

	# Cross-reference: check item_id references in stage configs
	for lesson in lessons:
		for i, stage in enumerate(lesson["stages"]):
			config = stage.get("config", {})
			if isinstance(config, str):
				try:
					config = json.loads(config)
				except (json.JSONDecodeError, TypeError):
					pass
			_check_item_id_refs(config, all_question_ids, lesson["title"], i + 1, warnings)

	return {
		"success": len(errors) == 0,
		"lessons": lessons,
		"errors": errors,
		"warnings": warnings,
	}


def _parse_information_highlights(config: dict) -> dict:
	"""Parse [[...]] markers in information stage text into highlights with from/to positions.

	Example: "read [[this word]] carefully" →
	  text: "read this word carefully"
	  highlights: [{"from": 5, "to": 14, "item_id": null}]
	"""
	text = config.get("text", "")
	if "[[" not in text:
		return config

	new_highlights: list[dict] = []
	clean_parts: list[str] = []
	pos = 0
	last_end = 0

	for match in re.finditer(r"\[\[(.*?)\]\]", text, re.DOTALL):
		before = text[last_end : match.start()]
		clean_parts.append(before)
		pos += len(before)

		inner = match.group(1).strip()
		from_pos = pos
		clean_parts.append(inner)
		pos += len(inner)
		new_highlights.append({"from": from_pos, "to": pos, "item_id": None})
		last_end = match.end()

	clean_parts.append(text[last_end:])

	result = dict(config)
	result["text"] = "".join(clean_parts)
	# Preserve any highlights already defined in the config (edge case), append new ones
	result["highlights"] = list(config.get("highlights") or []) + new_highlights
	return result


def _parse_reveal_config(config: dict) -> dict:
	"""Parse ``[[word]]`` or ``[[word|explanation]]`` markers in REVEAL stage sentence.

	Supports two formats:
	  1. ``[[word|explanation]]`` — explanation is inline in the marker.
	  2. ``[[word]]`` — explanation comes from the i-th existing highlight's
	     ``description`` (or ``explanation``) field.

	Each marker is stripped, the word kept in place, and a highlight entry
	``{word, explanation, from, to, item_id}`` is produced.

	Example (format 2, typical from AI-generated JSON):
	  sentence:   "من [[الجبس]]، وتعود ..."
	  highlights: [{"description": "...", "item_id": "..."}]
	  →
	  sentence:   "من الجبس، وتعود ..."
	  highlights: [{"word": "الجبس", "explanation": "...", "from": 3, "to": 8, "item_id": "..."}]
	"""
	sentence = config.get("sentence", "")
	if "[[" not in sentence:
		return config

	existing_highlights = config.get("highlights") or []

	new_highlights: list[dict] = []
	clean_parts: list[str] = []
	pos = 0
	last_end = 0
	marker_index = 0

	# Match both [[word|explanation]] and [[word]]
	for match in re.finditer(r"\[\[([^\]]+)\]\]", sentence, re.DOTALL):
		before = sentence[last_end : match.start()]
		clean_parts.append(before)
		pos += len(before)

		inner = match.group(1).strip()

		# Split on pipe if present: [[word|explanation]]
		if "|" in inner:
			word, explanation = inner.split("|", 1)
			word = word.strip()
			explanation = explanation.strip()
		else:
			# [[word]] — pull explanation from the positional existing highlight
			word = inner
			explanation = ""

		from_pos = pos
		clean_parts.append(word)
		pos += len(word)

		# Merge with existing highlight at same position (carries item_id, description)
		existing = existing_highlights[marker_index] if marker_index < len(existing_highlights) else {}
		if not explanation:
			explanation = existing.get("description", "") or existing.get("explanation", "")
		item_id = existing.get("item_id")

		new_highlights.append(
			{
				"word": word,
				"explanation": explanation,
				"from": from_pos,
				"to": pos,
				"item_id": item_id,
			}
		)
		last_end = match.end()
		marker_index += 1

	clean_parts.append(sentence[last_end:])

	result = dict(config)
	result["sentence"] = "".join(clean_parts)
	result["highlights"] = new_highlights
	return result


def _parse_fill_blank_config(config: dict, questions: list[dict]) -> dict:
	"""Parse ``[[answer]]`` or ``[[blank]]`` markers in fill_blank stage text.

	Supports two marker formats:
	  1. ``[[answer_text]]`` — answer is inline (e.g. ``[[الحجر]]``).
	  2. ``[[blank]]`` — answer looked up from the positional question's correct option.

	The i-th marker maps positionally to blanks[i].item_id (if set),
	otherwise to questions[i].id.  Distractors are collected from the
	matched question's wrong options.

	Example (format 1):
	  text:   "صنع أدواته من [[الحجر]]."
	  blanks: [{"item_id": "1"}]
	  →
	  text:   "صنع أدواته من الحجر."
	  blanks: [{"from": 15, "to": 20, "item_id": 1}]

	Example (format 2, questions[0]: options=["يجري","يطير","يمشي"], correct_answer=0):
	  text:   "النهر [[blank]] بسرعة"
	  →
	  text:   "النهر يجري بسرعة"
	  blanks: [{"from": 7, "to": 12, "item_id": <q.id>}]
	  distractors: ["يطير", "يمشي"]
	"""
	text = config.get("text", "")
	if "[[" not in text:
		return config

	existing_blanks = config.get("blanks") or []
	q_by_id = {str(q["id"]): q for q in questions}

	new_blanks: list[dict] = []
	distractors: list[str] = list(config.get("distractors") or [])
	distractor_set: set[str] = set(distractors)

	clean_parts: list[str] = []
	pos = 0
	last_end = 0
	blank_index = 0

	for match in re.finditer(r"\[\[([^\]]+)\]\]", text):
		before = text[last_end : match.start()]
		clean_parts.append(before)
		pos += len(before)

		inner = match.group(1).strip()
		answer_text = ""
		q = None

		# Prefer explicit item_id from blanks array, fall back to positional
		if blank_index < len(existing_blanks):
			explicit_id = existing_blanks[blank_index].get("item_id")
			if explicit_id is not None:
				q = q_by_id.get(str(explicit_id))
		if q is None and blank_index < len(questions):
			q = questions[blank_index]

		item_id = None
		if q:
			item_id = q["id"]

		# [[blank]] → look up answer from question; [[answer_text]] → use inline
		if inner.lower() == "blank":
			if q:
				opts = q.get("options", [])
				correct_idx = q.get("correct_answer", 0)
				answer_text = opts[correct_idx] if correct_idx < len(opts) else ""
		else:
			answer_text = inner

		# Collect distractors from the matched question's wrong options
		if q:
			opts = q.get("options", [])
			correct_idx = q.get("correct_answer", 0)
			for i, opt in enumerate(opts):
				if i != correct_idx and opt and opt not in distractor_set:
					distractors.append(opt)
					distractor_set.add(opt)

		from_pos = pos
		clean_parts.append(answer_text)
		pos += len(answer_text)
		new_blanks.append({"from": from_pos, "to": pos, "item_id": item_id})
		last_end = match.end()
		blank_index += 1

	clean_parts.append(text[last_end:])

	result = dict(config)
	result["text"] = "".join(clean_parts)
	result["blanks"] = new_blanks
	result["distractors"] = distractors
	return result


def _collect_item_ids(obj: object) -> set[str]:
	"""Recursively collect all non-None ``item_id`` values as strings."""
	ids: set[str] = set()
	if isinstance(obj, dict):
		for key, val in obj.items():
			if key == "item_id" and val is not None:
				ids.add(str(val))
			else:
				ids.update(_collect_item_ids(val))
	elif isinstance(obj, list):
		for item in obj:
			ids.update(_collect_item_ids(item))
	return ids


def _parse_question_config(q: dict) -> dict:
	"""Convert import-format question to QUESTION stage ``config`` dict.

	Output matches game_lesson.js format::

	  {"question": "...", "answers": [{"text": "...", "is_correct": bool, "item_id": <id>}, ...]}

	``item_id`` is set only on the correct answer (raw integer from import JSON;
	rewritten to UUID during ``execute_import``).
	"""
	options = q.get("options", [])
	correct_idx = q.get("correct_answer", 0)

	answers = []
	for i, opt in enumerate(options):
		answer: dict = {"text": opt, "is_correct": i == correct_idx}
		if i == correct_idx:
			answer["item_id"] = q["id"]
		answers.append(answer)

	return {"question": q.get("question", ""), "answers": answers}


def _check_item_id_refs(
	obj: object,
	valid_ids: set[int],
	lesson_title: str,
	stage_num: int,
	warnings: list[str],
) -> None:
	"""Recursively check for item_id references in stage configs."""
	if isinstance(obj, dict):
		for key, val in obj.items():
			if key == "item_id" and isinstance(val, int) and val not in valid_ids:
				warnings.append(
					f"Lesson '{lesson_title}', stage {stage_num}: " f"item_id {val} not found in questions"
				)
			else:
				_check_item_id_refs(val, valid_ids, lesson_title, stage_num, warnings)
	elif isinstance(obj, list):
		for item in obj:
			_check_item_id_refs(item, valid_ids, lesson_title, stage_num, warnings)


# =============================================================================
# Preview (Dry Run)
# =============================================================================


@frappe.whitelist()
def preview_import(topic_name: str, lessons_json: str) -> dict:
	"""Return a dry-run summary of what the import would create.

	No DB writes.

	Returns:
	  {
	    "topic": str,
	    "lesson_count": int,
	    "lessons": [{title, stage_count, question_count}],
	    "total_stages": int,
	    "total_questions": int,
	  }
	"""
	lessons = json.loads(lessons_json) if isinstance(lessons_json, str) else lessons_json

	lesson_summaries = []
	total_stages = 0
	total_questions = 0
	for lesson in lessons:
		sc = len(lesson.get("stages", []))
		qc = len(lesson.get("questions", []))
		total_stages += sc
		total_questions += qc
		lesson_summaries.append(
			{
				"title": lesson.get("title", ""),
				"stage_count": sc,
				"question_count": qc,
			}
		)

	return {
		"topic": topic_name,
		"lesson_count": len(lessons),
		"lessons": lesson_summaries,
		"total_stages": total_stages,
		"total_questions": total_questions,
	}


# =============================================================================
# Execute Import
# =============================================================================


@frappe.whitelist()
def execute_import(
	topic_name: str,
	lessons_json: str,
	id_to_uuid_json: str,
	mode: str = "add",
) -> dict:
	"""Create lessons, stages, and review items from validated import data.

	Args:
	  topic_name: Memora Topic document name
	  lessons_json: JSON array of lesson objects
	  id_to_uuid_json: JSON dict mapping integer question IDs to UUIDs
	  mode: "add" or "replace"

	Returns:
	  {
	    "lessons_created": int,
	    "stages_created": int,
	    "review_items_created": int,
	    "lesson_names": [str],
	  }
	"""
	# Permission check
	if not (
		frappe.session.user == "Administrator"
		or "System Manager" in frappe.get_roles()
		or "Memora Admin" in frappe.get_roles()
	):
		frappe.throw("You need System Manager or Memora Admin role to import content", frappe.PermissionError)

	lessons = json.loads(lessons_json) if isinstance(lessons_json, str) else lessons_json
	id_to_uuid = json.loads(id_to_uuid_json) if isinstance(id_to_uuid_json, str) else id_to_uuid_json
	# Ensure keys are strings for lookup
	id_to_uuid = {str(k): v for k, v in id_to_uuid.items()}

	# Load topic hierarchy
	topic_doc = frappe.get_doc("Memora Topic", topic_name)
	hierarchy = {
		"subject": topic_doc.subject,
		"track": topic_doc.track,
		"unit": topic_doc.unit,
		"topic": topic_name,
	}

	# Flush any pending interactions to MariaDB before deleting lessons,
	# so buffered interactions are written with valid lesson references.
	if mode == "replace":
		try:
			from memora_admin.tasks.sync import flush_interaction_buffer

			flush_interaction_buffer()
		except Exception:
			frappe.log_error(title="Content import: interaction buffer flush failed")

	# Use a savepoint so the entire import is all-or-nothing.
	# If any lesson/stage/review-item insert fails, we roll back
	# everything and the admin can re-upload the corrected JSON.
	frappe.db.savepoint("content_import")

	try:
		# Replace mode: delete existing lessons for this topic
		if mode == "replace":
			existing_lessons = frappe.get_all(
				"Memora Lesson",
				filters={"topic": topic_name},
				pluck="name",
			)
			for lesson_name in existing_lessons:
				# frappe.delete_doc fires on_trash hooks:
				#   - build_trigger.on_content_updated (cache invalidation)
				#   - dimension_sync.on_lesson_changed (analytics dimension refresh)
				#   - lesson_cleanup.on_lesson_trash (Review Item + Memory State + Practice Log cleanup)
				frappe.delete_doc("Memora Lesson", lesson_name, force=True, ignore_permissions=True)

		lessons_created = 0
		stages_created = 0
		review_items_created = 0
		lesson_names: list[str] = []
		used_uuids: set[str] = set()

		for lesson_data in lessons:
			# Build per-lesson UUID map — question IDs (1, 2, 3...) repeat across
			# lessons, so each lesson needs its own ID→UUID mapping.
			lesson_uuid_map: dict[str, str] = {}
			for q in lesson_data.get("questions", []):
				q_id = str(q["id"])
				client_uuid = id_to_uuid.get(q_id)
				if client_uuid and client_uuid not in used_uuids:
					lesson_uuid_map[q_id] = client_uuid
					used_uuids.add(client_uuid)
				else:
					new_uuid = str(_uuid.uuid4())
					lesson_uuid_map[q_id] = new_uuid
					used_uuids.add(new_uuid)

			# Create lesson doc
			lesson_doc = frappe.new_doc("Memora Lesson")
			lesson_doc.lesson_title = lesson_data["title"]
			lesson_doc.topic = topic_name
			lesson_doc.unit = hierarchy["unit"]
			lesson_doc.track = hierarchy["track"]
			lesson_doc.subject = hierarchy["subject"]
			lesson_doc.is_published = 1
			lesson_doc.is_reviewable = 1

			# Append stages with item_id rewriting (use per-lesson map)
			for stage_data in lesson_data.get("stages", []):
				config = stage_data.get("config", {})
				if isinstance(config, str):
					try:
						config = json.loads(config)
					except (json.JSONDecodeError, TypeError):
						pass

				# Rewrite integer item_id references to UUIDs
				config = _rewrite_item_ids(config, lesson_uuid_map)

				lesson_doc.append(
					"stages",
					{
						"stage_title": stage_data.get("stage_title", stage_data.get("title", "")),
						"stage_type": stage_data.get("stage_type", ""),
						"is_skippable": stage_data.get("is_skippable", 0),
						"config_json": json.dumps(config, ensure_ascii=False)
						if isinstance(config, (dict, list))
						else str(config),
					},
				)
				stages_created += 1

			# Insert fires before_insert (bit_index) + on_update (build trigger)
			lesson_doc.insert(ignore_permissions=True)
			lesson_names.append(lesson_doc.name)
			lessons_created += 1

			# Create Review Items for each question
			for q in lesson_data.get("questions", []):
				q_id = str(q["id"])
				uuid_val = lesson_uuid_map.get(q_id)
				if not uuid_val:
					continue

				options = q.get("options", [])
				correct_idx = q.get("correct_answer", 0)

				ri = frappe.new_doc("Memora Review Item")
				ri.item_id = uuid_val
				ri.subject = hierarchy["subject"]
				ri.track = hierarchy["track"]
				ri.unit = hierarchy["unit"]
				ri.topic = hierarchy["topic"]
				ri.lesson = lesson_doc.name
				ri.question_text = q.get("question", "")
				# Map options to choice_1..4 (pad with empty if fewer than 4)
				for i in range(4):
					setattr(ri, f"choice_{i + 1}", options[i] if i < len(options) else "")
				# 0-based index → 1-based choice number
				ri.correct_choice = correct_idx + 1 if isinstance(correct_idx, int) else 1
				# Insert fires after_insert hooks (practice_content + build_trigger, both debounced)
				ri.insert(ignore_permissions=True)
				review_items_created += 1

		frappe.db.commit()

	except Exception:
		frappe.db.rollback(save_point="content_import")
		frappe.log_error(title="Content import failed")
		frappe.throw(
			"Import failed — no changes were saved. Check the error log for details and re-upload the corrected JSON.",
			title="Import Error",
		)

	# Post-commit cache invalidation: the on_update hooks during insert() fire
	# BEFORE commit, so a concurrent hierarchy fetch can cache stale data (the
	# separate Frappe API transaction won't see uncommitted rows).  Re-invalidate
	# now that the rows are committed and visible to all transactions.
	from memora_admin.events.build_trigger import _invalidate_hierarchy_cache

	_invalidate_hierarchy_cache(hierarchy["subject"])

	return {
		"lessons_created": lessons_created,
		"stages_created": stages_created,
		"review_items_created": review_items_created,
		"lesson_names": lesson_names,
	}


def _rewrite_item_ids(obj: object, id_to_uuid: dict[str, str]) -> object:
	"""Recursively replace integer item_id values with UUIDs in stage configs.

	Only rewrites values whose dict key is literally ``item_id``.  Other integer
	fields (``from``, ``to``, ``correct_answer``, …) are left untouched.
	"""
	if isinstance(obj, dict):
		return {
			k: (id_to_uuid.get(str(v), v) if k == "item_id" else _rewrite_item_ids(v, id_to_uuid))
			for k, v in obj.items()
		}
	if isinstance(obj, list):
		return [_rewrite_item_ids(item, id_to_uuid) for item in obj]
	return obj
