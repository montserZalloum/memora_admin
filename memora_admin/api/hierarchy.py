"""Frappe API for subject hierarchy operations."""

import hashlib
import json
from collections import defaultdict

import frappe

from memora_admin.utils.review_item_counts import get_question_counts_by_topic


def _compute_content_hash(hierarchy: dict) -> str:
	"""Compute a structural fingerprint for the hierarchy.

	Hashes only fields that affect stats totals (bit_range, excluded_bits,
	track/unit/topic/lesson IDs, lesson counts and bit_indices).
	Ignores is_linear, is_free, xp, max_hearts — they don't affect totals.

	Returns:
	    8-character hex string (32 bits, MD5 truncated).
	"""
	h = hashlib.md5()
	h.update(str(hierarchy["bit_range"]).encode())
	excluded = hierarchy.get("excluded_bits", [])
	h.update(str(len(excluded)).encode())
	for eb in sorted(excluded):
		h.update(str(eb).encode())
	for track in hierarchy["tracks"]:
		h.update(track["track_id"].encode())
		for unit in track["units"]:
			h.update(unit["unit_id"].encode())
			for topic in unit["topics"]:
				h.update(topic["topic_id"].encode())
				h.update(str(len(topic["lessons"])).encode())
				for lesson in topic["lessons"]:
					h.update(lesson["lesson_id"].encode())
					h.update(str(lesson["bit_index"]).encode())
	return h.hexdigest()[:8]


def _get_free_content_from_plan(subject_id: str) -> tuple[list[str], list[str]]:
	"""Read free_units/free_topics from ALL Plan Subject records.

	Collects free content metadata from all plans containing this subject,
	regardless of is_premium status. A premium subject can still have
	individual free topics/units as samples.

	Returns:
	    Tuple of (free_units, free_topics) lists. Merged across all plans.
	"""
	free_units_set = set()
	free_topics_set = set()

	# Query ALL Plan Subject records (premium subjects can have free topics/units)
	plan_subjects = frappe.get_all(
		"Memora Plan Subject",
		filters={"subject": subject_id},
		fields=["meta_data"],
	)

	for ps in plan_subjects:
		if not ps.meta_data:
			continue
		try:
			data = json.loads(ps.meta_data) if isinstance(ps.meta_data, str) else ps.meta_data
			free_units_set.update(data.get("free_units", []))
			free_topics_set.update(data.get("free_topics", []))
		except (json.JSONDecodeError, AttributeError):
			continue

	return list(free_units_set), list(free_topics_set)


@frappe.whitelist(allow_guest=False)
def get_subject_hierarchy(subject_id: str) -> dict | None:
	"""
	Get full subject hierarchy for unlock state calculation.

	Returns nested structure:
	{
	    "subject_id": "MATH-G5",
	    "version": 1,
	    "bit_range": 100,
	    "excluded_bits": [],
	    "is_linear": true,
	    "tracks": [
	        {
	            "track_id": "TRK-001",
	            "is_linear": true,
	            "units": [
	                {
	                    "unit_id": "UNIT-001",
	                    "is_linear": true,
	                    "is_free": false,
	                    "topics": [
	                        {
	                            "topic_id": "TOPIC-001",
	                            "is_linear": true,
	                            "is_free": false,
	                            "lessons": [
	                                {"lesson_id": "LESSON-001", "bit_index": 0, "xp": 100, "max_hearts": 5}
	                            ]
	                        }
	                    ]
	                }
	            ]
	        }
	    ]
	}
	"""
	# Get subject
	if not frappe.db.exists("Memora Subject", subject_id):
		return None

	subject = frappe.get_doc("Memora Subject", subject_id)

	if not subject.is_published:
		return None

	# Load Memora Settings defaults for fallback
	settings = frappe.get_single("Memora Settings")
	default_base_xp = settings.base_lesson_xp or 100
	default_max_hearts = settings.default_max_hearts or 5

	# Read free content index from Plan Subject meta_data (avoids looping units/topics)
	free_units, free_topics = _get_free_content_from_plan(subject_id)

	hierarchy = {
		"subject_id": subject.name,
		"version": getattr(subject, "version", 1),
		"bit_range": 0,  # Will be calculated
		"excluded_bits": [],
		"is_linear": getattr(subject, "is_linear", True),
		"tracks": [],
	}

	# --- Bulk-fetch MCQ counts per topic (for Challenge Hub) ---
	mcq_count_by_topic = get_question_counts_by_topic(subject_id)

	# --- Bulk-fetch all entities in 4 queries (was N+1 nested loops) ---
	all_tracks = frappe.get_all(
		"Memora Track",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "track_title", "is_linear", "is_sold_separately"],
		order_by="idx asc",
	)

	all_units = frappe.get_all(
		"Memora Unit",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "track", "unit_title", "is_linear", "is_free"],
		order_by="idx asc",
	)

	all_topics = frappe.get_all(
		"Memora Topic",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "unit", "topic_title", "is_linear", "is_free"],
		order_by="idx asc",
	)

	# ALL lessons (published + unpublished) in one query — split in Python
	all_lessons = frappe.get_all(
		"Memora Lesson",
		filters={"subject": subject_id},
		fields=["name", "topic", "base_xp", "max_hearts", "bit_index", "is_published"],
		order_by="idx asc",
	)

	# --- Group by parent ---
	units_by_track = defaultdict(list)
	for u in all_units:
		units_by_track[u.track].append(u)

	topics_by_unit = defaultdict(list)
	for t in all_topics:
		topics_by_unit[t.unit].append(t)

	lessons_by_topic = defaultdict(list)
	for l in all_lessons:
		lessons_by_topic[l.topic].append(l)

	# --- Single-pass assemble ---
	max_bit_index = -1

	for track in all_tracks:
		track_info = {
			"track_id": track.name,
			"track_title": track.track_title or track.name,
			"is_linear": track.is_linear if track.is_linear is not None else True,
			"is_sold_separately": bool(track.get("is_sold_separately")),
			"units": [],
		}

		for unit in units_by_track[track.name]:
			unit_info = {
				"unit_id": unit.name,
				"unit_title": unit.unit_title or unit.name,
				"is_linear": unit.is_linear if unit.is_linear is not None else True,
				"is_free": unit.is_free if unit.is_free is not None else False,
				"topics": [],
			}

			for topic in topics_by_unit[unit.name]:
				topic_info = {
					"topic_id": topic.name,
					"topic_title": topic.topic_title or topic.name,
					"is_linear": topic.is_linear if topic.is_linear is not None else True,
					"is_free": topic.is_free if topic.is_free is not None else False,
					"mcq_count": mcq_count_by_topic.get(topic.name, 0),
					"lessons": [],
				}

				for lesson in lessons_by_topic[topic.name]:
					lesson_bit_index = lesson.bit_index or 0
					if lesson.is_published:
						topic_info["lessons"].append(
							{
								"lesson_id": lesson.name,
								"bit_index": lesson_bit_index,
								"xp": lesson.base_xp if lesson.base_xp else default_base_xp,
								"max_hearts": lesson.max_hearts if lesson.max_hearts else default_max_hearts,
							}
						)
					else:
						if lesson.bit_index is not None:
							hierarchy["excluded_bits"].append(lesson_bit_index)
					if lesson_bit_index > max_bit_index:
						max_bit_index = lesson_bit_index

				unit_info["topics"].append(topic_info)

			track_info["units"].append(unit_info)

		hierarchy["tracks"].append(track_info)

	# bit_range = highest bit_index + 1, or use subject counter as fallback
	hierarchy["bit_range"] = (
		max(max_bit_index + 1, subject.last_bit_index or 0)
		if max_bit_index >= 0
		else (subject.last_bit_index or 0)
	)
	hierarchy["free_units"] = free_units
	hierarchy["free_topics"] = free_topics
	hierarchy["content_hash"] = _compute_content_hash(hierarchy)

	return hierarchy
