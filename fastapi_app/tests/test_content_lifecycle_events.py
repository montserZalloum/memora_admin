"""Unit tests for content lifecycle event handlers.

Tests what happens when admin users publish/unpublish/delete content in the
full academic hierarchy:

  Memora Academic Plan → Plan Subject → Plan Overrider
  Memora Subject → Track → Unit → Topic → Lesson

Coverage:
  build_trigger.py:
    - on_content_updated (Subject, Track, Unit, Topic, Lesson)
    - on_plan_updated (Academic Plan)
    - on_plan_subject_changed (Plan Subject add/modify/delete)
    - _has_is_premium_changed helper
    - _get_subject_id helper (hierarchy traversal)
    - _invalidate_hierarchy_cache helper
    - _invalidate_catalog_cache helper
    - Debounce pattern (120s per-plan deduplication)

  access_sync.py:
    - on_season_updated / on_season_deleted
    - on_subscription_change / on_subscription_deleted
    - on_plan_subject_changed (is_premium flag changes)
    - on_unit_free_changed / on_topic_free_changed

Strategy:
  All Frappe ORM/cache calls and Redis connections are mocked via unittest.mock.
  Tests run in the standard FastAPI pytest environment — no Frappe DB required.

Run:
  pytest fastapi_app/tests/test_content_lifecycle_events.py -v
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from fastapi_app.core.redis_keys import (
	ACCESS_KEY_TTL,
	PLAN_FREE_SUBJECTS_TTL,
	access_key,
	build_debounce_key,
	cache_invalidation_channel,
	catalog_key,
	hierarchy_key,
	plan_free_subjects_key,
	plan_manifest_key,
	plan_season_seq_key,
	season_key,
	subjects_with_free_content_key,
)
from memora_admin.events import access_sync, build_trigger


# =============================================================================
# Doc factories — minimal SimpleNamespace objects mirroring real DocTypes
# =============================================================================


def _subject(name="SUBJ-001"):
	return SimpleNamespace(doctype="Memora Subject", name=name)


def _track(name="TRK-001", subject="SUBJ-001"):
	return SimpleNamespace(doctype="Memora Track", name=name, subject=subject)


def _unit(name="UNIT-001", track="TRK-001", is_free=0):
	return SimpleNamespace(doctype="Memora Unit", name=name, track=track, is_free=is_free)


def _topic(name="TOP-001", unit="UNIT-001", is_free=0):
	return SimpleNamespace(doctype="Memora Topic", name=name, unit=unit, is_free=is_free)


def _lesson(name="LES-001", subject="SUBJ-001"):
	return SimpleNamespace(doctype="Memora Lesson", name=name, subject=subject)


def _plan_doc(name="PLAN-001", season_changed=False, subjects_old=None, subjects_new=None):
	"""Mock plan doc with configurable plan_subjects and change detection."""
	old_doc = MagicMock()
	old_doc.plan_subjects = subjects_old or []
	doc = MagicMock()
	doc.name = name
	doc.plan_subjects = subjects_new or []
	doc.has_value_changed.return_value = season_changed
	doc.get_doc_before_save.return_value = old_doc
	return doc


def _plan_subject_doc(parent="PLAN-001", subject="SUBJ-001", is_premium=0):
	return SimpleNamespace(
		doctype="Memora Plan Subject",
		parent=parent,
		subject=subject,
		is_premium=is_premium,
	)


def _season(name="SEAS-001", is_published=1, start_date="2025-01-01", end_date="2026-01-01", season_seq=1):
	return SimpleNamespace(
		name=name,
		is_published=is_published,
		start_date=start_date,
		end_date=end_date,
		season_seq=season_seq,
	)


def _subscription(player="PLAYER-00001", access_key_val="SUB-MATH", is_active=1):
	return SimpleNamespace(player=player, access_key=access_key_val, is_active=is_active)


def _setup_build_trigger_frappe(mock_frappe, plan_ids=("PLAN-001",), debounce_hits=True):
	"""Configure frappe mock for build_trigger tests."""
	mock_frappe.get_all.return_value = [{"parent": p} for p in plan_ids]
	mock_frappe.cache = MagicMock()
	mock_frappe.cache.set.return_value = True if debounce_hits else None
	mock_frappe.session.user = "Administrator"
	mock_frappe.logger.return_value = MagicMock()
	mock_frappe.utils.now.return_value = "2026-01-01 00:00:00"
	build_queue_doc = MagicMock()
	mock_frappe.get_doc.return_value = build_queue_doc
	return build_queue_doc


# =============================================================================
# _get_subject_id — hierarchy traversal helper
# =============================================================================


class TestGetSubjectId:
	"""_get_subject_id resolves the Subject ID from any content DocType."""

	def test_subject_returns_doc_name(self):
		"""TC-GS-01: Memora Subject — returns doc.name directly."""
		assert build_trigger._get_subject_id(_subject("SUBJ-999")) == "SUBJ-999"

	def test_track_returns_doc_subject_field(self):
		"""TC-GS-02: Memora Track — uses doc.subject (no ORM needed)."""
		result = build_trigger._get_subject_id(_track("TRK-001", subject="SUBJ-002"))
		assert result == "SUBJ-002"

	@patch("memora_admin.events.build_trigger.frappe")
	def test_unit_resolves_via_track_get_cached_value(self, mock_frappe):
		"""TC-GS-03: Memora Unit — Unit.track → frappe.get_cached_value → subject."""
		mock_frappe.get_cached_value.return_value = "SUBJ-003"
		result = build_trigger._get_subject_id(_unit("UNIT-001", track="TRK-001"))
		assert result == "SUBJ-003"
		mock_frappe.get_cached_value.assert_called_once_with("Memora Track", "TRK-001", "subject")

	@patch("memora_admin.events.build_trigger.frappe")
	def test_unit_with_no_track_returns_none(self, mock_frappe):
		"""TC-GS-04: Memora Unit with no track link — returns None safely."""
		doc = SimpleNamespace(doctype="Memora Unit", name="UNIT-001", track=None)
		assert build_trigger._get_subject_id(doc) is None

	@patch("memora_admin.events.build_trigger.frappe")
	def test_topic_resolves_via_two_get_cached_value_calls(self, mock_frappe):
		"""TC-GS-05: Memora Topic — two hops: Topic.unit → track, track → subject."""
		mock_frappe.get_cached_value.side_effect = ["TRK-001", "SUBJ-004"]
		result = build_trigger._get_subject_id(_topic("TOP-001", unit="UNIT-001"))
		assert result == "SUBJ-004"
		assert mock_frappe.get_cached_value.call_count == 2

	def test_lesson_returns_doc_subject_field(self):
		"""TC-GS-06: Memora Lesson — uses doc.subject directly (no ORM needed)."""
		assert build_trigger._get_subject_id(_lesson("LES-001", subject="SUBJ-005")) == "SUBJ-005"

	def test_unknown_doctype_returns_none(self):
		"""TC-GS-07: Unknown DocType — returns None (defensive)."""
		doc = SimpleNamespace(doctype="Memora Other", name="OTHER-001")
		assert build_trigger._get_subject_id(doc) is None


# =============================================================================
# _has_is_premium_changed — plan subject diff helper
# =============================================================================


class TestHasIsPremiumChanged:
	"""_has_is_premium_changed detects is_premium flag changes across plan_subjects rows."""

	def _row(self, subject, is_premium):
		return SimpleNamespace(subject=subject, is_premium=is_premium)

	def test_no_old_doc_treated_as_changed(self):
		"""TC-HPC-01: New plan doc (old_doc=None) → treated as changed (trigger rebuild)."""
		doc = _plan_doc()
		assert build_trigger._has_is_premium_changed(None, doc) is True

	def test_identical_maps_not_changed(self):
		"""TC-HPC-02: Identical is_premium values → no change."""
		old = MagicMock(plan_subjects=[self._row("SUBJ-001", 0)])
		new = _plan_doc(subjects_new=[self._row("SUBJ-001", 0)])
		assert build_trigger._has_is_premium_changed(old, new) is False

	def test_subject_premium_flip_detected(self):
		"""TC-HPC-03: is_premium 0→1 for a subject → change detected."""
		old = MagicMock(plan_subjects=[self._row("SUBJ-001", 0)])
		new = _plan_doc(subjects_new=[self._row("SUBJ-001", 1)])
		assert build_trigger._has_is_premium_changed(old, new) is True

	def test_new_subject_added_detected(self):
		"""TC-HPC-04: New subject added to plan → change detected."""
		old = MagicMock(plan_subjects=[self._row("SUBJ-001", 0)])
		new = _plan_doc(subjects_new=[self._row("SUBJ-001", 0), self._row("SUBJ-002", 0)])
		assert build_trigger._has_is_premium_changed(old, new) is True

	def test_subject_removed_detected(self):
		"""TC-HPC-05: Subject removed from plan → change detected."""
		old = MagicMock(plan_subjects=[self._row("SUBJ-001", 0), self._row("SUBJ-002", 0)])
		new = _plan_doc(subjects_new=[self._row("SUBJ-001", 0)])
		assert build_trigger._has_is_premium_changed(old, new) is True


# =============================================================================
# _invalidate_hierarchy_cache — Redis DEL + pubsub
# =============================================================================


class TestInvalidateHierarchyCache:
	"""_invalidate_hierarchy_cache deletes the key and notifies FastAPI workers."""

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_deletes_correct_redis_key(self, mock_get_redis, mock_frappe):
		"""TC-IHC-01: DEL memora:hierarchy:{subject_id}."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		mock_frappe.utils.now.return_value = "2026-01-01"

		build_trigger._invalidate_hierarchy_cache("SUBJ-001")

		mock_redis.delete.assert_called_once_with(hierarchy_key("SUBJ-001"))

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_publishes_hierarchy_invalidation_event(self, mock_get_redis, mock_frappe):
		"""TC-IHC-02: Publishes {"type": "hierarchy", "subject_id": ...} to cache channel."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		mock_frappe.utils.now.return_value = "2026-01-01"

		build_trigger._invalidate_hierarchy_cache("SUBJ-001")

		mock_redis.publish.assert_called_once()
		channel, payload = mock_redis.publish.call_args[0]
		assert channel == cache_invalidation_channel()
		data = json.loads(payload)
		assert data["type"] == "hierarchy"
		assert data["subject_id"] == "SUBJ-001"

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_redis_error_is_logged_and_swallowed(self, mock_get_redis, mock_frappe):
		"""TC-IHC-03: Redis failure logs error without raising (best-effort)."""
		mock_get_redis.side_effect = Exception("Redis down")

		build_trigger._invalidate_hierarchy_cache("SUBJ-001")  # must not raise

		mock_frappe.log_error.assert_called_once()


# =============================================================================
# _invalidate_catalog_cache — Redis DEL + pubsub
# =============================================================================


class TestInvalidateCatalogCache:
	"""_invalidate_catalog_cache deletes the plan's catalog key and notifies workers."""

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_deletes_correct_redis_key(self, mock_get_redis, mock_frappe):
		"""TC-ICC-01: DEL memora:catalog:{plan_id}."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		mock_frappe.utils.now.return_value = "2026-01-01"

		build_trigger._invalidate_catalog_cache("PLAN-001")

		mock_redis.delete.assert_called_once_with(catalog_key("PLAN-001"))

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_publishes_catalog_invalidation_event(self, mock_get_redis, mock_frappe):
		"""TC-ICC-02: Publishes {"type": "catalog", "plan_id": ...} to cache channel."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		mock_frappe.utils.now.return_value = "2026-01-01"

		build_trigger._invalidate_catalog_cache("PLAN-001")

		mock_redis.publish.assert_called_once()
		channel, payload = mock_redis.publish.call_args[0]
		assert channel == cache_invalidation_channel()
		data = json.loads(payload)
		assert data["type"] == "catalog"
		assert data["plan_id"] == "PLAN-001"

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_redis_error_is_logged_and_swallowed(self, mock_get_redis, mock_frappe):
		"""TC-ICC-03: Redis failure logs error without raising."""
		mock_get_redis.side_effect = Exception("Redis down")

		build_trigger._invalidate_catalog_cache("PLAN-001")  # must not raise

		mock_frappe.log_error.assert_called_once()


# =============================================================================
# on_content_updated — Subject / Track / Unit / Topic / Lesson lifecycle
# =============================================================================


class TestOnContentUpdated:
	"""on_content_updated invalidates hierarchy cache and queues plan builds."""

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_subject_update_invalidates_hierarchy_cache(self, mock_get_redis, mock_frappe):
		"""TC-OCU-01: Memora Subject update → hierarchy cache for that subject deleted."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		mock_redis.delete.assert_called_with(hierarchy_key("SUBJ-001"))

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_subject_trash_invalidates_hierarchy_cache(self, mock_get_redis, mock_frappe):
		"""TC-OCU-02: Memora Subject delete → hierarchy cache invalidated."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_trash")

		mock_redis.delete.assert_called_with(hierarchy_key("SUBJ-001"))

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_track_update_resolves_subject_from_doc_field(self, mock_get_redis, mock_frappe):
		"""TC-OCU-03: Memora Track update → resolves subject via doc.subject."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_track("TRK-001", subject="SUBJ-001"), "on_update")

		mock_redis.delete.assert_called_with(hierarchy_key("SUBJ-001"))

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_unit_update_resolves_subject_via_track(self, mock_get_redis, mock_frappe):
		"""TC-OCU-04: Memora Unit update → Unit.track → get_cached_value → subject."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		mock_frappe.get_cached_value.return_value = "SUBJ-001"
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_unit("UNIT-001", track="TRK-001"), "on_update")

		mock_redis.delete.assert_called_with(hierarchy_key("SUBJ-001"))

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_topic_update_resolves_subject_via_unit_and_track(self, mock_get_redis, mock_frappe):
		"""TC-OCU-05: Memora Topic update → two hops through ORM to reach subject."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		# get_cached_value called twice: first for unit→track, then track→subject
		mock_frappe.get_cached_value.side_effect = ["TRK-001", "SUBJ-001"]
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_topic("TOP-001", unit="UNIT-001"), "on_update")

		mock_redis.delete.assert_called_with(hierarchy_key("SUBJ-001"))

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_lesson_update_resolves_subject_from_doc_field(self, mock_get_redis, mock_frappe):
		"""TC-OCU-06: Memora Lesson update → uses doc.subject directly."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_lesson("LES-001", subject="SUBJ-001"), "on_update")

		mock_redis.delete.assert_called_with(hierarchy_key("SUBJ-001"))

	@patch("memora_admin.events.build_trigger._delete_lesson_json")
	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_lesson_trash_deletes_lesson_json_file(self, mock_get_redis, mock_frappe, mock_delete_json):
		"""TC-OCU-07: Memora Lesson on_trash → orphaned JSON file is deleted."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_lesson("LES-001", subject="SUBJ-001"), "on_trash")

		mock_delete_json.assert_called_once_with("LES-001")

	@patch("memora_admin.events.build_trigger._delete_lesson_json")
	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_non_lesson_trash_does_not_delete_json(self, mock_get_redis, mock_frappe, mock_delete_json):
		"""TC-OCU-08: Non-lesson on_trash → _delete_lesson_json NOT called."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_trash")

		mock_delete_json.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_subject_not_resolved_logs_error_and_stops(self, mock_get_redis, mock_frappe):
		"""TC-OCU-09: Unresolvable subject → log_error called, no cache/build ops."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		# Unit with no track → _get_subject_id returns None
		doc = SimpleNamespace(doctype="Memora Unit", name="UNIT-001", track=None)

		build_trigger.on_content_updated(doc, "on_update")

		mock_frappe.log_error.assert_called_once()
		mock_frappe.get_all.assert_not_called()
		mock_redis.delete.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_queues_build_for_each_plan_containing_subject(self, mock_get_redis, mock_frappe):
		"""TC-OCU-10: Multiple plans contain the subject → one build queue per plan."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe, plan_ids=["PLAN-001", "PLAN-002"])

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		assert mock_frappe.get_doc.call_count == 2

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_no_plans_for_subject_skips_build_queue(self, mock_get_redis, mock_frappe):
		"""TC-OCU-11: No plans reference the subject → no build queue created."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe, plan_ids=[])

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		mock_frappe.get_doc.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_build_queue_trigger_reason_is_content_update(self, mock_get_redis, mock_frappe):
		"""TC-OCU-12: Build queue entry has trigger_reason='content_update'."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		call_kwargs = mock_frappe.get_doc.call_args[0][0]
		assert call_kwargs["trigger_reason"] == "content_update"
		assert call_kwargs["target_name"] == "PLAN-001"

	@patch("memora_admin.events.build_trigger._remove_subject_from_plan_free_subjects")
	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_subject_trash_calls_remove_from_free_subjects(
		self, mock_get_redis, mock_frappe, mock_remove
	):
		"""TC-OCU-13: Subject on_trash → _remove_subject_from_plan_free_subjects called."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_trash")

		mock_remove.assert_called_once_with("SUBJ-001")

	@patch("memora_admin.events.build_trigger._remove_subject_from_plan_free_subjects")
	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_subject_update_does_not_remove_from_free_subjects(
		self, mock_get_redis, mock_frappe, mock_remove
	):
		"""TC-OCU-14: Subject on_update → _remove_subject_from_plan_free_subjects NOT called."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		mock_remove.assert_not_called()

	@patch("memora_admin.events.build_trigger._cascade_delete_plan_subjects")
	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_subject_trash_calls_cascade_delete_after_builds_queued(
		self, mock_get_redis, mock_frappe, mock_cascade
	):
		"""TC-OCU-15: Subject on_trash → _cascade_delete_plan_subjects called after build queue."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)
		call_order = []
		mock_frappe.get_doc.side_effect = lambda *a, **kw: (
			call_order.append("get_doc") or MagicMock()
		)
		mock_cascade.side_effect = lambda *a: call_order.append("cascade")

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_trash")

		assert call_order.index("get_doc") < call_order.index("cascade")

	@patch("memora_admin.events.build_trigger._cascade_delete_plan_subjects")
	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_subject_update_does_not_cascade_delete(self, mock_get_redis, mock_frappe, mock_cascade):
		"""TC-OCU-16: Subject on_update → _cascade_delete_plan_subjects NOT called."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		mock_cascade.assert_not_called()


# =============================================================================
# _cascade_delete_plan_subjects — orphaned Plan Subject row cleanup
# =============================================================================


class TestCascadeDeletePlanSubjects:
	"""_cascade_delete_plan_subjects removes orphaned Plan Subject rows on Subject deletion."""

	@patch("memora_admin.events.build_trigger.frappe")
	def test_deletes_plan_subject_rows_for_subject(self, mock_frappe):
		"""TC-CDS-01: Rows exist → frappe.db.delete called with correct filters."""
		mock_frappe.db.count.return_value = 2
		mock_frappe.logger.return_value = MagicMock()

		build_trigger._cascade_delete_plan_subjects("SUBJ-001")

		mock_frappe.db.delete.assert_called_once_with(
			"Memora Plan Subject", {"subject": "SUBJ-001"}
		)

	@patch("memora_admin.events.build_trigger.frappe")
	def test_no_rows_skips_delete(self, mock_frappe):
		"""TC-CDS-02: No matching rows → db.delete NOT called (early return)."""
		mock_frappe.db.count.return_value = 0

		build_trigger._cascade_delete_plan_subjects("SUBJ-001")

		mock_frappe.db.delete.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	def test_db_error_is_logged_and_swallowed(self, mock_frappe):
		"""TC-CDS-03: DB failure → error logged, no exception raised."""
		mock_frappe.db.count.side_effect = Exception("DB down")

		build_trigger._cascade_delete_plan_subjects("SUBJ-001")  # must not raise

		mock_frappe.log_error.assert_called_once()


# =============================================================================
# _cancel_pending_builds — stop builds for a deleted plan
# =============================================================================


class TestCancelPendingBuilds:
	"""_cancel_pending_builds marks Pending/Processing builds as Failed on plan deletion."""

	@patch("memora_admin.events.build_trigger.frappe")
	def test_marks_pending_builds_as_failed(self, mock_frappe):
		"""TC-CPB-01: Pending builds found → set_value called with status=Failed."""
		mock_frappe.get_all.return_value = [{"name": "BLD-00001"}, {"name": "BLD-00002"}]
		mock_frappe.logger.return_value = MagicMock()

		build_trigger._cancel_pending_builds("PLAN-001")

		assert mock_frappe.db.set_value.call_count == 2
		for c in mock_frappe.db.set_value.call_args_list:
			_, values = c[0][0], c[0][2]
			assert values["status"] == "Failed"
			assert "PLAN-001" in values["error_message"]

	@patch("memora_admin.events.build_trigger.frappe")
	def test_queries_pending_and_processing_statuses(self, mock_frappe):
		"""TC-CPB-02: Query filters for both Pending and Processing builds."""
		mock_frappe.get_all.return_value = []

		build_trigger._cancel_pending_builds("PLAN-001")

		_, kwargs = mock_frappe.get_all.call_args
		statuses = kwargs["filters"]["status"]
		assert "Pending" in statuses[1]
		assert "Processing" in statuses[1]

	@patch("memora_admin.events.build_trigger.frappe")
	def test_no_pending_builds_skips_set_value(self, mock_frappe):
		"""TC-CPB-03: No pending builds → db.set_value NOT called."""
		mock_frappe.get_all.return_value = []

		build_trigger._cancel_pending_builds("PLAN-001")

		mock_frappe.db.set_value.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	def test_db_error_is_logged_and_swallowed(self, mock_frappe):
		"""TC-CPB-04: DB failure → error logged, no exception raised."""
		mock_frappe.get_all.side_effect = Exception("DB down")

		build_trigger._cancel_pending_builds("PLAN-001")  # must not raise

		mock_frappe.log_error.assert_called_once()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")
	@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")
	def test_on_plan_deleted_calls_cancel_before_directory_delete(
		self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe
	):
		"""TC-CPB-05: on_plan_deleted → cancel runs before storage delete."""
		mock_storage = MagicMock()
		mock_storage.list_directory.return_value = []
		mock_get_storage.return_value = mock_storage
		mock_get_redis.return_value = MagicMock()
		mock_frappe.logger.return_value = MagicMock()
		call_order = []
		mock_frappe.get_all.side_effect = lambda *a, **kw: (
			call_order.append("cancel") or []
		)
		mock_storage.delete_directory.side_effect = lambda *a: (
			call_order.append("storage_delete") or True
		)

		build_trigger.on_plan_deleted(
			SimpleNamespace(doctype="Memora Academic Plan", name="PLAN-001"), "on_trash"
		)

		assert call_order.index("cancel") < call_order.index("storage_delete")


# =============================================================================
# Debounce pattern — 120-second per-plan deduplication
# =============================================================================


class TestDebounce:
	"""Build queue creation is debounced (120s per plan) to prevent build storms."""

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_first_update_creates_build_queue_entry(self, mock_get_redis, mock_frappe):
		"""TC-DEB-01: SET NX EX succeeds → build queue entry is created."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe, debounce_hits=True)

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		mock_frappe.get_doc.assert_called_once()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_second_update_within_window_skipped(self, mock_get_redis, mock_frappe):
		"""TC-DEB-02: SET NX EX returns None (key exists) → no duplicate build queue."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe, debounce_hits=False)  # None = key already set

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		mock_frappe.get_doc.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_debounce_key_cleared_on_queue_insert_failure(self, mock_get_redis, mock_frappe):
		"""TC-DEB-03: Build queue insert fails → debounce key removed so next update retries."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe, debounce_hits=True)
		# Make the queue insert fail
		mock_frappe.get_doc.return_value.insert.side_effect = Exception("DB error")

		build_trigger.on_content_updated(_subject("SUBJ-001"), "on_update")

		# debounce key must be cleaned up
		mock_frappe.cache.delete_value.assert_called_once()


# =============================================================================
# on_plan_updated — Academic Plan publish/change
# =============================================================================


class TestOnPlanUpdated:
	"""on_plan_updated always invalidates catalog cache and queues a plan rebuild."""

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_always_invalidates_catalog_cache(self, mock_get_redis, mock_frappe):
		"""TC-OPU-01: Any plan update → catalog cache deleted immediately."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_plan_updated(_plan_doc("PLAN-001"), "on_update")

		deleted_keys = [c[0][0] for c in mock_redis.delete.call_args_list]
		assert catalog_key("PLAN-001") in deleted_keys

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_queues_build_with_trigger_reason_plan_update(self, mock_get_redis, mock_frappe):
		"""TC-OPU-02: Build queue entry has trigger_reason='plan_update'."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_plan_updated(_plan_doc("PLAN-001"), "on_update")

		mock_frappe.get_doc.assert_called_once()
		call_kwargs = mock_frappe.get_doc.call_args[0][0]
		assert call_kwargs["trigger_reason"] == "plan_update"

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_season_change_invalidates_plan_season_seq_key(self, mock_get_redis, mock_frappe):
		"""TC-OPU-03: Plan season field changed → plan_season_seq key deleted."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_plan_updated(_plan_doc("PLAN-001", season_changed=True), "on_update")

		deleted_keys = [c[0][0] for c in mock_redis.delete.call_args_list]
		assert plan_season_seq_key("PLAN-001") in deleted_keys

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_no_season_change_does_not_delete_plan_season_seq(self, mock_get_redis, mock_frappe):
		"""TC-OPU-04: No season field change → plan_season_seq key NOT touched."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_plan_updated(_plan_doc("PLAN-001", season_changed=False), "on_update")

		deleted_keys = [c[0][0] for c in mock_redis.delete.call_args_list]
		assert plan_season_seq_key("PLAN-001") not in deleted_keys

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_is_premium_change_rebuilds_free_subjects(self, mock_get_redis, mock_frappe):
		"""TC-OPU-05: is_premium flipped on a subject → rebuild_plan_free_subjects called."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		old_row = SimpleNamespace(subject="SUBJ-001", is_premium=0)
		new_row = SimpleNamespace(subject="SUBJ-001", is_premium=1)
		doc = _plan_doc("PLAN-001", subjects_old=[old_row], subjects_new=[new_row])

		# Lazy import: patch at the source module, not build_trigger
		with patch("memora_admin.events.access_sync.rebuild_plan_free_subjects") as mock_rebuild:
			build_trigger.on_plan_updated(doc, "on_update")

		mock_rebuild.assert_called_once_with("PLAN-001")

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_no_is_premium_change_skips_free_subjects_rebuild(self, mock_get_redis, mock_frappe):
		"""TC-OPU-06: is_premium unchanged → rebuild_plan_free_subjects NOT called."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		row = SimpleNamespace(subject="SUBJ-001", is_premium=0)
		doc = _plan_doc("PLAN-001", subjects_old=[row], subjects_new=[row])

		# Lazy import: patch at the source module
		with patch("memora_admin.events.access_sync.rebuild_plan_free_subjects") as mock_rebuild:
			build_trigger.on_plan_updated(doc, "on_update")

		mock_rebuild.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_is_premium_change_publishes_plan_subjects_event(self, mock_get_redis, mock_frappe):
		"""TC-OPU-07: is_premium changed → pubsub plan_subjects event published."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		old_row = SimpleNamespace(subject="SUBJ-001", is_premium=0)
		new_row = SimpleNamespace(subject="SUBJ-001", is_premium=1)
		doc = _plan_doc("PLAN-001", subjects_old=[old_row], subjects_new=[new_row])

		# Lazy import: patch at the source module
		with patch("memora_admin.events.access_sync.rebuild_plan_free_subjects"):
			build_trigger.on_plan_updated(doc, "on_update")

		published = [json.loads(c[0][1]) for c in mock_redis.publish.call_args_list]
		plan_events = [e for e in published if e.get("type") == "plan_subjects"]
		assert len(plan_events) == 1
		assert plan_events[0]["plan_id"] == "PLAN-001"


# =============================================================================
# on_plan_subject_changed — Plan Subject add/modify/delete
# =============================================================================


class TestOnPlanSubjectChanged:
	"""on_plan_subject_changed invalidates both hierarchy + catalog caches."""

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_invalidates_hierarchy_cache_for_subject(self, mock_get_redis, mock_frappe):
		"""TC-OPSC-01: Plan Subject change → hierarchy cache for that subject deleted."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_plan_subject_changed(
			_plan_subject_doc("PLAN-001", "SUBJ-001"), "on_update"
		)

		deleted_keys = [c[0][0] for c in mock_redis.delete.call_args_list]
		assert hierarchy_key("SUBJ-001") in deleted_keys

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_invalidates_catalog_cache_for_plan(self, mock_get_redis, mock_frappe):
		"""TC-OPSC-02: Plan Subject change → catalog cache for the plan deleted."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_plan_subject_changed(
			_plan_subject_doc("PLAN-001", "SUBJ-001"), "on_update"
		)

		deleted_keys = [c[0][0] for c in mock_redis.delete.call_args_list]
		assert catalog_key("PLAN-001") in deleted_keys

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_queues_build_with_trigger_reason_plan_subject_change(self, mock_get_redis, mock_frappe):
		"""TC-OPSC-03: Build queue entry has trigger_reason='plan_subject_change'."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		_setup_build_trigger_frappe(mock_frappe)

		build_trigger.on_plan_subject_changed(
			_plan_subject_doc("PLAN-001", "SUBJ-001"), "on_update"
		)

		mock_frappe.get_doc.assert_called_once()
		call_kwargs = mock_frappe.get_doc.call_args[0][0]
		assert call_kwargs["trigger_reason"] == "plan_subject_change"

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_no_parent_returns_early_without_ops(self, mock_get_redis, mock_frappe):
		"""TC-OPSC-04: Plan Subject doc with no parent → no cache/build ops."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		doc = SimpleNamespace(parent=None, subject="SUBJ-001", is_premium=0)

		build_trigger.on_plan_subject_changed(doc, "on_update")

		mock_frappe.get_doc.assert_not_called()


# =============================================================================
# Season lifecycle (access_sync)
# =============================================================================


class TestSeasonLifecycle:
	"""Season publish/unpublish/delete syncs Gate 1 data to Redis."""

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_published_season_stores_is_published_one(self, mock_get_redis):
		"""TC-SEA-01: Published season → Redis hash has is_published='1'."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_season_updated(_season("SEAS-001", is_published=1), "on_update")

		mapping = mock_redis.hset.call_args[1]["mapping"]
		assert mapping["is_published"] == "1"

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_unpublished_season_stores_is_published_zero(self, mock_get_redis):
		"""TC-SEA-02: Unpublished season → Redis hash has is_published='0'."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_season_updated(_season("SEAS-001", is_published=0), "on_update")

		mapping = mock_redis.hset.call_args[1]["mapping"]
		assert mapping["is_published"] == "0"

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_season_update_uses_correct_key_format(self, mock_get_redis):
		"""TC-SEA-03: Season Redis key matches season_key() builder."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_season_updated(_season("SEAS-007"), "on_update")

		redis_key = mock_redis.hset.call_args[0][0]
		assert redis_key == season_key("SEAS-007")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_season_update_stores_all_required_fields(self, mock_get_redis):
		"""TC-SEA-04: Season sync stores start_date, end_date, season_seq."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_season_updated(
				_season("SEAS-001", start_date="2025-01-01", end_date="2026-01-01", season_seq=3),
				"on_update",
			)

		mapping = mock_redis.hset.call_args[1]["mapping"]
		assert mapping["start_date"] == "2025-01-01"
		assert mapping["end_date"] == "2026-01-01"
		assert mapping["season_seq"] == "3"

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_season_delete_removes_redis_key(self, mock_get_redis):
		"""TC-SEA-05: Season deleted → DEL memora:season:{season_id}."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_season_deleted(_season("SEAS-007"), "on_trash")

		mock_redis.delete.assert_called_once_with(season_key("SEAS-007"))


# =============================================================================
# Subscription lifecycle (access_sync)
# =============================================================================


class TestSubscriptionLifecycle:
	"""Subscription grant/revoke/delete controls Gate 2 access in Redis."""

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_activate_subscription_adds_grant_to_set(self, mock_get_redis):
		"""TC-SUB-01: is_active=1 → SADD access_key to player's access set."""
		mock_redis = MagicMock()
		mock_redis.exists.return_value = True
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_subscription_change(
				_subscription("PLAYER-00001", "SUB-MATH", is_active=1), "on_update"
			)

		mock_redis.sadd.assert_called_once_with(access_key("PLAYER-00001"), "SUB-MATH")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_activate_subscription_sets_ttl(self, mock_get_redis):
		"""TC-SUB-02: Activated grant → EXPIRE set to ACCESS_KEY_TTL (24h)."""
		mock_redis = MagicMock()
		mock_redis.exists.return_value = True
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_subscription_change(
				_subscription("PLAYER-00001", "SUB-MATH", is_active=1), "on_update"
			)

		mock_redis.expire.assert_called_once_with(access_key("PLAYER-00001"), ACCESS_KEY_TTL)

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_activate_subscription_publishes_notification(self, mock_get_redis):
		"""TC-SUB-03: Subscription change → pubsub subscription_changed event."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_subscription_change(
				_subscription("PLAYER-00001", "SUB-MATH", is_active=1), "on_update"
			)

		mock_redis.publish.assert_called_once()
		channel, payload = mock_redis.publish.call_args[0]
		assert channel == cache_invalidation_channel()
		data = json.loads(payload)
		assert data["type"] == "subscription_changed"
		assert data["player_id"] == "PLAYER-00001"

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_deactivate_subscription_removes_grant(self, mock_get_redis):
		"""TC-SUB-04: is_active=0 → SREM grant from player's access set."""
		mock_redis = MagicMock()
		mock_redis.exists.return_value = True
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_subscription_change(
				_subscription("PLAYER-00001", "SUB-MATH", is_active=0), "on_update"
			)

		mock_redis.srem.assert_called_once_with(access_key("PLAYER-00001"), "SUB-MATH")
		mock_redis.sadd.assert_not_called()

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_deactivate_refreshes_ttl_if_other_grants_remain(self, mock_get_redis):
		"""TC-SUB-05: Deactivation when other grants exist → EXPIRE refreshed."""
		mock_redis = MagicMock()
		mock_redis.exists.return_value = True  # Other grants remain
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_subscription_change(
				_subscription("PLAYER-00001", "SUB-MATH", is_active=0), "on_update"
			)

		mock_redis.expire.assert_called_once_with(access_key("PLAYER-00001"), ACCESS_KEY_TTL)

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_delete_subscription_removes_grant(self, mock_get_redis):
		"""TC-SUB-06: Subscription deleted → SREM grant from player's access set."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_subscription_deleted(
				_subscription("PLAYER-00001", "SUB-MATH"), "on_trash"
			)

		mock_redis.srem.assert_called_once_with(access_key("PLAYER-00001"), "SUB-MATH")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_delete_subscription_publishes_notification(self, mock_get_redis):
		"""TC-SUB-07: Subscription deleted → pubsub subscription_changed event."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_subscription_deleted(
				_subscription("PLAYER-00001", "SUB-MATH"), "on_trash"
			)

		mock_redis.publish.assert_called_once()
		data = json.loads(mock_redis.publish.call_args[0][1])
		assert data["type"] == "subscription_changed"
		assert data["player_id"] == "PLAYER-00001"


# =============================================================================
# Plan Subject free sync (access_sync.on_plan_subject_changed)
# =============================================================================


class TestPlanSubjectFreeSync:
	"""Plan Subject is_premium flag syncs to memora:plan:{plan}:free_subjects."""

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_trash_removes_subject_from_free_set(self, mock_get_redis):
		"""TC-PSF-01: on_trash → SREM subject from plan free subjects set."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_plan_subject_changed(
				_plan_subject_doc("PLAN-001", "SUBJ-001", is_premium=0), "on_trash"
			)

		mock_redis.srem.assert_called_with(plan_free_subjects_key("PLAN-001"), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_free_subject_added_to_free_set(self, mock_get_redis):
		"""TC-PSF-02: is_premium=0 → SADD subject to plan free subjects set."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_plan_subject_changed(
				_plan_subject_doc("PLAN-001", "SUBJ-001", is_premium=0), "on_update"
			)

		mock_redis.sadd.assert_called_with(plan_free_subjects_key("PLAN-001"), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_free_subject_sets_ttl(self, mock_get_redis):
		"""TC-PSF-03: Free subject added → EXPIRE set to PLAN_FREE_SUBJECTS_TTL (12h)."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_plan_subject_changed(
				_plan_subject_doc("PLAN-001", "SUBJ-001", is_premium=0), "on_update"
			)

		mock_redis.expire.assert_called_with(plan_free_subjects_key("PLAN-001"), PLAN_FREE_SUBJECTS_TTL)

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_premium_subject_removed_from_free_set(self, mock_get_redis):
		"""TC-PSF-04: is_premium=1 → SREM subject from plan free subjects set."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_plan_subject_changed(
				_plan_subject_doc("PLAN-001", "SUBJ-001", is_premium=1), "on_update"
			)

		mock_redis.srem.assert_called_with(plan_free_subjects_key("PLAN-001"), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_always_publishes_plan_subjects_event(self, mock_get_redis):
		"""TC-PSF-05: Any plan_subject change → pubsub plan_subjects event for the plan."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		with patch("memora_admin.events.access_sync.frappe"):
			access_sync.on_plan_subject_changed(
				_plan_subject_doc("PLAN-001", "SUBJ-001", is_premium=0), "on_update"
			)

		mock_redis.publish.assert_called_once()
		data = json.loads(mock_redis.publish.call_args[0][1])
		assert data["type"] == "plan_subjects"
		assert data["plan_id"] == "PLAN-001"


# =============================================================================
# Unit / Topic is_free flag sync (access_sync)
# =============================================================================


class TestFreeContentSync:
	"""Unit and Topic is_free flag changes update memora:subjects_with_free_content set."""

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_unit_set_free_adds_subject_to_set(self, mock_get_redis):
		"""TC-FCS-01: Unit.is_free=1 → SADD subject to subjects_with_free_content."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		unit_doc = _unit(is_free=1)
		track = SimpleNamespace(subject="SUBJ-001")
		mock_frappe = MagicMock()
		mock_frappe.get_doc.return_value = track

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_unit_free_changed(unit_doc, "on_update")

		mock_redis.sadd.assert_called_with(subjects_with_free_content_key(), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_unit_set_not_free_checks_db_and_removes_when_empty(self, mock_get_redis):
		"""TC-FCS-02: Unit.is_free=0, no other free content → SREM subject."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		unit_doc = _unit(is_free=0)
		track = SimpleNamespace(subject="SUBJ-001")
		mock_frappe = MagicMock()
		mock_frappe.get_doc.return_value = track
		mock_frappe.db.sql.return_value = [(0,)]  # No free content remains

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_unit_free_changed(unit_doc, "on_update")

		mock_redis.srem.assert_called_with(subjects_with_free_content_key(), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_unit_set_not_free_keeps_subject_if_other_free_content_exists(self, mock_get_redis):
		"""TC-FCS-03: Unit.is_free=0 but other free units/topics exist → SADD (keep)."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		unit_doc = _unit(is_free=0)
		track = SimpleNamespace(subject="SUBJ-001")
		mock_frappe = MagicMock()
		mock_frappe.get_doc.return_value = track
		mock_frappe.db.sql.return_value = [(1,)]  # Other free content exists

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_unit_free_changed(unit_doc, "on_update")

		mock_redis.sadd.assert_called_with(subjects_with_free_content_key(), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_unit_trash_checks_db_and_removes_if_no_free_content(self, mock_get_redis):
		"""TC-FCS-04: Unit on_trash → DB check, SREM if subject has no more free content."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		unit_doc = _unit(is_free=1)
		track = SimpleNamespace(subject="SUBJ-001")
		mock_frappe = MagicMock()
		mock_frappe.get_doc.return_value = track
		mock_frappe.db.sql.return_value = [(0,)]  # Nothing left after deletion

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_unit_free_changed(unit_doc, "on_trash")

		mock_redis.srem.assert_called_with(subjects_with_free_content_key(), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_unit_trash_keeps_subject_if_other_free_content_remains(self, mock_get_redis):
		"""TC-FCS-05: Unit on_trash, other free content remains → SADD (keep)."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		unit_doc = _unit(is_free=1)
		track = SimpleNamespace(subject="SUBJ-001")
		mock_frappe = MagicMock()
		mock_frappe.get_doc.return_value = track
		mock_frappe.db.sql.return_value = [(1,)]

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_unit_free_changed(unit_doc, "on_trash")

		mock_redis.sadd.assert_called_with(subjects_with_free_content_key(), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_unit_with_no_track_returns_early(self, mock_get_redis):
		"""TC-FCS-06: Unit.track is None → no Redis ops, no Frappe queries."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		unit_doc = SimpleNamespace(doctype="Memora Unit", name="UNIT-001", track=None, is_free=1)
		mock_frappe = MagicMock()

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_unit_free_changed(unit_doc, "on_update")

		mock_redis.sadd.assert_not_called()
		mock_frappe.get_doc.assert_not_called()

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_topic_set_free_adds_subject_to_set(self, mock_get_redis):
		"""TC-FCS-07: Topic.is_free=1 → SADD subject to subjects_with_free_content."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		topic_doc = _topic(is_free=1)
		unit_mock = SimpleNamespace(track="TRK-001")
		track_mock = SimpleNamespace(subject="SUBJ-001")
		mock_frappe = MagicMock()
		mock_frappe.get_doc.side_effect = [unit_mock, track_mock]

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_topic_free_changed(topic_doc, "on_update")

		mock_redis.sadd.assert_called_with(subjects_with_free_content_key(), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_topic_trash_removes_subject_when_no_free_content_remains(self, mock_get_redis):
		"""TC-FCS-08: Topic on_trash, no more free content → SREM subject."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		topic_doc = _topic(is_free=1)
		unit_mock = SimpleNamespace(track="TRK-001")
		track_mock = SimpleNamespace(subject="SUBJ-001")
		mock_frappe = MagicMock()
		mock_frappe.get_doc.side_effect = [unit_mock, track_mock]
		mock_frappe.db.sql.return_value = [(0,)]

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_topic_free_changed(topic_doc, "on_trash")

		mock_redis.srem.assert_called_with(subjects_with_free_content_key(), "SUBJ-001")

	@patch("memora_admin.events.access_sync.get_memora_redis")
	def test_topic_with_no_unit_returns_early(self, mock_get_redis):
		"""TC-FCS-09: Topic.unit is None → no Redis ops, no Frappe queries."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		topic_doc = SimpleNamespace(doctype="Memora Topic", name="TOP-001", unit=None, is_free=1)
		mock_frappe = MagicMock()

		with patch("memora_admin.events.access_sync.frappe", mock_frappe):
			access_sync.on_topic_free_changed(topic_doc, "on_update")

		mock_redis.sadd.assert_not_called()
		mock_frappe.get_doc.assert_not_called()


# =============================================================================
# on_plan_deleted — GAP 1 fix: storage + CDN + Redis cleanup on plan deletion
# =============================================================================


class TestOnPlanDeleted:
	"""on_plan_deleted cleans up storage, CDN, and Redis when a plan is deleted."""

	def _doc(self, name="PLAN-001"):
		return SimpleNamespace(doctype="Memora Academic Plan", name=name)

	def _setup(self, mock_get_storage, mock_get_redis, file_keys=None):
		"""Wire up standard mocks for on_plan_deleted tests."""
		mock_storage = MagicMock()
		mock_storage.list_directory.return_value = file_keys or []
		mock_storage.delete_directory.return_value = bool(file_keys)
		mock_get_storage.return_value = mock_storage

		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis

		return mock_storage, mock_redis

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")
	@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")
	def test_deletes_plan_directory_from_storage(
		self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe
	):
		"""TC-OPD-01: delete_directory called with plans/{plan_id} path."""
		mock_storage, mock_redis = self._setup(mock_get_storage, mock_get_redis)
		mock_frappe.logger.return_value = MagicMock()

		build_trigger.on_plan_deleted(self._doc("PLAN-001"), "on_trash")

		mock_storage.delete_directory.assert_called_once_with("plans/PLAN-001")

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")
	@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")
	def test_purges_cdn_files_for_plan(
		self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe
	):
		"""TC-OPD-02: CDN purge issued for all files listed in plans/{plan_id}/."""
		file_keys = ["plans/PLAN-001/manifest.json", "plans/PLAN-001/subjects/SUBJ-001.json"]
		mock_storage, mock_redis = self._setup(mock_get_storage, mock_get_redis, file_keys)
		mock_purge = MagicMock()
		mock_get_purge.return_value = mock_purge
		mock_frappe.logger.return_value = MagicMock()

		build_trigger.on_plan_deleted(self._doc("PLAN-001"), "on_trash")

		mock_purge.purge_files.assert_called_once_with(file_keys)

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")
	@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")
	def test_no_cdn_purge_when_directory_is_empty(
		self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe
	):
		"""TC-OPD-03: No CDN purge issued if directory has no files (already clean)."""
		mock_storage, mock_redis = self._setup(mock_get_storage, mock_get_redis, file_keys=[])
		mock_frappe.logger.return_value = MagicMock()

		build_trigger.on_plan_deleted(self._doc("PLAN-001"), "on_trash")

		mock_get_purge.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")
	@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")
	def test_deletes_all_plan_redis_keys(
		self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe
	):
		"""TC-OPD-04: catalog, manifest, free_subjects, and debounce keys all deleted."""
		mock_storage, mock_redis = self._setup(mock_get_storage, mock_get_redis)
		mock_frappe.logger.return_value = MagicMock()

		build_trigger.on_plan_deleted(self._doc("PLAN-001"), "on_trash")

		deleted_keys = mock_redis.delete.call_args[0]
		assert catalog_key("PLAN-001") in deleted_keys
		assert plan_manifest_key("PLAN-001") in deleted_keys
		assert plan_free_subjects_key("PLAN-001") in deleted_keys
		assert build_debounce_key("PLAN-001") in deleted_keys

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")
	@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")
	def test_publishes_catalog_invalidation_event(
		self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe
	):
		"""TC-OPD-05: Publishes {"type": "catalog", "plan_id": ...} to cache channel."""
		mock_storage, mock_redis = self._setup(mock_get_storage, mock_get_redis)
		mock_frappe.logger.return_value = MagicMock()

		build_trigger.on_plan_deleted(self._doc("PLAN-001"), "on_trash")

		mock_redis.publish.assert_called_once()
		channel, payload = mock_redis.publish.call_args[0]
		assert channel == cache_invalidation_channel()
		data = json.loads(payload)
		assert data["type"] == "catalog"
		assert data["plan_id"] == "PLAN-001"

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")
	@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")
	def test_storage_error_is_swallowed_and_redis_still_runs(
		self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe
	):
		"""TC-OPD-06: Storage failure is logged; Redis cleanup still executes."""
		mock_get_storage.side_effect = Exception("Storage unavailable")
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		mock_frappe.logger.return_value = MagicMock()

		build_trigger.on_plan_deleted(self._doc("PLAN-001"), "on_trash")  # must not raise

		mock_frappe.log_error.assert_called()
		mock_redis.delete.assert_called_once()  # Redis cleanup still runs

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")
	@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")
	def test_redis_error_is_swallowed(
		self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe
	):
		"""TC-OPD-07: Redis failure is logged and does not propagate."""
		mock_storage, _ = self._setup(mock_get_storage, mock_get_redis)
		mock_get_redis.side_effect = Exception("Redis down")
		mock_frappe.logger.return_value = MagicMock()

		build_trigger.on_plan_deleted(self._doc("PLAN-001"), "on_trash")  # must not raise

		mock_frappe.log_error.assert_called()


# =============================================================================
# _remove_subject_from_plan_free_subjects — GAP 2 fix
# =============================================================================


class TestRemoveSubjectFromPlanFreeSubjects:
	"""_remove_subject_from_plan_free_subjects removes a deleted subject from plan Redis sets."""

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_srems_subject_from_all_matching_plans(self, mock_get_redis, mock_frappe):
		"""TC-RSF-01: Subject in two free plans → SREM from both plan free_subjects sets."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		mock_frappe.get_all.return_value = [{"parent": "PLAN-001"}, {"parent": "PLAN-002"}]
		mock_frappe.logger.return_value = MagicMock()

		build_trigger._remove_subject_from_plan_free_subjects("SUBJ-001")

		assert mock_redis.srem.call_count == 2
		mock_redis.srem.assert_any_call(plan_free_subjects_key("PLAN-001"), "SUBJ-001")
		mock_redis.srem.assert_any_call(plan_free_subjects_key("PLAN-002"), "SUBJ-001")

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_queries_only_non_premium_plan_subjects(self, mock_get_redis, mock_frappe):
		"""TC-RSF-02: Filters Plan Subject query by is_premium=0."""
		mock_redis = MagicMock()
		mock_get_redis.return_value = mock_redis
		mock_frappe.get_all.return_value = []

		build_trigger._remove_subject_from_plan_free_subjects("SUBJ-001")

		_, kwargs = mock_frappe.get_all.call_args
		assert kwargs["filters"] == {"subject": "SUBJ-001", "is_premium": 0}

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_no_plans_skips_redis_entirely(self, mock_get_redis, mock_frappe):
		"""TC-RSF-03: No matching plans → get_memora_redis never called, no Redis ops."""
		mock_frappe.get_all.return_value = []

		build_trigger._remove_subject_from_plan_free_subjects("SUBJ-001")

		mock_get_redis.assert_not_called()

	@patch("memora_admin.events.build_trigger.frappe")
	@patch("memora_admin.utils.redis_connection.get_memora_redis")
	def test_redis_error_is_swallowed(self, mock_get_redis, mock_frappe):
		"""TC-RSF-04: Redis failure is logged and does not propagate."""
		mock_frappe.get_all.return_value = [{"parent": "PLAN-001"}]
		mock_get_redis.side_effect = Exception("Redis down")

		build_trigger._remove_subject_from_plan_free_subjects("SUBJ-001")  # must not raise

		mock_frappe.log_error.assert_called_once()
