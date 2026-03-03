"""Review Item extraction and sync logic.

Extracts reviewable items from lesson stages and upserts them into the
Memora Review Item DocType. Called from the Memora Lesson on_update hook.

Stage types with `is_skippable=1` in Memora Lesson Stage Settings are
globally excluded. Per-stage `is_skippable` overrides are also respected.
"""

from __future__ import annotations

import hashlib
import json

import frappe

from memora_admin.api.utils import get_player_season_seq as _get_player_season_seq

# ---------------------------------------------------------------------------
# Item extraction (T004)
# ---------------------------------------------------------------------------

# Cache of globally skippable stage types (populated once per request)
_skippable_cache: set[str] | None = None


def _get_globally_skippable_types() -> set[str]:
	"""Return set of stage_type names where is_skippable=1 globally."""
	global _skippable_cache
	if _skippable_cache is None:
		rows = frappe.get_all(
			"Memora Lesson Stage Settings",
			filters={"is_skippable": 1},
			pluck="name",
		)
		_skippable_cache = set(rows)
	return _skippable_cache


def extract_items_from_stage(stage) -> list[dict]:
	"""Extract review items from a single stage's config_json.

	Dispatches by stage_type:
	  QUESTION   → one item per answer (MCQ fields populated)
	  FILL_BLANK → one item per blank (content_json populated)
	  MATCHING   → one item per pair (content_json populated)
	  Other      → one item per item_id found (content_json fallback)

	Returns list of dicts with keys: item_id, stage_type, question_text,
	choice_1..4, correct_choice, content_json.
	"""
	config_str = stage.config_json
	if not config_str:
		return []

	try:
		config = json.loads(config_str)
	except (json.JSONDecodeError, TypeError):
		return []

	stage_type = stage.stage_type
	if stage_type == "QUESTION":
		return _extract_question(config, stage_type)
	elif stage_type == "FILL_BLANK":
		return _extract_fill_blank(config, stage_type)
	elif stage_type == "MATCHING":
		return _extract_matching(config, stage_type)
	elif stage_type == "MINDMAP":
		return _extract_mindmap(config, stage_type)
	else:
		return _extract_generic(config, stage_type)


def _extract_question(config: dict, stage_type: str) -> list[dict]:
	"""Extract ONE item from a QUESTION stage.

	A QUESTION is a single reviewable unit — the student sees one question
	with all choices. We use the correct answer's item_id as the
	representative ID. Falls back to the first answer's item_id if none
	is marked correct.
	"""
	question_text = config.get("question", "")
	answers = config.get("answers", [])
	if not answers:
		return []

	# Build choices list and find correct index + representative item_id
	choices = [a.get("text", "") for a in answers]
	correct_idx = None
	representative_item_id = None

	for i, a in enumerate(answers):
		if a.get("is_correct"):
			correct_idx = i + 1  # 1-based
			representative_item_id = a.get("item_id")
			break

	# Fallback: use first answer's item_id
	if not representative_item_id:
		for a in answers:
			if a.get("item_id"):
				representative_item_id = a["item_id"]
				break

	if not representative_item_id:
		return []

	return [{
		"item_id": representative_item_id,
		"stage_type": stage_type,
		"question_text": question_text,
		"choice_1": choices[0] if len(choices) > 0 else None,
		"choice_2": choices[1] if len(choices) > 1 else None,
		"choice_3": choices[2] if len(choices) > 2 else None,
		"choice_4": choices[3] if len(choices) > 3 else None,
		"correct_choice": correct_idx,
		"content_json": None,
	}]


def _extract_fill_blank(config: dict, stage_type: str) -> list[dict]:
	"""Extract items from a FILL_BLANK stage.

	Each blank has its own item_id. The question_text is the full sentence.
	"""
	text = config.get("text", "")
	blanks = config.get("blanks", [])
	distractors = config.get("distractors", [])
	if not blanks:
		return []

	items = []
	for blank in blanks:
		item_id = blank.get("item_id")
		if not item_id:
			continue

		blank_from = blank.get("from", 0)
		blank_to = blank.get("to", 0)
		correct_word = text[blank_from:blank_to] if text else ""

		items.append({
			"item_id": item_id,
			"stage_type": stage_type,
			"question_text": text,
			"choice_1": None,
			"choice_2": None,
			"choice_3": None,
			"choice_4": None,
			"correct_choice": None,
			"content_json": json.dumps({
				"blank_from": blank_from,
				"blank_to": blank_to,
				"correct_word": correct_word,
				"distractors": distractors,
			}),
		})
	return items


def _extract_matching(config: dict, stage_type: str) -> list[dict]:
	"""Extract items from a MATCHING stage.

	Each pair has its own item_id.
	"""
	instruction = config.get("instruction", "")
	pairs = config.get("pairs", [])
	if not pairs:
		return []

	items = []
	for pair in pairs:
		item_id = pair.get("item_id")
		if not item_id:
			continue
		items.append({
			"item_id": item_id,
			"stage_type": stage_type,
			"question_text": instruction,
			"choice_1": None,
			"choice_2": None,
			"choice_3": None,
			"choice_4": None,
			"correct_choice": None,
			"content_json": json.dumps({
				"left": pair.get("left", ""),
				"right": pair.get("right", ""),
			}),
		})
	return items


def _extract_mindmap(config: dict, stage_type: str) -> list[dict]:
	"""Recursively extract items from MINDMAP children[].

	MINDMAP stages have a tree of nodes, each with an optional item_id.
	Generic fallback only finds top-level children — this traverses the
	full depth.
	"""
	instruction = config.get("instruction") or config.get("central") or ""
	items = []

	def _walk(nodes):
		for node in nodes or []:
			if not isinstance(node, dict):
				continue
			item_id = node.get("item_id")
			if item_id:
				items.append({
					"item_id": item_id,
					"stage_type": stage_type,
					"question_text": instruction,
					"choice_1": None,
					"choice_2": None,
					"choice_3": None,
					"choice_4": None,
					"correct_choice": None,
					"content_json": json.dumps(node),
				})
			_walk(node.get("children"))

	_walk(config.get("children"))
	return items


def _extract_generic(config: dict, stage_type: str) -> list[dict]:
	"""Fallback extraction for unknown non-skippable stage types.

	Searches for item_id at top level and in common list fields.
	"""
	items = []

	# Check for top-level item_id
	if config.get("item_id"):
		items.append({
			"item_id": config["item_id"],
			"stage_type": stage_type,
			"question_text": config.get("text") or config.get("question") or config.get("instruction") or "",
			"choice_1": None,
			"choice_2": None,
			"choice_3": None,
			"choice_4": None,
			"correct_choice": None,
			"content_json": json.dumps(config),
		})
		return items

	# Search common list fields for item_ids
	for key in ("items", "answers", "blanks", "pairs", "elements"):
		entries = config.get(key, [])
		if not isinstance(entries, list):
			continue
		for entry in entries:
			if not isinstance(entry, dict):
				continue
			item_id = entry.get("item_id")
			if item_id:
				items.append({
					"item_id": item_id,
					"stage_type": stage_type,
					"question_text": config.get("text") or config.get("question") or config.get("instruction") or "",
					"choice_1": None,
					"choice_2": None,
					"choice_3": None,
					"choice_4": None,
					"correct_choice": None,
					"content_json": json.dumps(entry),
				})

	return items


# ---------------------------------------------------------------------------
# Content hash for debounce (T010)
# ---------------------------------------------------------------------------


def _compute_lesson_content_hash(stages) -> str:
	"""Deterministic hash of stage configs for debounce.

	Only includes fields that affect Review Item extraction: stage name,
	stage_type, is_skippable, and config_json. Changes to other fields
	(e.g. xp, is_linear) do NOT trigger re-extraction.
	"""
	parts = []
	for stage in sorted(stages or [], key=lambda s: s.name):
		parts.append(f"{stage.name}:{stage.stage_type}:{stage.is_skippable}:{stage.config_json or ''}")
	return hashlib.md5("|".join(parts).encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Sync orchestrator (T005)
# ---------------------------------------------------------------------------


def sync_review_items(lesson_doc) -> dict:
	"""Sync Review Item records from lesson stages.

	Called from the on_update hook. Idempotent — safe to call multiple times.

	1. Check is_reviewable — if disabled, delete existing items and bail
	2. Debounce via content_hash — skip if unchanged
	3. Collect all item_ids from non-skippable stages
	4. Fetch existing Review Items for this lesson
	5. Upsert new/changed items
	6. Delete orphans (in DB but not in current config)
	7. For deleted items, also delete Memory State + Practice Log records

	Returns: {"created": int, "updated": int, "deleted": int}
	"""
	lesson_name = lesson_doc.name

	# --- Gate: is_reviewable check ---
	if not lesson_doc.is_reviewable:
		count = delete_review_items_for_lesson(lesson_name)
		return {"created": 0, "updated": 0, "deleted": count}

	# --- Debounce: content_hash comparison ---
	new_hash = _compute_lesson_content_hash(lesson_doc.stages)
	if lesson_doc.content_hash and lesson_doc.content_hash == new_hash:
		return {"created": 0, "updated": 0, "deleted": 0}

	skippable_types = _get_globally_skippable_types()

	# --- Step 1: Collect items from all non-skippable stages ---
	current_items = {}  # item_id -> item dict
	for stage in lesson_doc.stages or []:
		# Skip globally skippable stage types
		if stage.stage_type in skippable_types:
			continue
		# Skip per-stage override
		if stage.is_skippable:
			continue

		extracted = extract_items_from_stage(stage)
		for item in extracted:
			item["stage_id"] = stage.name  # child table row name
			current_items[item["item_id"]] = item

	# --- Step 2: Fetch existing Review Items for this lesson ---
	existing = frappe.get_all(
		"Memora Review Item",
		filters={"lesson": lesson_name},
		fields=[
			"name", "item_id", "stage_id", "stage_type",
			"question_text", "choice_1", "choice_2", "choice_3", "choice_4",
			"correct_choice", "content_json",
		],
	)
	existing_by_id = {r.item_id: r for r in existing}

	created = 0
	updated = 0
	deleted = 0

	# --- Step 3: Upsert new/changed items ---
	for item_id, item_data in current_items.items():
		if item_id in existing_by_id:
			# Check if anything changed
			ex = existing_by_id[item_id]
			changed = (
				ex.stage_id != item_data["stage_id"]
				or ex.stage_type != item_data["stage_type"]
				or (ex.question_text or "") != (item_data["question_text"] or "")
				or (ex.choice_1 or "") != (item_data["choice_1"] or "")
				or (ex.choice_2 or "") != (item_data["choice_2"] or "")
				or (ex.choice_3 or "") != (item_data["choice_3"] or "")
				or (ex.choice_4 or "") != (item_data["choice_4"] or "")
				or (ex.correct_choice or 0) != (item_data["correct_choice"] or 0)
				or (ex.content_json or "") != (item_data["content_json"] or "")
			)
			if changed:
				frappe.db.set_value("Memora Review Item", item_id, {
					"stage_id": item_data["stage_id"],
					"stage_type": item_data["stage_type"],
					"question_text": item_data["question_text"],
					"choice_1": item_data["choice_1"],
					"choice_2": item_data["choice_2"],
					"choice_3": item_data["choice_3"],
					"choice_4": item_data["choice_4"],
					"correct_choice": item_data["correct_choice"],
					"content_json": item_data["content_json"],
				}, update_modified=True)
				updated += 1
		else:
			# Create new item
			doc = frappe.new_doc("Memora Review Item")
			doc.item_id = item_id
			doc.subject = lesson_doc.subject
			doc.track = lesson_doc.track
			doc.unit = lesson_doc.unit
			doc.topic = lesson_doc.topic
			doc.lesson = lesson_name
			doc.stage_id = item_data["stage_id"]
			doc.stage_type = item_data["stage_type"]
			doc.question_text = item_data["question_text"]
			doc.choice_1 = item_data["choice_1"]
			doc.choice_2 = item_data["choice_2"]
			doc.choice_3 = item_data["choice_3"]
			doc.choice_4 = item_data["choice_4"]
			doc.correct_choice = item_data["correct_choice"]
			doc.content_json = item_data["content_json"]
			doc.flags.ignore_permissions = True
			doc.insert()
			created += 1

	# --- Step 4: Delete orphans ---
	orphan_ids = set(existing_by_id.keys()) - set(current_items.keys())
	if orphan_ids:
		_delete_review_items_and_memory_state(list(orphan_ids))
		deleted = len(orphan_ids)

	# --- Step 5: Update content_hash for debounce ---
	frappe.db.set_value("Memora Lesson", lesson_name, "content_hash", new_hash, update_modified=False)

	return {"created": created, "updated": updated, "deleted": deleted}


def delete_review_items_for_lesson(lesson_name: str) -> int:
	"""Delete all Review Items for a lesson and clean up Memory State.

	Called from the on_trash hook.

	Returns: number of Review Items deleted.
	"""
	item_ids = frappe.get_all(
		"Memora Review Item",
		filters={"lesson": lesson_name},
		pluck="name",
	)
	if not item_ids:
		return 0

	_delete_review_items_and_memory_state(item_ids)
	return len(item_ids)


def _delete_review_items_and_memory_state(item_ids: list[str]):
	"""Delete Review Item records and their associated Memory State rows.

	Memory State uses BINARY(16) item_id and is RANGE-partitioned by season_seq.
	We must include season_seq for partition pruning.
	"""
	if not item_ids:
		return

	# Delete Practice Log entries (raw SQL table, idx_item_id index)
	placeholders = ", ".join(["%s"] * len(item_ids))
	frappe.db.sql(
		f"DELETE FROM `tabMemora Practice Log` WHERE item_id IN ({placeholders})",
		tuple(item_ids),
	)

	# Delete Memory State records (raw SQL — partitioned table)
	# Get all season_seqs to ensure we prune across partitions
	season_rows = frappe.db.sql(
		"SELECT DISTINCT season_seq FROM `tabMemora Season`",
		as_dict=True,
	)

	for season_row in season_rows:
		season_seq = season_row.get("season_seq")
		if not season_seq:
			continue
		# Build parameterized IN clause for item_ids
		params = {"season_seq": season_seq}
		in_parts = []
		for i, iid in enumerate(item_ids):
			key = f"id_{i}"
			params[key] = iid
			in_parts.append(f"UUID_TO_BIN(%({key})s)")

		frappe.db.sql(
			f"""
			DELETE FROM `tabMemora Memory State`
			WHERE item_id IN ({", ".join(in_parts)})
			  AND season_seq = %(season_seq)s
			""",
			params,
		)

	# Delete Review Item records
	for item_id in item_ids:
		frappe.delete_doc("Memora Review Item", item_id, force=True, ignore_permissions=True)


def resync_all_review_items():
	"""One-time cleanup: re-sync all reviewable lessons to remove duplicates.

	Resets content_hash to force re-extraction with the fixed logic
	(one item per QUESTION stage instead of one per answer choice).
	The sync's orphan deletion cleans up Practice Log and Memory State.

	Run via: bench --site <site> execute memora_admin.api.review_items.resync_all_review_items
	"""
	global _skippable_cache
	_skippable_cache = None  # Reset cache

	# Reset content_hash on all reviewable lessons to bypass debounce
	frappe.db.sql("UPDATE `tabMemora Lesson` SET content_hash = NULL WHERE is_reviewable = 1")
	frappe.db.commit()

	lessons = frappe.get_all("Memora Lesson", filters={"is_reviewable": 1}, pluck="name")
	total_created = 0
	total_deleted = 0

	for lesson_name in lessons:
		doc = frappe.get_doc("Memora Lesson", lesson_name)
		result = sync_review_items(doc)
		total_created += result["created"]
		total_deleted += result["deleted"]
		if result["deleted"]:
			print(f"  {lesson_name}: deleted={result['deleted']}")

	frappe.db.commit()
	print(f"\nResync complete: {len(lessons)} lessons processed, "
		  f"{total_created} created, {total_deleted} duplicates removed")
