"""Unlock state calculation for linear progression enforcement."""

from fastapi_app.models.progress import (
	SubjectHierarchy,
	TopicInfo,
	TrackInfo,
	UnitInfo,
)


def _is_topic_complete(topic: TopicInfo, completed_bits: set[int]) -> bool:
	"""Check if all lessons in topic are complete."""
	return all(lesson.bit_index in completed_bits for lesson in topic.lessons)


def _is_unit_complete(unit: UnitInfo, completed_bits: set[int]) -> bool:
	"""Check if all topics in unit are complete."""
	return all(_is_topic_complete(topic, completed_bits) for topic in unit.topics)


def _is_track_complete(track: TrackInfo, completed_bits: set[int]) -> bool:
	"""Check if all units in track are complete."""
	return all(_is_unit_complete(unit, completed_bits) for unit in track.units)


def calculate_unlock_state(
	hierarchy: SubjectHierarchy,
	completed_bits: set[int],
) -> dict[str, bool]:
	"""
	Calculate unlock state for all lessons in subject.

	Per CONTEXT.md unlock rules:
	- First item in any sequence is ALWAYS unlocked
	- is_linear at Track level: units must complete in order
	- is_linear at Unit level: topics must complete in order
	- is_linear at Topic level: lessons must complete in order
	- Unlock requires 100% completion of previous item

	Args:
	    hierarchy: Subject hierarchy with is_linear flags
	    completed_bits: Set of completed lesson bit_indexes

	Returns:
	    Dict mapping lesson_id to unlocked status
	"""
	unlock_states: dict[str, bool] = {}

	for track_idx, track in enumerate(hierarchy.tracks):
		# Track-level: first track always unlocked
		track_unlocked = track_idx == 0 or not hierarchy.is_linear

		if track_idx > 0 and hierarchy.is_linear:
			# Previous track must be 100% complete
			prev_track = hierarchy.tracks[track_idx - 1]
			track_unlocked = _is_track_complete(prev_track, completed_bits)

		for unit_idx, unit in enumerate(track.units):
			# Unit-level: first unit always unlocked if track unlocked
			unit_unlocked = track_unlocked and (unit_idx == 0 or not track.is_linear)

			if unit_idx > 0 and track.is_linear:
				prev_unit = track.units[unit_idx - 1]
				unit_unlocked = track_unlocked and _is_unit_complete(prev_unit, completed_bits)

			for topic_idx, topic in enumerate(unit.topics):
				# Topic-level: first topic always unlocked if unit unlocked
				topic_unlocked = unit_unlocked and (topic_idx == 0 or not unit.is_linear)

				if topic_idx > 0 and unit.is_linear:
					prev_topic = unit.topics[topic_idx - 1]
					topic_unlocked = unit_unlocked and _is_topic_complete(prev_topic, completed_bits)

				for lesson_idx, lesson in enumerate(topic.lessons):
					# Lesson-level: first lesson always unlocked if topic unlocked
					lesson_unlocked = topic_unlocked and (lesson_idx == 0 or not topic.is_linear)

					if lesson_idx > 0 and topic.is_linear:
						prev_lesson = topic.lessons[lesson_idx - 1]
						lesson_unlocked = topic_unlocked and prev_lesson.bit_index in completed_bits

					unlock_states[lesson.lesson_id] = lesson_unlocked

	return unlock_states


def is_lesson_unlocked(
	lesson_id: str,
	hierarchy: SubjectHierarchy,
	completed_bits: set[int],
) -> bool:
	"""
	Check if specific lesson is unlocked.

	Convenience wrapper around calculate_unlock_state.
	"""
	unlock_states = calculate_unlock_state(hierarchy, completed_bits)
	return unlock_states.get(lesson_id, False)
