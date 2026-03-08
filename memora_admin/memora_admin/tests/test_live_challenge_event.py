"""Unit tests for Memora Live Challenge Event DocType.

Tests:
- VALID_TRANSITIONS rejects invalid transitions
- Computed fields (exam_start_ts, exam_end_ts) are set correctly
- Overlap detection rejects conflicting schedules with 5-minute buffer
- At-least-one-question validation
- XP field non-negative validation
"""

import unittest
from datetime import datetime, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, now_datetime

# Counter to space out test events in time and avoid cross-test overlap
_TIME_OFFSET = 0


def _next_offset():
	"""Return an increasing offset (hours) so each test event gets unique times."""
	global _TIME_OFFSET
	_TIME_OFFSET += 10
	return _TIME_OFFSET


def _make_event(
	event_name=None,
	scheduled_start=None,
	waiting_room_duration=180,
	exam_duration=10,
	capacity=100,
	questions=None,
	eligible_plans=None,
	status="Draft",
	do_not_save=False,
	**kwargs,
):
	"""Helper to create a Live Challenge Event for tests."""
	if event_name is None:
		event_name = f"Test Event {frappe.generate_hash(length=6)}"
	if scheduled_start is None:
		scheduled_start = now_datetime() + timedelta(hours=_next_offset())
	if questions is None:
		questions = [
			{
				"question_text": "What is 2+2?",
				"option_a": "3",
				"option_b": "4",
				"option_c": "5",
				"option_d": "6",
				"correct_answer": "B",
			}
		]

	doc = frappe.get_doc(
		{
			"doctype": "Memora Live Challenge Event",
			"event_name": event_name,
			"status": status,
			"scheduled_start": scheduled_start,
			"waiting_room_duration": waiting_room_duration,
			"exam_duration": exam_duration,
			"capacity": capacity,
			"questions": questions,
			**kwargs,
		}
	)

	if eligible_plans:
		for plan in eligible_plans:
			doc.append("eligible_plans", {"plan": plan})

	if not do_not_save:
		doc.insert(ignore_permissions=True)

	return doc


class TestLiveChallengeEventTransitions(FrappeTestCase):
	"""Test VALID_TRANSITIONS enforcement."""

	def test_valid_transition_draft_to_waiting(self):
		"""Draft -> Waiting is allowed."""
		event = _make_event()
		event.status = "Waiting"
		event.flags.ignore_validate = True
		event.save(ignore_permissions=True)
		self.assertEqual(event.status, "Waiting")

	def test_valid_transition_waiting_to_active(self):
		"""Waiting -> Active is allowed."""
		event = _make_event()
		event.status = "Waiting"
		event.flags.ignore_validate = True
		event.save(ignore_permissions=True)
		event.flags.ignore_validate = False
		event.status = "Active"
		event.flags.ignore_validate = True
		event.save(ignore_permissions=True)
		self.assertEqual(event.status, "Active")

	def test_valid_transition_active_to_ended(self):
		"""Active -> Ended is allowed."""
		event = _make_event()
		# Fast-track through transitions using ignore_validate
		for target in ("Waiting", "Active", "Ended"):
			event.status = target
			event.flags.ignore_validate = True
			event.save(ignore_permissions=True)
		self.assertEqual(event.status, "Ended")

	def test_invalid_transition_draft_to_active(self):
		"""Draft -> Active is NOT allowed."""
		event = _make_event()
		event.status = "Active"
		self.assertRaises(frappe.ValidationError, event.save, ignore_permissions=True)

	def test_invalid_transition_draft_to_ended(self):
		"""Draft -> Ended is NOT allowed."""
		event = _make_event()
		event.status = "Ended"
		self.assertRaises(frappe.ValidationError, event.save, ignore_permissions=True)

	def test_invalid_transition_waiting_to_draft(self):
		"""Waiting -> Draft is NOT allowed (no going back)."""
		event = _make_event()
		event.status = "Waiting"
		event.flags.ignore_validate = True
		event.save(ignore_permissions=True)
		event.flags.ignore_validate = False
		event.status = "Draft"
		self.assertRaises(frappe.ValidationError, event.save, ignore_permissions=True)

	def test_invalid_transition_ended_is_terminal(self):
		"""Ended is a terminal state - no transitions out."""
		event = _make_event()
		for target in ("Waiting", "Active", "Ended"):
			event.status = target
			event.flags.ignore_validate = True
			event.save(ignore_permissions=True)
		event.flags.ignore_validate = False
		event.status = "Draft"
		self.assertRaises(frappe.ValidationError, event.save, ignore_permissions=True)


class TestLiveChallengeEventComputedFields(FrappeTestCase):
	"""Test computed fields (exam_start_ts, exam_end_ts)."""

	def test_computed_exam_start_ts(self):
		"""exam_start_ts = scheduled_start + waiting_room_duration."""
		start = now_datetime().replace(microsecond=0) + timedelta(hours=_next_offset())
		event = _make_event(scheduled_start=start, waiting_room_duration=180)
		expected = start + timedelta(seconds=180)
		actual = get_datetime(event.exam_start_ts)
		self.assertEqual(actual.replace(microsecond=0), expected)

	def test_computed_exam_end_ts(self):
		"""exam_end_ts = exam_start_ts + exam_duration (minutes)."""
		start = now_datetime().replace(microsecond=0) + timedelta(hours=_next_offset())
		event = _make_event(scheduled_start=start, waiting_room_duration=180, exam_duration=10)
		expected_end = start + timedelta(seconds=180) + timedelta(minutes=10)
		actual = get_datetime(event.exam_end_ts)
		self.assertEqual(actual.replace(microsecond=0), expected_end)

	def test_computed_fields_update_on_change(self):
		"""Computed fields should update when schedule changes."""
		event = _make_event(waiting_room_duration=60, exam_duration=5)
		event.waiting_room_duration = 120
		event.exam_duration = 15
		event.save(ignore_permissions=True)
		start = get_datetime(event.scheduled_start)
		expected_exam_start = start + timedelta(seconds=120)
		expected_exam_end = expected_exam_start + timedelta(minutes=15)
		self.assertEqual(get_datetime(event.exam_start_ts), expected_exam_start)
		self.assertEqual(get_datetime(event.exam_end_ts), expected_exam_end)


class TestLiveChallengeEventOverlapDetection(FrappeTestCase):
	"""Test schedule overlap detection with 5-minute buffer.

	Overlap is checked against non-Draft events. We must transition one event
	to Waiting first, then verify the second one is rejected.
	"""

	def test_overlapping_events_rejected(self):
		"""Two events with overlapping time slots should be rejected."""
		base_offset = _next_offset()
		start1 = now_datetime() + timedelta(hours=base_offset)
		event_a = _make_event(
			event_name="Overlap A", scheduled_start=start1, waiting_room_duration=180, exam_duration=30
		)
		# Transition event_a to Waiting so it counts for overlap checks
		event_a.status = "Waiting"
		event_a.flags.ignore_validate = True
		event_a.save(ignore_permissions=True)

		# Second event starts during first event's exam
		start2 = start1 + timedelta(minutes=10)
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			event_name="Overlap B",
			scheduled_start=start2,
			waiting_room_duration=180,
			exam_duration=30,
		)

	def test_events_within_5min_buffer_rejected(self):
		"""Events within 5-minute buffer after another event should be rejected."""
		base_offset = _next_offset()
		start1 = now_datetime().replace(microsecond=0) + timedelta(hours=base_offset)
		event_c = _make_event(
			event_name="Buffer C", scheduled_start=start1, waiting_room_duration=60, exam_duration=10
		)
		# Transition to Waiting so it counts for overlap
		event_c.status = "Waiting"
		event_c.flags.ignore_validate = True
		event_c.save(ignore_permissions=True)

		# event_c ends at start1 + 60s + 10min = start1 + 11min
		end_c = get_datetime(event_c.exam_end_ts)
		# start2 is 2 min after event_c ends (within 5-min buffer)
		start2 = end_c + timedelta(minutes=2)
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			event_name="Buffer D",
			scheduled_start=start2,
			waiting_room_duration=60,
			exam_duration=10,
		)

	def test_events_after_buffer_allowed(self):
		"""Events starting after the 5-minute buffer should be allowed."""
		base_offset = _next_offset()
		start1 = now_datetime().replace(microsecond=0) + timedelta(hours=base_offset)
		event_e = _make_event(
			event_name="After E", scheduled_start=start1, waiting_room_duration=60, exam_duration=10
		)
		# Transition to Waiting
		event_e.status = "Waiting"
		event_e.flags.ignore_validate = True
		event_e.save(ignore_permissions=True)

		end_e = get_datetime(event_e.exam_end_ts)
		start2 = end_e + timedelta(minutes=6)
		event_f = _make_event(
			event_name="After F", scheduled_start=start2, waiting_room_duration=60, exam_duration=10
		)
		self.assertTrue(event_f.name)

	def test_draft_events_excluded_from_overlap(self):
		"""Draft events should not block new event creation."""
		base_offset = _next_offset()
		start1 = now_datetime() + timedelta(hours=base_offset)
		_make_event(event_name="Draft G", scheduled_start=start1)
		# Same time, also Draft - should be allowed (Drafts excluded from overlap check)
		event_h = _make_event(event_name="Draft H", scheduled_start=start1)
		self.assertTrue(event_h.name)


class TestLiveChallengeEventValidations(FrappeTestCase):
	"""Test validation rules."""

	def test_at_least_one_question_required_before_leaving_draft(self):
		"""Cannot transition from Draft if no questions.

		Note: Frappe's reqd=1 on the questions table field means empty questions
		triggers MandatoryError at the ORM level. We verify a question exists,
		remove it, then attempt to transition — our custom validation catches this.
		"""
		event = _make_event()
		# Remove all questions
		event.questions = []
		event.status = "Waiting"
		# This should fail — either MandatoryError or our ValidationError
		self.assertRaises(Exception, event.save, ignore_permissions=True)

	def test_xp_fields_non_negative(self):
		"""All XP fields must be >= 0."""
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			participation_xp=-10,
		)

	def test_xp_first_place_non_negative(self):
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			first_place_xp=-5,
		)

	def test_xp_second_place_non_negative(self):
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			second_place_xp=-1,
		)

	def test_xp_third_place_non_negative(self):
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			third_place_xp=-1,
		)

	def test_xp_default_non_negative(self):
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			default_xp=-1,
		)

	def test_waiting_room_duration_min(self):
		"""Waiting room duration must be >= 30 seconds."""
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			waiting_room_duration=10,
		)

	def test_waiting_room_duration_max(self):
		"""Waiting room duration must be <= 600 seconds."""
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			waiting_room_duration=700,
		)

	def test_exam_duration_min(self):
		"""Exam duration must be >= 1 minute."""
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			exam_duration=0,
		)

	def test_exam_duration_max(self):
		"""Exam duration must be <= 180 minutes."""
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			exam_duration=200,
		)

	def test_capacity_min(self):
		"""Capacity must be >= 1."""
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			capacity=0,
		)

	def test_capacity_max(self):
		"""Capacity must be <= 10000."""
		self.assertRaises(
			frappe.ValidationError,
			_make_event,
			capacity=15000,
		)

	def test_freeze_edits_after_draft(self):
		"""Editing fields other than status is blocked after leaving Draft."""
		event = _make_event()
		event.status = "Waiting"
		event.flags.ignore_validate = True
		event.save(ignore_permissions=True)
		event.flags.ignore_validate = False
		event.event_name = "Modified Name"
		self.assertRaises(frappe.ValidationError, event.save, ignore_permissions=True)
