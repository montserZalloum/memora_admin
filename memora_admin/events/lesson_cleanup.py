"""Cleanup handlers for Memora Lesson on_trash and on_update.

When a lesson is deleted, its Review Items and their associated
Memory State + Practice Log records must be cleaned up.

When stages are removed from a lesson, orphaned Review Items
(whose item_id no longer appears in any stage) are also cleaned up.

Registered in hooks.py as doc_event on Memora Lesson.
"""

from __future__ import annotations

import json

import frappe


def on_lesson_trash(doc, method):
	"""Delete Review Items (cascade to Memory State + Practice Log) when lesson is trashed."""
	item_ids = frappe.get_all(
		"Memora Review Item",
		filters={"lesson": doc.name},
		pluck="name",
	)
	if not item_ids:
		return

	_delete_review_items_and_memory_state(item_ids)


def on_lesson_stages_updated(doc, method):
	"""Remove orphaned Review Items when stages are removed from a lesson.

	Also syncs Review Item content when QUESTION stages are updated.
	"""
	# Skip if stages haven't changed — avoids expensive config parsing on every save
	old_doc = doc.get_doc_before_save()
	if not old_doc:
		return  # New doc — no pre-existing Review Items to orphan

	old_stage_data = [(s.name, s.config_json) for s in old_doc.stages or []]
	new_stage_data = [(s.name, s.config_json) for s in doc.stages or []]
	if old_stage_data == new_stage_data:
		return

	# --- Orphan cleanup ---
	current_item_ids = _extract_stage_item_ids(doc)

	review_items = frappe.get_all(
		"Memora Review Item",
		filters={"lesson": doc.name},
		fields=["name", "item_id"],
	)
	if review_items:
		orphaned = [ri["name"] for ri in review_items if ri["item_id"] not in current_item_ids]
		if orphaned:
			_delete_review_items_and_memory_state(orphaned)

	# --- Clean stale references from MATCHING stages ---
	_clean_matching_refs(doc, current_item_ids)

	# --- Sync QUESTION stage content to Review Items ---
	_sync_question_review_items(old_doc, doc)


def _clean_matching_refs(doc, current_item_ids: set[str]):
	"""Remove pairs from MATCHING stages whose item_id no longer exists."""
	for stage in doc.stages or []:
		if stage.stage_type != "MATCHING":
			continue
		try:
			config = json.loads(stage.config_json) if isinstance(stage.config_json, str) else (stage.config_json or {})
		except (json.JSONDecodeError, TypeError):
			continue

		pairs = config.get("pairs")
		if not isinstance(pairs, list):
			continue

		cleaned = [p for p in pairs if isinstance(p, dict) and p.get("item_id") in current_item_ids]
		if len(cleaned) == len(pairs):
			continue

		config["pairs"] = cleaned
		stage.config_json = json.dumps(config, ensure_ascii=False)
		stage.db_update()


_STANDALONE_STAGE_TYPES = frozenset(("MATCHING", "MINDMAP", "INTERACTIVE_MINDMAP", "STORY"))


def _extract_stage_item_ids(doc) -> set[str]:
	"""Extract item_ids from item-group stages only.

	Standalone stage types (MATCHING, MINDMAP, INTERACTIVE_MINDMAP, STORY) are
	skipped — their item_id references are cross-references, not content
	ownership.  A Review Item should only survive if an actual content stage
	still carries its item_id.
	"""
	item_ids = set()
	for stage in doc.stages or []:
		if stage.stage_type in _STANDALONE_STAGE_TYPES:
			continue
		try:
			config = json.loads(stage.config_json) if isinstance(stage.config_json, str) else (stage.config_json or {})
		except (json.JSONDecodeError, TypeError):
			continue
		# Top-level item_id (the group identifier)
		if config.get("item_id"):
			item_ids.add(config["item_id"])
	return item_ids


def _sync_question_review_items(old_doc, doc):
	"""Update Review Items when QUESTION stage content changes."""
	# Build map of old QUESTION stages by row name
	old_question_configs = {}
	for stage in old_doc.stages or []:
		if stage.stage_type == "QUESTION":
			old_question_configs[stage.name] = stage.config_json

	for stage in doc.stages or []:
		if stage.stage_type != "QUESTION":
			continue

		# Skip if config unchanged for this row
		if old_question_configs.get(stage.name) == stage.config_json:
			continue

		try:
			config = json.loads(stage.config_json) if isinstance(stage.config_json, str) else (stage.config_json or {})
		except (json.JSONDecodeError, TypeError):
			continue

		# Use the group's top-level item_id — this is shared across all stages
		# in the item group, so the Review Item survives even if the QUESTION
		# stage is removed (as long as any stage with this item_id remains).
		item_id = config.get("item_id")
		if not item_id:
			continue

		question_text = config.get("question", "")
		answers = config.get("answers", [])
		if not answers:
			continue

		choices = []
		correct_choice = 0
		for i, answer in enumerate(answers):
			if not isinstance(answer, dict):
				continue
			choices.append(answer.get("text", ""))
			if answer.get("is_correct"):
				correct_choice = i + 1  # 1-based

		# Pad to 4 choices
		while len(choices) < 4:
			choices.append("")

		review_name = frappe.db.exists("Memora Review Item", {"item_id": item_id, "lesson": doc.name})
		if review_name:
			frappe.db.set_value(
				"Memora Review Item",
				review_name,
				{
					"question_text": question_text,
					"choice_1": choices[0],
					"choice_2": choices[1],
					"choice_3": choices[2],
					"choice_4": choices[3],
					"correct_choice": correct_choice,
				},
				update_modified=True,
			)
		else:
			ri = frappe.new_doc("Memora Review Item")
			ri.item_id = item_id
			ri.lesson = doc.name
			ri.subject = doc.subject
			ri.track = doc.track
			ri.unit = doc.unit
			ri.topic = doc.topic
			ri.question_text = question_text
			ri.choice_1 = choices[0]
			ri.choice_2 = choices[1]
			ri.choice_3 = choices[2]
			ri.choice_4 = choices[3]
			ri.correct_choice = correct_choice
			ri.insert(ignore_permissions=True)


_BATCH_SIZE = 10_000


def _delete_review_items_and_memory_state(item_ids: list[str]):
	"""Delete Review Item records and their associated Memory State rows.

	Runs as a background job. Batches large DELETEs to avoid long locks.
	Memory State uses BINARY(16) item_id and is RANGE-partitioned by season_seq.
	We must include season_seq for partition pruning.
	"""
	if not item_ids:
		return

	# Delete Practice Log entries in batches (raw SQL table, idx_item_id index)
	for i in range(0, len(item_ids), _BATCH_SIZE):
		batch = item_ids[i : i + _BATCH_SIZE]
		placeholders = ", ".join(["%s"] * len(batch))
		frappe.db.sql(
			f"DELETE FROM `tabMemora Practice Log` WHERE item_id IN ({placeholders})",
			tuple(batch),
		)
		frappe.db.commit()

	# Delete Memory State records (raw SQL — partitioned table)
	season_seqs = frappe.db.sql(
		"SELECT DISTINCT season_seq FROM `tabMemora Season`",
		pluck="season_seq",
	)

	for season_seq in season_seqs:
		if not season_seq:
			continue
		for i in range(0, len(item_ids), _BATCH_SIZE):
			batch = item_ids[i : i + _BATCH_SIZE]
			params = {"season_seq": season_seq}
			in_parts = []
			for j, iid in enumerate(batch):
				key = f"id_{j}"
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
			frappe.db.commit()

	# Delete Review Item records
	for item_id in item_ids:
		frappe.delete_doc("Memora Review Item", item_id, force=True, ignore_permissions=True)
