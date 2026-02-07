"""
JSON Generator for subject hierarchy, lesson content, and bitmap metadata.

Generates hierarchical JSON files for mobile app consumption:
- _subjects.json: subjects list with track IDs
- track_{id}.json: track with unit IDs
- unit_{id}.json: unit with full topic and lesson metadata (content JSON)
- topic_{id}.json: topic with lesson IDs (navigation metadata)
- lesson_{id}.json: lesson with stages array and configurations
- {subject_id}_b.json: bitmap metadata with bit_range and excluded_bits
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import frappe

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def generate_subject_json(subject_id: str) -> list[dict]:
	"""
	Generate all JSON files for a subject.

	Args:
	    subject_id: The Memora Subject document name (e.g., "SUBJ-00001")

	Returns:
	    List of file dictionaries with:
	    - filename: str (e.g., "_subjects.json", "track_Track-00001.json")
	    - content: str (JSON string)
	    - subject_id: str | None (subject_id for hierarchy files, None for _subjects)
	"""
	files: list[dict] = []

	try:
		subject_doc = frappe.get_doc("Memora Subject", subject_id)
	except frappe.DoesNotExistError:
		logger.warning(f"Subject {subject_id} not found, skipping")
		return files

	# Generate _subjects.json (subject level index)
	subjects_data = _generate_subjects_index(subject_doc)
	files.append({
		"filename": "_subjects.json",
		"content": _to_json(subjects_data),
		"subject_id": subject_id,
	})

	# Get tracks for this subject
	tracks = frappe.get_all(
		"Memora Track",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "track_title", "image", "is_linear"],
		order_by="sort_order asc",
	)

	track_ids = []
	for track in tracks:
		track_ids.append(track.name)

		# Generate track_{id}.json
		track_files = _generate_track_json(track)
		files.extend(track_files)

	# Update subjects data with track_ids
	if subjects_data.get("subjects") and len(subjects_data["subjects"]) > 0:
		subjects_data["subjects"][0]["track_ids"] = track_ids
		files[0]["content"] = _to_json(subjects_data)

	# Generate bitmap metadata
	bitmap_data = _generate_bitmap_json(subject_id, subject_doc)
	files.append({
		"filename": f"{subject_id}_b.json",
		"content": _to_json(bitmap_data),
		"subject_id": subject_id,
	})

	return files


def _generate_subjects_index(subject_doc: Any) -> dict:
	"""Generate _subjects.json structure for a single subject."""
	return {
		"schema_version": SCHEMA_VERSION,
		"subjects": [
			{
				"subject_id": subject_doc.name,
				"title": subject_doc.subject_title,
				"image": _relative_path(subject_doc.image),
				"is_linear": bool(subject_doc.in_linear),
				"track_ids": [],  # Populated after track iteration
			}
		],
	}


def _generate_track_json(track: dict) -> list[dict]:
	"""
	Generate track_{id}.json and all child unit/topic/lesson files.

	Returns list of file dicts for track and all descendants.
	"""
	files: list[dict] = []
	track_id = track["name"]

	# Get units for this track
	units = frappe.get_all(
		"Memora Unit",
		filters={"track": track_id, "is_published": 1},
		fields=["name", "unit_title", "is_linear", "is_free"],
		order_by="sort_order asc",
	)

	unit_ids = [unit["name"] for unit in units]

	# Track JSON
	track_data = {
		"schema_version": SCHEMA_VERSION,
		"track_id": track_id,
		"title": track["track_title"],
		"image": _relative_path(track.get("image")),
		"is_linear": bool(track.get("is_linear")),
		"unit_ids": unit_ids,
	}

	files.append({
		"filename": f"track_{track_id}.json",
		"content": _to_json(track_data),
		"subject_id": None,
	})

	# Generate unit files and their descendants
	for unit in units:
		unit_files = _generate_unit_json(unit)
		files.extend(unit_files)

	return files


def _generate_unit_json(unit: dict) -> list[dict]:
	"""
	Generate unit_{id}.json with full topic and lesson metadata (content JSON).

	Per BUILD-04: unit_{id}.json contains topics array with nested lesson metadata.
	Also generates topic_{id}.json for navigation and lesson_{id}.json for content.

	Returns list of file dicts for unit and all descendants.
	"""
	files: list[dict] = []
	unit_id = unit["name"]

	# Get topics for this unit
	topics = frappe.get_all(
		"Memora Topic",
		filters={"unit": unit_id, "is_published": 1},
		fields=["name", "topic_title", "is_linear", "is_free"],
		order_by="sort_order asc",
	)

	# Build topics array with full lesson metadata for unit JSON
	topics_with_lessons = []
	for topic in topics:
		topic_id = topic["name"]

		# Get lessons for this topic
		lessons = frappe.get_all(
			"Memora Lesson",
			filters={"topic": topic_id},
			fields=["name", "lesson_title", "bit_index"],
			order_by="name asc",
		)

		# Topic with nested lesson metadata
		topic_with_lessons = {
			"topic_id": topic_id,
			"title": topic["topic_title"],
			"is_linear": bool(topic.get("is_linear")),
			"is_free": bool(topic.get("is_free")),
			"lessons": [
				{
					"lesson_id": lesson["name"],
					"title": lesson["lesson_title"],
					"bit_index": lesson.get("bit_index") or 0,
				}
				for lesson in lessons
			],
		}
		topics_with_lessons.append(topic_with_lessons)

		# Generate topic_{id}.json (navigation metadata)
		topic_files = _generate_topic_json(topic, lessons)
		files.extend(topic_files)

		# Generate lesson_{id}.json for each lesson
		for lesson in lessons:
			lesson_file = _generate_lesson_json(lesson["name"])
			if lesson_file:
				files.append(lesson_file)

	# Unit JSON with full topic/lesson content (BUILD-04)
	unit_data = {
		"schema_version": SCHEMA_VERSION,
		"unit_id": unit_id,
		"title": unit["unit_title"],
		"is_linear": bool(unit.get("is_linear")),
		"is_free": bool(unit.get("is_free")),
		"topics": topics_with_lessons,
	}

	files.append({
		"filename": f"unit_{unit_id}.json",
		"content": _to_json(unit_data),
		"subject_id": None,
	})

	return files


def _generate_topic_json(topic: dict, lessons: list[dict]) -> list[dict]:
	"""
	Generate topic_{id}.json with lesson IDs for navigation.

	Returns list with single topic file dict.
	"""
	topic_id = topic["name"]
	lesson_ids = [lesson["name"] for lesson in lessons]

	topic_data = {
		"schema_version": SCHEMA_VERSION,
		"topic_id": topic_id,
		"title": topic["topic_title"],
		"is_linear": bool(topic.get("is_linear")),
		"is_free": bool(topic.get("is_free")),
		"lesson_ids": lesson_ids,
	}

	return [
		{
			"filename": f"topic_{topic_id}.json",
			"content": _to_json(topic_data),
			"subject_id": None,
		}
	]


def _generate_lesson_json(lesson_name: str) -> dict | None:
	"""
	Generate lesson_{id}.json with stages array and configurations.

	Per BUILD-05: lesson JSON includes stages with stage_id, stage_type,
	is_skippable, and parsed config object.

	Returns file dict or None if lesson not found.
	"""
	try:
		lesson_doc = frappe.get_doc("Memora Lesson", lesson_name)
	except frappe.DoesNotExistError:
		logger.warning(f"Lesson {lesson_name} not found, skipping")
		return None

	# Build stages array from child table
	stages = []
	for stage in lesson_doc.stages or []:
		stage_data = {
			"stage_id": stage.name,
			"stage_type": stage.stage_type,
			"is_skippable": bool(stage.is_skippable),
			"config": _parse_stage_config(stage.config_json),
		}
		stages.append(stage_data)

	lesson_data = {
		"schema_version": SCHEMA_VERSION,
		"lesson_id": lesson_doc.name,
		"title": lesson_doc.lesson_title,
		"base_xp": lesson_doc.base_xp or 10,
		"max_hearts": lesson_doc.max_hearts or 3,
		"bit_index": lesson_doc.bit_index or 0,
		"is_reviewable": bool(lesson_doc.is_reviewable),
		"stages": stages,
	}

	return {
		"filename": f"lesson_{lesson_doc.name}.json",
		"content": _to_json(lesson_data),
		"subject_id": None,
	}


def _generate_bitmap_json(subject_id: str, subject_doc: Any) -> dict:
	"""
	Generate {subject_id}_b.json bitmap metadata.

	Contains bit_range (total bits needed) and excluded_bits for progress tracking.
	Excluded bits are lesson bit_index values that should not count toward completion.
	"""
	# Get all lessons for this subject to calculate bit_range
	lessons = frappe.get_all(
		"Memora Lesson",
		filters={"subject": subject_id},
		fields=["bit_index"],
	)

	# Calculate bit_range (highest bit_index + 1, or use last_bit_index from subject)
	bit_indices = [lesson.get("bit_index") or 0 for lesson in lessons]
	if bit_indices:
		bit_range = max(bit_indices) + 1
	else:
		bit_range = subject_doc.last_bit_index or 0

	# excluded_bits: currently empty, can be populated based on lesson flags
	# or other criteria in the future
	excluded_bits: list[int] = []

	return {
		"schema_version": SCHEMA_VERSION,
		"subject_id": subject_id,
		"bit_range": bit_range,
		"excluded_bits": excluded_bits,
		"generated_at": datetime.now(timezone.utc).isoformat(),
	}


def _relative_path(url: str | None) -> str | None:
	"""
	Convert full URL to relative path.

	Strips domain prefix, returns path only.
	Example: "https://cdn.example.com/files/image.png" -> "/files/image.png"
	"""
	if not url:
		return None

	# Handle Frappe file URLs (typically /files/...)
	if url.startswith("/"):
		return url

	# Strip protocol and domain
	if "://" in url:
		# Remove protocol
		path_start = url.find("://") + 3
		# Find first slash after domain
		slash_pos = url.find("/", path_start)
		if slash_pos != -1:
			return url[slash_pos:]

	return url


def _parse_stage_config(config_json_str: str | None) -> dict:
	"""
	Safely parse stage config JSON string.

	Returns empty dict on malformed JSON.
	"""
	if not config_json_str:
		return {}

	try:
		return json.loads(config_json_str)
	except (json.JSONDecodeError, TypeError) as e:
		logger.warning(f"Malformed stage config JSON: {e}, returning empty config")
		return {}


def _to_json(data: dict) -> str:
	"""Convert dict to formatted JSON string with UTF-8 support."""
	return json.dumps(data, ensure_ascii=False, indent=2)
