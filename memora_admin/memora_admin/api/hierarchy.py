"""Frappe API for subject hierarchy operations."""

import frappe


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
	                            "lessons": [
	                                {"lesson_id": "LESSON-001", "bit_index": 0, "xp": 10}
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

	# Build hierarchy
	free_units = []
	free_topics = []

	hierarchy = {
		"subject_id": subject.name,
		"version": getattr(subject, "version", 1),
		"bit_range": 0,  # Will be calculated
		"excluded_bits": [],
		"is_linear": getattr(subject, "is_linear", True),
		"free_units": free_units,  # Will be populated
		"free_topics": free_topics,  # Will be populated
		"tracks": [],
	}

	# Get tracks ordered by idx
	tracks = frappe.get_all(
		"Memora Track",
		filters={"subject": subject_id},
		fields=["name", "is_linear"],
		order_by="idx asc",
	)

	max_bit_index = -1  # Track highest bit_index seen

	for track in tracks:
		track_info = {
			"track_id": track.name,
			"is_linear": track.is_linear if track.is_linear is not None else True,
			"units": [],
		}

		# Get units ordered by idx
		units = frappe.get_all(
			"Memora Unit",
			filters={"track": track.name},
			fields=["name", "is_linear", "is_free"],
			order_by="idx asc",
		)

		for unit in units:
			unit_is_free = unit.is_free if unit.is_free is not None else False

			unit_info = {
				"unit_id": unit.name,
				"is_linear": unit.is_linear if unit.is_linear is not None else True,
				"is_free": unit_is_free,
				"topics": [],
			}

			# Track free units
			if unit_is_free:
				free_units.append(unit.name)

			# Get topics ordered by idx
			topics = frappe.get_all(
				"Memora Topic",
				filters={"unit": unit.name},
				fields=["name", "is_linear", "is_free"],
				order_by="idx asc",
			)

			for topic in topics:
				topic_is_free = topic.is_free if topic.is_free is not None else False

				# If any topic in unit is free, mark unit as free
				if topic_is_free and not unit_is_free:
					free_topics.append(topic.name)
					unit_info["is_free"] = True

				topic_info = {
					"topic_id": topic.name,
					"is_linear": (topic.is_linear if topic.is_linear is not None else True),
					"is_free": topic_is_free,
					"lessons": [],
				}

				# Get lessons ordered by idx, reading persisted bit_index from DB
				lessons = frappe.get_all(
					"Memora Lesson",
					filters={"topic": topic.name},
					fields=["name", "xp", "bit_index"],
					order_by="idx asc",
				)

				for lesson in lessons:
					lesson_bit_index = lesson.bit_index or 0
					lesson_info = {
						"lesson_id": lesson.name,
						"bit_index": lesson_bit_index,
						"xp": lesson.xp if lesson.xp else 10,  # Default 10 XP
					}
					topic_info["lessons"].append(lesson_info)
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

	return hierarchy
