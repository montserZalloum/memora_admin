"""Tests for stats-derived unlock helpers (Phase 1 — no Redis needed)."""

import pytest

from fastapi_app.api.v1.endpoints.progress import (
	_is_entity_complete_from_stats,
	_is_topic_unlocked_from_stats,
	_is_unit_unlocked_from_stats,
	_stats_are_valid,
)
from fastapi_app.models.progress import (
	LessonInfo,
	SubjectHierarchy,
	TopicInfo,
	TrackInfo,
	UnitInfo,
)


# --- Test fixtures (pure Python, no Redis) ---


def _make_hierarchy(
	*,
	is_linear: bool = True,
	track_count: int = 2,
	units_per_track: int = 2,
	topics_per_unit: int = 2,
	track_linear: bool = True,
	unit_linear: bool = True,
) -> SubjectHierarchy:
	"""Build a minimal SubjectHierarchy for testing unlock logic."""
	bit = 0
	tracks = []
	for t in range(track_count):
		units = []
		for u in range(units_per_track):
			topics = []
			for tp in range(topics_per_unit):
				lessons = [
					LessonInfo(
						lesson_id=f"L-{t}-{u}-{tp}",
						bit_index=bit,
						xp=10,
						max_hearts=3,
						is_reviewable=True,
					)
				]
				bit += 1
				topics.append(
					TopicInfo(
						topic_id=f"TOPIC-{t}-{u}-{tp}",
						is_linear=True,
						lessons=lessons,
					)
				)
			units.append(
				UnitInfo(
					unit_id=f"UNIT-{t}-{u}",
					is_linear=unit_linear,
					topics=topics,
				)
			)
		tracks.append(
			TrackInfo(
				track_id=f"TRK-{t}",
				is_linear=track_linear,
				units=units,
			)
		)

	return SubjectHierarchy(
		subject_id="SUB-TEST",
		version=1,
		bit_range=bit,
		is_linear=is_linear,
		content_hash="abc12345",
		tracks=tracks,
	)


def _complete_stats(entity_id: str, total: int = 1) -> dict[str, str]:
	"""Return stats marking an entity as complete."""
	return {f"{entity_id}:completed": str(total), f"{entity_id}:total": str(total)}


def _incomplete_stats(entity_id: str, completed: int = 0, total: int = 1) -> dict[str, str]:
	"""Return stats marking an entity as incomplete."""
	return {f"{entity_id}:completed": str(completed), f"{entity_id}:total": str(total)}


# =========================================================================
# T001: _is_entity_complete_from_stats
# =========================================================================


class TestIsEntityCompleteFromStats:
	def test_complete(self) -> None:
		stats = {"TRK-0:completed": "5", "TRK-0:total": "5"}
		assert _is_entity_complete_from_stats("TRK-0", stats) is True

	def test_incomplete(self) -> None:
		stats = {"TRK-0:completed": "3", "TRK-0:total": "5"}
		assert _is_entity_complete_from_stats("TRK-0", stats) is False

	def test_empty_stats(self) -> None:
		assert _is_entity_complete_from_stats("TRK-0", {}) is False

	def test_zero_total(self) -> None:
		stats = {"TRK-0:completed": "0", "TRK-0:total": "0"}
		assert _is_entity_complete_from_stats("TRK-0", stats) is True

	def test_completed_exceeds_total(self) -> None:
		stats = {"TRK-0:completed": "6", "TRK-0:total": "5"}
		assert _is_entity_complete_from_stats("TRK-0", stats) is True

	def test_missing_completed_key(self) -> None:
		stats = {"TRK-0:total": "5"}
		assert _is_entity_complete_from_stats("TRK-0", stats) is False

	def test_missing_total_key(self) -> None:
		stats = {"TRK-0:completed": "5"}
		assert _is_entity_complete_from_stats("TRK-0", stats) is False


# =========================================================================
# T002: _is_unit_unlocked_from_stats
# =========================================================================


class TestIsUnitUnlockedFromStats:
	def test_first_unit_first_track_always_unlocked(self) -> None:
		hier = _make_hierarchy(is_linear=True)
		assert _is_unit_unlocked_from_stats(0, 0, hier, {}) is True

	def test_second_unit_linear_track_prev_complete(self) -> None:
		hier = _make_hierarchy(is_linear=True, track_linear=True)
		stats = {**_complete_stats("UNIT-0-0")}
		assert _is_unit_unlocked_from_stats(0, 1, hier, stats) is True

	def test_second_unit_linear_track_prev_incomplete(self) -> None:
		hier = _make_hierarchy(is_linear=True, track_linear=True)
		stats = {**_incomplete_stats("UNIT-0-0", 0, 2)}
		assert _is_unit_unlocked_from_stats(0, 1, hier, stats) is False

	def test_second_unit_nonlinear_track(self) -> None:
		hier = _make_hierarchy(is_linear=True, track_linear=False)
		assert _is_unit_unlocked_from_stats(0, 1, hier, {}) is True

	def test_first_unit_second_track_linear_subject_prev_complete(self) -> None:
		hier = _make_hierarchy(is_linear=True)
		stats = {**_complete_stats("TRK-0")}
		assert _is_unit_unlocked_from_stats(1, 0, hier, stats) is True

	def test_first_unit_second_track_linear_subject_prev_incomplete(self) -> None:
		hier = _make_hierarchy(is_linear=True)
		stats = {**_incomplete_stats("TRK-0", 1, 4)}
		assert _is_unit_unlocked_from_stats(1, 0, hier, stats) is False

	def test_first_unit_second_track_nonlinear_subject(self) -> None:
		hier = _make_hierarchy(is_linear=False)
		assert _is_unit_unlocked_from_stats(1, 0, hier, {}) is True


# =========================================================================
# T003: _is_topic_unlocked_from_stats
# =========================================================================


class TestIsTopicUnlockedFromStats:
	def test_first_topic_always_unlocked_if_unit_unlocked(self) -> None:
		hier = _make_hierarchy(is_linear=True)
		assert _is_topic_unlocked_from_stats(0, 0, 0, hier, {}) is True

	def test_second_topic_linear_unit_prev_complete(self) -> None:
		hier = _make_hierarchy(is_linear=True, unit_linear=True)
		stats = {**_complete_stats("TOPIC-0-0-0")}
		assert _is_topic_unlocked_from_stats(0, 0, 1, hier, stats) is True

	def test_second_topic_linear_unit_prev_incomplete(self) -> None:
		hier = _make_hierarchy(is_linear=True, unit_linear=True)
		stats = {**_incomplete_stats("TOPIC-0-0-0", 0, 1)}
		assert _is_topic_unlocked_from_stats(0, 0, 1, hier, stats) is False

	def test_second_topic_nonlinear_unit(self) -> None:
		hier = _make_hierarchy(is_linear=True, unit_linear=False)
		assert _is_topic_unlocked_from_stats(0, 0, 1, hier, {}) is True

	def test_topic_locked_if_unit_locked(self) -> None:
		"""Topic in second unit of linear track is locked if first unit incomplete."""
		hier = _make_hierarchy(is_linear=True, track_linear=True)
		stats = {**_incomplete_stats("UNIT-0-0", 0, 2)}
		assert _is_topic_unlocked_from_stats(0, 1, 0, hier, stats) is False

	def test_chained_unlock(self) -> None:
		"""Second topic in second unit requires: unit unlocked + prev topic complete."""
		hier = _make_hierarchy(is_linear=True, track_linear=True, unit_linear=True)
		stats = {
			**_complete_stats("UNIT-0-0"),
			**_complete_stats("TOPIC-0-1-0"),
		}
		assert _is_topic_unlocked_from_stats(0, 1, 1, hier, stats) is True


# =========================================================================
# T004: _stats_are_valid
# =========================================================================


class TestStatsAreValid:
	def test_valid_stats(self) -> None:
		stats = {"total": "10", "completed": "5", "_content_hash": "abc12345"}
		assert _stats_are_valid(stats, "abc12345") is True

	def test_none_stats(self) -> None:
		assert _stats_are_valid(None, "abc12345") is False

	def test_missing_total(self) -> None:
		stats = {"completed": "5", "_content_hash": "abc12345"}
		assert _stats_are_valid(stats, "abc12345") is False

	def test_mismatching_hash(self) -> None:
		stats = {"total": "10", "completed": "5", "_content_hash": "old_hash"}
		assert _stats_are_valid(stats, "new_hash") is False

	def test_missing_content_hash(self) -> None:
		stats = {"total": "10", "completed": "5"}
		assert _stats_are_valid(stats, "abc12345") is False

	def test_empty_content_hash(self) -> None:
		"""Pre-migration stats with empty content_hash should only match empty hierarchy hash."""
		stats = {"total": "10", "_content_hash": ""}
		assert _stats_are_valid(stats, "") is True
		assert _stats_are_valid(stats, "abc12345") is False
