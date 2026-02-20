"""
Plan-centric JSON Generator for mobile app consumption.

Generates hierarchical JSON files organized by plan:
- plans/{plan_id}/manifest.json: Plan metadata + subject list
- plans/{plan_id}/subjects/{subject_id}/_h.json: Subject hierarchy (with Plan Overrides)
- plans/{plan_id}/subjects/{subject_id}/units/{unit_id}_c.json: Unit content
- lessons/{lesson_id}.json: Shared lesson content (not per-plan)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import frappe

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class PlanSubjectTree:
	"""Pre-fetched subject tree data shared across manifest, hierarchy, and content generation."""

	tracks: list = field(default_factory=list)
	units_by_track: dict = field(default_factory=dict)
	topics_by_unit: dict = field(default_factory=dict)
	lessons_by_topic: dict = field(default_factory=dict)
	stages_by_lesson: dict = field(default_factory=dict)
	skippable_types: set = field(default_factory=set)


def _prefetch_plan_subject_tree(subject_id: str) -> PlanSubjectTree:
	"""Bulk-fetch entire subject tree in 6 queries (was 3x N+1 nested loops)."""
	tree = PlanSubjectTree()

	# Query 1: tracks (superset of fields needed by all 3 consumers)
	tree.tracks = frappe.get_all(
		"Memora Track",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "track_title", "sort_order", "is_linear", "image", "is_sold_separately"],
		order_by="sort_order asc",
	)

	# Query 2: units
	all_units = frappe.get_all(
		"Memora Unit",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "track", "unit_title", "sort_order", "is_linear", "is_free"],
		order_by="sort_order asc",
	)
	units_by_track = defaultdict(list)
	for u in all_units:
		units_by_track[u.track].append(u)
	tree.units_by_track = units_by_track

	# Query 3: topics
	all_topics = frappe.get_all(
		"Memora Topic",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "unit", "topic_title", "sort_order", "is_linear", "is_free"],
		order_by="sort_order asc",
	)
	topics_by_unit = defaultdict(list)
	for t in all_topics:
		topics_by_unit[t.unit].append(t)
	tree.topics_by_unit = topics_by_unit

	# Query 4: lessons (published only)
	all_lessons = frappe.get_all(
		"Memora Lesson",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "topic", "lesson_title", "bit_index", "base_xp", "max_hearts", "is_reviewable"],
		order_by="name asc",
	)
	lessons_by_topic = defaultdict(list)
	for l in all_lessons:
		lessons_by_topic[l.topic].append(l)
	tree.lessons_by_topic = lessons_by_topic

	# Query 5: stages via JOIN (avoids IN clause on massive child table)
	raw_stages = frappe.db.sql(
		"""
		SELECT ls.parent, ls.name, ls.stage_type, ls.idx,
		       ls.is_skippable, ls.config_json
		FROM `tabMemora Lesson Stage` ls
		INNER JOIN `tabMemora Lesson` l
		    ON ls.parent = l.name AND ls.parenttype = 'Memora Lesson'
		WHERE l.subject = %(subject)s AND l.is_published = 1
		ORDER BY ls.parent, ls.idx
		""",
		{"subject": subject_id},
		as_dict=True,
	)
	stages_by_lesson = defaultdict(list)
	for s in raw_stages:
		stages_by_lesson[s.parent].append(s)
	tree.stages_by_lesson = stages_by_lesson

	# Query 6: skippable stage types (single small query)
	tree.skippable_types = _get_skippable_stage_types()

	return tree


def generate_plan_json(plan_id: str) -> list[dict]:
	"""
	Generate all JSON files for an academic plan.

	Args:
	    plan_id: The Memora Academic Plan document name (e.g., "PLAN-00001")

	Returns:
	    List of file dictionaries with:
	    - filename: str (path including plan folder)
	    - content: str (JSON string)
	"""
	files: list[dict] = []

	try:
		plan_doc = frappe.get_doc("Memora Academic Plan", plan_id)
	except frappe.DoesNotExistError:
		logger.warning(f"Plan {plan_id} not found, skipping")
		return files

	# Load Plan Overrides once for efficiency
	overrides = _load_plan_overrides(plan_id)

	# Get plan subjects from child table (keep full objects for is_premium, alias_title)
	plan_subjects = plan_doc.plan_subjects or []

	if not plan_subjects:
		logger.warning(f"Plan {plan_id} has no subjects, skipping")
		return files

	# Build lookup dict for Plan Subject metadata (is_premium, alias_title)
	plan_subject_meta = {
		ps.subject: {
			"is_premium": bool(ps.is_premium) if hasattr(ps, "is_premium") else True,
			"alias_title": getattr(ps, "alias_title", None),
		}
		for ps in plan_subjects
	}

	# Extract subject IDs for iteration
	subject_ids = [ps.subject for ps in plan_subjects]

	# Pre-fetch trees for all subjects (shared across manifest + subject files)
	subject_trees = {}
	for subject_id in subject_ids:
		subject_trees[subject_id] = _prefetch_plan_subject_tree(subject_id)

	# Generate manifest
	manifest_data = _generate_manifest(plan_doc, subject_ids, overrides, plan_subject_meta, subject_trees)
	files.append(
		{
			"filename": f"plans/{plan_id}/manifest.json",
			"content": _to_json(manifest_data),
		}
	)

	# Generate per-subject files
	for subject_id in subject_ids:
		subject_files = _generate_subject_files(plan_id, subject_id, overrides, subject_trees[subject_id])
		files.extend(subject_files)

	return files


def _load_plan_overrides(plan_id: str) -> dict[str, dict]:
	"""
	Load all Plan Overrides for a plan.

	Returns dict keyed by (ref_doctype, ref_name) with action.
	"""
	overrides_raw = frappe.get_all(
		"Memora Plan Overrider",
		filters={"plan": plan_id},
		fields=["ref_doctype", "ref_name", "action"],
	)

	overrides = {}
	for ovr in overrides_raw:
		key = (ovr["ref_doctype"], ovr["ref_name"])
		overrides[key] = {"action": ovr["action"]}

	return overrides


def _is_hidden(overrides: dict, doctype: str, name: str) -> bool:
	"""Check if item is hidden by Plan Override."""
	key = (doctype, name)
	return overrides.get(key, {}).get("action") == "Hide"


def _is_override_free(overrides: dict, doctype: str, name: str) -> bool | None:
	"""Check if item has 'Set Free' override. Returns None if no override."""
	key = (doctype, name)
	if overrides.get(key, {}).get("action") == "Set Free":
		return True
	return None


def _update_plan_subject_metadata(plan_id: str, subject_id: str, free_content: dict) -> None:
	"""
	Update Memora Plan Subject child table row with free content metadata.

	Args:
		plan_id: The Memora Academic Plan document name
		subject_id: The Memora Subject document name
		free_content: Dict with "free_units" and "free_topics" arrays
	"""
	try:
		plan_doc = frappe.get_doc("Memora Academic Plan", plan_id)

		# Find the Plan Subject row for this subject
		for plan_subject in plan_doc.plan_subjects or []:
			if plan_subject.subject == subject_id:
				# Update meta_data field with free content index
				plan_subject.meta_data = json.dumps(free_content)
				plan_doc.save()
				return
	except Exception as e:
		logger.error(f"Failed to update meta_data for Plan {plan_id}, Subject {subject_id}: {e}")


def _generate_manifest(
	plan_doc: Any,
	subject_ids: list[str],
	overrides: dict,
	plan_subject_meta: dict,
	subject_trees: dict[str, PlanSubjectTree],
) -> dict:
	"""Generate manifest.json for the plan.

	Args:
		plan_doc: The Memora Academic Plan document
		subject_ids: List of subject IDs in the plan
		overrides: Plan Overrides dict
		plan_subject_meta: Dict of subject_id -> {is_premium, alias_title} from Plan Subject child table
		subject_trees: Dict of subject_id -> PlanSubjectTree
	"""
	version = int(datetime.now(timezone.utc).timestamp())

	# Get grade and major titles
	grade_title = ""
	major_title = ""

	if plan_doc.grade:
		grade_title = frappe.get_cached_value("Memora Grade", plan_doc.grade, "grade_title") or ""
	if plan_doc.major:
		major_title = frappe.get_cached_value("Memora Major", plan_doc.major, "major_title") or ""

	# Build subjects array
	subjects = []
	for subject_id in subject_ids:
		if _is_hidden(overrides, "Memora Subject", subject_id):
			continue

		try:
			subject_doc = frappe.get_doc("Memora Subject", subject_id)
		except frappe.DoesNotExistError:
			continue

		# Calculate stats and is_free_preview (from pre-fetched tree)
		stats = _calculate_subject_stats(subject_id, overrides, subject_trees[subject_id])

		# Get is_premium and alias_title from Plan Subject (not from Subject itself)
		ps_meta = plan_subject_meta.get(subject_id, {})

		subjects.append(
			{
				"id": subject_id,
				"title": subject_doc.subject_title,
				"alias_title": ps_meta.get("alias_title"),
				"image": _relative_path(subject_doc.image),
				"total_lessons": stats["total_lessons"],
				"total_tracks": stats["total_tracks"],
				"is_premium": ps_meta.get("is_premium", True),
				"is_free_preview": stats["is_free_preview"],
				"hierarchy_url": f"/files/cdn/plans/{plan_doc.name}/subjects/{subject_id}/_h.json?v={version}",
			}
		)

	return {
		"schema_version": SCHEMA_VERSION,
		"version": version,
		"generated_at": datetime.now(timezone.utc).isoformat(),
		"plan_id": plan_doc.name,
		"title": plan_doc.plan_name,
		"grade_id": plan_doc.grade,
		"grade_title": grade_title,
		"major_id": plan_doc.major,
		"major_title": major_title,
		"season_id": plan_doc.season,
		"subjects": subjects,
	}


def _calculate_subject_stats(subject_id: str, overrides: dict, tree: PlanSubjectTree) -> dict:
	"""
	Calculate subject statistics with Plan Overrides applied (from pre-fetched tree).

	Returns:
	    dict with total_lessons, total_tracks, is_free_preview
	"""
	total_lessons = 0
	total_tracks = 0
	is_free_preview = False

	for track in tree.tracks:
		if _is_hidden(overrides, "Memora Track", track["name"]):
			continue

		total_tracks += 1

		for unit in tree.units_by_track[track["name"]]:
			if _is_hidden(overrides, "Memora Unit", unit["name"]):
				continue

			# Check unit is_free (with override)
			unit_is_free = _is_override_free(overrides, "Memora Unit", unit["name"])
			if unit_is_free is None:
				unit_is_free = bool(unit.get("is_free"))

			if unit_is_free:
				is_free_preview = True

			for topic in tree.topics_by_unit[unit["name"]]:
				if _is_hidden(overrides, "Memora Topic", topic["name"]):
					continue

				# Check topic is_free (with override)
				topic_is_free = _is_override_free(overrides, "Memora Topic", topic["name"])
				if topic_is_free is None:
					topic_is_free = bool(topic.get("is_free"))

				if topic_is_free:
					is_free_preview = True

				# Count published lessons from pre-fetched tree
				total_lessons += len(tree.lessons_by_topic[topic["name"]])

	return {
		"total_lessons": total_lessons,
		"total_tracks": total_tracks,
		"is_free_preview": is_free_preview,
	}


def _generate_subject_files(
	plan_id: str,
	subject_id: str,
	overrides: dict,
	tree: PlanSubjectTree,
) -> list[dict]:
	"""Generate hierarchy and unit content files for a subject (from pre-fetched tree)."""
	files: list[dict] = []

	try:
		subject_doc = frappe.get_doc("Memora Subject", subject_id)
	except frappe.DoesNotExistError:
		return files

	# Generate _h.json (hierarchy with Plan Overrides applied)
	hierarchy_data, free_content = _generate_hierarchy(plan_id, subject_doc, overrides, tree)
	files.append(
		{
			"filename": f"plans/{plan_id}/subjects/{subject_id}/_h.json",
			"content": _to_json(hierarchy_data),
		}
	)

	# Update Plan Subject meta_data with free content index
	_update_plan_subject_metadata(plan_id, subject_id, free_content)

	# Generate unit content files and lesson files from tree
	version = int(datetime.now(timezone.utc).timestamp())

	for track in tree.tracks:
		if _is_hidden(overrides, "Memora Track", track["name"]):
			continue

		for unit in tree.units_by_track[track["name"]]:
			if _is_hidden(overrides, "Memora Unit", unit["name"]):
				continue

			unit_content = _generate_unit_content_from_tree(unit, overrides, tree, version)
			files.append(
				{
					"filename": f"plans/{plan_id}/subjects/{subject_id}/units/{unit['name']}_c.json",
					"content": _to_json(unit_content),
				}
			)

			# Generate lesson files (shared)
			for topic in tree.topics_by_unit[unit["name"]]:
				if _is_hidden(overrides, "Memora Topic", topic["name"]):
					continue

				for lesson in tree.lessons_by_topic[topic["name"]]:
					lesson_file = _generate_lesson_json_from_tree(lesson, tree)
					if lesson_file:
						files.append(lesson_file)

	return files


def _generate_hierarchy(
	plan_id: str,
	subject_doc: Any,
	overrides: dict,
	tree: PlanSubjectTree,
) -> tuple[dict, dict]:
	"""Generate _h.json subject hierarchy with Plan Overrides applied (from pre-fetched tree).

	Returns:
		Tuple of (hierarchy_data, free_content_index)
		free_content_index: {"free_units": [...], "free_topics": [...]}
	"""
	version = int(datetime.now(timezone.utc).timestamp())

	tracks_data = []
	free_units = []
	free_topics = []

	for track in tree.tracks:
		if _is_hidden(overrides, "Memora Track", track["name"]):
			continue

		units_data = []

		for unit in tree.units_by_track[track["name"]]:
			if _is_hidden(overrides, "Memora Unit", unit["name"]):
				continue

			# Apply is_free override for unit
			unit_is_free = _is_override_free(overrides, "Memora Unit", unit["name"])
			if unit_is_free is None:
				unit_is_free = bool(unit.get("is_free"))

			# Track free units
			if unit_is_free:
				free_units.append(unit["name"])

			# If unit is not already free, check if any topic is free
			if not unit_is_free:
				for topic in tree.topics_by_unit[unit["name"]]:
					if _is_hidden(overrides, "Memora Topic", topic["name"]):
						continue
					topic_is_free = _is_override_free(overrides, "Memora Topic", topic["name"])
					if topic_is_free is None:
						topic_is_free = bool(topic.get("is_free"))
					if topic_is_free:
						unit_is_free = True
						free_topics.append(topic["name"])

			units_data.append(
				{
					"id": unit["name"],
					"title": unit["unit_title"],
					"sort_order": unit["sort_order"] or 0,
					"is_linear": bool(unit.get("is_linear")),
					"is_free": unit_is_free,
					"content_url": f"/files/cdn/plans/{plan_id}/subjects/{subject_doc.name}/units/{unit['name']}_c.json?v={version}",
				}
			)

		tracks_data.append(
			{
				"id": track["name"],
				"title": track["track_title"],
				"sort_order": track["sort_order"] or 0,
				"is_linear": bool(track.get("is_linear")),
				"is_sold_separately": bool(track.get("is_sold_separately")),
				"image": _relative_path(track.get("image")),
				"units": units_data,
			}
		)

	hierarchy_data = {
		"schema_version": SCHEMA_VERSION,
		"version": version,
		"subject_id": subject_doc.name,
		"title": subject_doc.subject_title,
		"is_linear": bool(getattr(subject_doc, "is_linear", True)),
		"tracks": tracks_data,
	}

	free_content = {
		"free_units": free_units,
		"free_topics": free_topics,
	}

	return hierarchy_data, free_content


def _generate_unit_content_from_tree(
	unit: dict,
	overrides: dict,
	tree: PlanSubjectTree,
	version: int,
) -> dict:
	"""Generate unit content JSON from pre-fetched tree data."""
	unit_id = unit["name"]

	# Apply is_free override
	unit_is_free = _is_override_free(overrides, "Memora Unit", unit_id)
	if unit_is_free is None:
		unit_is_free = bool(unit.get("is_free"))

	topics_data = []
	for topic in tree.topics_by_unit[unit_id]:
		if _is_hidden(overrides, "Memora Topic", topic["name"]):
			continue

		# Apply is_free override
		topic_is_free = _is_override_free(overrides, "Memora Topic", topic["name"])
		if topic_is_free is None:
			topic_is_free = bool(topic.get("is_free"))

		lessons = tree.lessons_by_topic[topic["name"]]

		lessons_data = [
			{
				"id": lesson["name"],
				"title": lesson["lesson_title"],
				"bit_index": lesson.get("bit_index") or 0,
				"content_url": f"/files/cdn/lessons/{lesson['name']}.json?v={version}",
			}
			for lesson in lessons
		]

		topics_data.append(
			{
				"id": topic["name"],
				"title": topic["topic_title"],
				"sort_order": topic["sort_order"] or 0,
				"is_linear": bool(topic.get("is_linear")),
				"is_free": topic_is_free,
				"lessons": lessons_data,
			}
		)

	return {
		"schema_version": SCHEMA_VERSION,
		"version": version,
		"unit_id": unit_id,
		"title": unit["unit_title"],
		"is_linear": bool(unit.get("is_linear")),
		"is_free": unit_is_free,
		"topics": topics_data,
	}


def _generate_lesson_json_from_tree(lesson: dict, tree: PlanSubjectTree) -> dict | None:
	"""Generate lesson JSON from pre-fetched tree data (no get_doc calls)."""
	lesson_name = lesson["name"]
	stages_raw = tree.stages_by_lesson.get(lesson_name, [])

	stages = []
	for stage in stages_raw:
		effective_skippable = bool(stage.is_skippable) or (stage.stage_type in tree.skippable_types)

		config = _parse_stage_config(stage.config_json)
		if effective_skippable:
			_strip_item_ids(config)

		stage_data = {
			"stage_id": stage.name,
			"stage_type": stage.stage_type,
			"is_skippable": effective_skippable,
			"config": config,
		}
		stages.append(stage_data)

	lesson_data = {
		"schema_version": SCHEMA_VERSION,
		"version": int(datetime.now(timezone.utc).timestamp()),
		"lesson_id": lesson_name,
		"title": lesson["lesson_title"],
		"base_xp": lesson.get("base_xp") or 10,
		"max_hearts": lesson.get("max_hearts") or 3,
		"bit_index": lesson.get("bit_index") or 0,
		"is_reviewable": bool(lesson.get("is_reviewable")),
		"stages": stages,
	}

	return {
		"filename": f"lessons/{lesson_name}.json",
		"content": _to_json(lesson_data),
	}


def _get_skippable_stage_types() -> set[str]:
	"""Get set of stage type names where is_skippable=1 globally."""
	stages = frappe.get_all(
		"Memora Lesson Stage Settings",
		filters={"is_skippable": 1},
		fields=["stage_title"],
	)
	return {s.stage_title for s in stages}


def _strip_item_ids(config: dict) -> None:
	"""Remove item_id keys from config when stage is skippable."""
	for _key, value in config.items():
		if isinstance(value, list):
			for item in value:
				if isinstance(item, dict):
					item.pop("item_id", None)
					# Recurse for nested children (MINDMAP)
					if "children" in item and isinstance(item["children"], list):
						for child in item["children"]:
							if isinstance(child, dict):
								child.pop("item_id", None)


def _generate_lesson_json(lesson_name: str) -> dict | None:
	"""Generate lesson JSON (shared across plans). Fallback for non-tree usage."""
	try:
		lesson_doc = frappe.get_doc("Memora Lesson", lesson_name)
	except frappe.DoesNotExistError:
		logger.warning(f"Lesson {lesson_name} not found, skipping")
		return None

	# Fetch global skippable types once per lesson
	skippable_types = _get_skippable_stage_types()

	stages = []
	for stage in lesson_doc.stages or []:
		# Two-tier resolution: per-stage override then global stage type setting
		effective_skippable = bool(stage.is_skippable) or (stage.stage_type in skippable_types)

		config = _parse_stage_config(stage.config_json)
		if effective_skippable:
			_strip_item_ids(config)

		stage_data = {
			"stage_id": stage.name,
			"stage_type": stage.stage_type,
			"is_skippable": effective_skippable,
			"config": config,
		}
		stages.append(stage_data)

	lesson_data = {
		"schema_version": SCHEMA_VERSION,
		"version": int(datetime.now(timezone.utc).timestamp()),
		"lesson_id": lesson_doc.name,
		"title": lesson_doc.lesson_title,
		"base_xp": lesson_doc.base_xp or 10,
		"max_hearts": lesson_doc.max_hearts or 3,
		"bit_index": lesson_doc.bit_index or 0,
		"is_reviewable": bool(lesson_doc.is_reviewable),
		"stages": stages,
	}

	return {
		"filename": f"lessons/{lesson_doc.name}.json",
		"content": _to_json(lesson_data),
	}


def _generate_unit_content(unit_id: str, overrides: dict) -> dict:
	"""Generate unit content JSON with topics and lessons. Fallback for non-tree usage."""
	version = int(datetime.now(timezone.utc).timestamp())

	try:
		unit_doc = frappe.get_doc("Memora Unit", unit_id)
	except frappe.DoesNotExistError:
		return {"error": f"Unit {unit_id} not found"}

	# Apply is_free override
	unit_is_free = _is_override_free(overrides, "Memora Unit", unit_id)
	if unit_is_free is None:
		unit_is_free = bool(unit_doc.is_free)

	topics_data = []
	topics = frappe.get_all(
		"Memora Topic",
		filters={"unit": unit_id, "is_published": 1},
		fields=["name", "topic_title", "sort_order", "is_linear", "is_free"],
		order_by="sort_order asc",
	)

	for topic in topics:
		if _is_hidden(overrides, "Memora Topic", topic["name"]):
			continue

		# Apply is_free override
		topic_is_free = _is_override_free(overrides, "Memora Topic", topic["name"])
		if topic_is_free is None:
			topic_is_free = bool(topic.get("is_free"))

		lessons = frappe.get_all(
			"Memora Lesson",
			filters={"topic": topic["name"], "is_published": 1},
			fields=["name", "lesson_title", "bit_index"],
			order_by="name asc",
		)

		lessons_data = [
			{
				"id": lesson["name"],
				"title": lesson["lesson_title"],
				"bit_index": lesson.get("bit_index") or 0,
				"content_url": f"/files/cdn/lessons/{lesson['name']}.json?v={version}",
			}
			for lesson in lessons
		]

		topics_data.append(
			{
				"id": topic["name"],
				"title": topic["topic_title"],
				"sort_order": topic["sort_order"] or 0,
				"is_linear": bool(topic.get("is_linear")),
				"is_free": topic_is_free,
				"lessons": lessons_data,
			}
		)

	return {
		"schema_version": SCHEMA_VERSION,
		"version": version,
		"unit_id": unit_id,
		"title": unit_doc.unit_title,
		"is_linear": bool(unit_doc.is_linear),
		"is_free": unit_is_free,
		"topics": topics_data,
	}


def _relative_path(url: str | None) -> str | None:
	"""Convert full URL to relative path."""
	if not url:
		return None
	if url.startswith("/"):
		return url
	if "://" in url:
		path_start = url.find("://") + 3
		slash_pos = url.find("/", path_start)
		if slash_pos != -1:
			return url[slash_pos:]
	return url


def _parse_stage_config(config_json_str: str | None) -> dict:
	"""Safely parse stage config JSON string."""
	if not config_json_str:
		return {}
	try:
		return json.loads(config_json_str)
	except (json.JSONDecodeError, TypeError):
		return {}


def _to_json(data: dict) -> str:
	"""Convert dict to formatted JSON string with UTF-8 support."""
	return json.dumps(data, ensure_ascii=False, indent=2)
