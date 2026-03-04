"""Unit tests for _compute_content_hash() algorithm.

The algorithm lives in memora_admin/api/hierarchy.py (Frappe-land) and
cannot be imported in the FastAPI pytest environment. The function is a
pure stdlib function with zero Frappe dependencies, so we inline the
same algorithm here to verify its properties: determinism, structural
sensitivity, and stability on non-structural changes.

If you move the function to a shared utility importable from FastAPI-land,
replace the local definition with the import.
"""

import hashlib


def _compute_content_hash(hierarchy: dict) -> str:
	"""Same algorithm as memora_admin/api/hierarchy.py:_compute_content_hash().

	Hashes structural fields that affect stats totals. Pure function.
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


# ---------------------------------------------------------------------------
# Helpers: minimal hierarchy dicts for testing
# ---------------------------------------------------------------------------


def _make_hierarchy(
	bit_range: int = 10,
	excluded_bits: list | None = None,
	tracks: list | None = None,
) -> dict:
	"""Build a minimal hierarchy dict matching get_subject_hierarchy() output."""
	return {
		"subject_id": "SUBJ-TEST-001",
		"version": 1,
		"bit_range": bit_range,
		"excluded_bits": excluded_bits or [],
		"is_linear": True,
		"free_units": [],
		"free_topics": [],
		"tracks": tracks or [],
	}


def _make_lesson(lesson_id: str, bit_index: int, xp: int = 100, max_hearts: int = 5) -> dict:
	return {"lesson_id": lesson_id, "bit_index": bit_index, "xp": xp, "max_hearts": max_hearts}


def _make_topic(topic_id: str, lessons: list, is_free: bool = False, is_linear: bool = True) -> dict:
	return {"topic_id": topic_id, "is_linear": is_linear, "is_free": is_free, "lessons": lessons}


def _make_unit(unit_id: str, topics: list, is_free: bool = False, is_linear: bool = True) -> dict:
	return {"unit_id": unit_id, "is_linear": is_linear, "is_free": is_free, "topics": topics}


def _make_track(track_id: str, units: list, is_linear: bool = True, is_sold_separately: bool = False) -> dict:
	return {
		"track_id": track_id,
		"is_linear": is_linear,
		"is_sold_separately": is_sold_separately,
		"units": units,
	}


def _simple_hierarchy() -> dict:
	"""A simple 1-track, 1-unit, 1-topic, 2-lesson hierarchy for baseline tests."""
	return _make_hierarchy(
		bit_range=2,
		tracks=[
			_make_track(
				"TRK-001",
				units=[
					_make_unit(
						"UNT-001",
						topics=[
							_make_topic(
								"TPC-001",
								lessons=[
									_make_lesson("LSN-001", bit_index=0),
									_make_lesson("LSN-002", bit_index=1),
								],
							)
						],
					)
				],
			)
		],
	)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
	"""Same input always produces same hash."""

	def test_same_hierarchy_same_hash(self):
		"""Hash is deterministic — calling twice returns the same value."""
		h1 = _compute_content_hash(_simple_hierarchy())
		h2 = _compute_content_hash(_simple_hierarchy())
		assert h1 == h2

	def test_returns_8_hex_chars(self):
		"""Output is exactly 8 hex characters."""
		result = _compute_content_hash(_simple_hierarchy())
		assert len(result) == 8
		assert all(c in "0123456789abcdef" for c in result)

	def test_empty_hierarchy_deterministic(self):
		"""Empty hierarchy (no tracks) produces consistent hash."""
		empty = _make_hierarchy(bit_range=0, tracks=[])
		h1 = _compute_content_hash(empty)
		h2 = _compute_content_hash(empty)
		assert h1 == h2

	def test_different_bit_range_different_hash(self):
		"""Same structure but different bit_range → different hash."""
		h1 = _compute_content_hash(_simple_hierarchy())
		modified = _simple_hierarchy()
		modified["bit_range"] = 99
		h2 = _compute_content_hash(modified)
		assert h1 != h2


# ---------------------------------------------------------------------------
# Structural sensitivity — adding/removing/reordering lessons changes hash
# ---------------------------------------------------------------------------


class TestStructuralSensitivity:
	"""Hash changes when structure affecting stats totals changes."""

	def test_adding_lesson_changes_hash(self):
		"""Adding a lesson to a topic changes the hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["topics"][0]["lessons"].append(_make_lesson("LSN-003", bit_index=2))
		h_after = _compute_content_hash(modified)
		assert h_before != h_after

	def test_removing_lesson_changes_hash(self):
		"""Removing a lesson from a topic changes the hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["topics"][0]["lessons"].pop()
		h_after = _compute_content_hash(modified)
		assert h_before != h_after

	def test_lesson_bit_index_change_changes_hash(self):
		"""Changing a lesson's bit_index changes the hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["topics"][0]["lessons"][0]["bit_index"] = 99
		h_after = _compute_content_hash(modified)
		assert h_before != h_after

	def test_lesson_id_change_changes_hash(self):
		"""Changing a lesson_id changes the hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["topics"][0]["lessons"][0]["lesson_id"] = "LSN-RENAMED"
		h_after = _compute_content_hash(modified)
		assert h_before != h_after

	def test_topic_id_change_changes_hash(self):
		"""Changing a topic_id changes the hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["topics"][0]["topic_id"] = "TPC-RENAMED"
		h_after = _compute_content_hash(modified)
		assert h_before != h_after

	def test_unit_id_change_changes_hash(self):
		"""Changing a unit_id changes the hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["unit_id"] = "UNT-RENAMED"
		h_after = _compute_content_hash(modified)
		assert h_before != h_after

	def test_track_id_change_changes_hash(self):
		"""Changing a track_id changes the hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["track_id"] = "TRK-RENAMED"
		h_after = _compute_content_hash(modified)
		assert h_before != h_after

	def test_adding_excluded_bit_changes_hash(self):
		"""Adding an excluded_bit changes the hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["excluded_bits"] = [5]
		h_after = _compute_content_hash(modified)
		assert h_before != h_after

	def test_excluded_bits_order_independent(self):
		"""excluded_bits are sorted before hashing — order doesn't matter."""
		h_sorted = _compute_content_hash(_make_hierarchy(excluded_bits=[1, 3, 5]))
		h_unsorted = _compute_content_hash(_make_hierarchy(excluded_bits=[5, 1, 3]))
		assert h_sorted == h_unsorted


# ---------------------------------------------------------------------------
# Stability — non-structural changes do NOT change the hash
# ---------------------------------------------------------------------------


class TestStability:
	"""Irrelevant fields (is_linear, xp, is_free, max_hearts) don't change hash."""

	def test_is_linear_change_no_hash_change(self):
		"""Changing is_linear on subject/track/unit/topic does not change hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["is_linear"] = not modified["is_linear"]
		modified["tracks"][0]["is_linear"] = False
		modified["tracks"][0]["units"][0]["is_linear"] = False
		modified["tracks"][0]["units"][0]["topics"][0]["is_linear"] = False
		h_after = _compute_content_hash(modified)
		assert h_before == h_after

	def test_xp_change_no_hash_change(self):
		"""Changing a lesson's XP does not change hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["topics"][0]["lessons"][0]["xp"] = 9999
		h_after = _compute_content_hash(modified)
		assert h_before == h_after

	def test_max_hearts_change_no_hash_change(self):
		"""Changing max_hearts does not change hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["topics"][0]["lessons"][0]["max_hearts"] = 99
		h_after = _compute_content_hash(modified)
		assert h_before == h_after

	def test_is_free_change_no_hash_change(self):
		"""Changing is_free on unit/topic does not change hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["units"][0]["is_free"] = True
		modified["tracks"][0]["units"][0]["topics"][0]["is_free"] = True
		h_after = _compute_content_hash(modified)
		assert h_before == h_after

	def test_free_units_and_free_topics_no_hash_change(self):
		"""Changing free_units/free_topics at the hierarchy level does not change hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["free_units"] = ["UNT-001"]
		modified["free_topics"] = ["TPC-001"]
		h_after = _compute_content_hash(modified)
		assert h_before == h_after

	def test_is_sold_separately_no_hash_change(self):
		"""Changing is_sold_separately on a track does not change hash."""
		original = _simple_hierarchy()
		h_before = _compute_content_hash(original)

		modified = _simple_hierarchy()
		modified["tracks"][0]["is_sold_separately"] = True
		h_after = _compute_content_hash(modified)
		assert h_before == h_after
