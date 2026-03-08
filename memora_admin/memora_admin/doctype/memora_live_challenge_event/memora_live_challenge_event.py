# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

VALID_TRANSITIONS = {
	"Draft": {"Waiting"},
	"Waiting": {"Active"},
	"Active": {"Ended"},
	"Ended": set(),  # Terminal
}

# Fields that are allowed to change after leaving Draft (status + read-only counters)
_MUTABLE_AFTER_DRAFT = {
	"status",
	"participant_count",
	"submitted_count",
	"leaderboard_json",
	"modified",
	"modified_by",
}

# 5-minute buffer between events (seconds)
OVERLAP_BUFFER = 300

# XP fields that must be non-negative
_XP_FIELDS = (
	"participation_xp",
	"first_place_xp",
	"second_place_xp",
	"third_place_xp",
	"default_xp",
)


class MemoraLiveChallengeEvent(Document):
	def validate(self):
		self._compute_timestamps()
		self._validate_ranges()
		self._validate_xp_non_negative()
		self._validate_status_transition()
		self._validate_questions_before_leaving_draft()
		self._validate_freeze_after_draft()
		if self.status == "Draft":
			self._validate_no_overlap()

	def _compute_timestamps(self):
		"""Set exam_start_ts and exam_end_ts from schedule fields."""
		if self.scheduled_start and self.waiting_room_duration is not None and self.exam_duration is not None:
			start = get_datetime(self.scheduled_start)
			self.exam_start_ts = start + timedelta(seconds=int(self.waiting_room_duration))
			self.exam_end_ts = get_datetime(self.exam_start_ts) + timedelta(minutes=int(self.exam_duration))

	def _validate_ranges(self):
		"""Validate min/max ranges for duration and capacity fields."""
		wr = int(self.waiting_room_duration or 0)
		if wr < 30 or wr > 600:
			frappe.throw("Waiting Room Duration must be between 30 and 600 seconds.")

		ed = int(self.exam_duration or 0)
		if ed < 1 or ed > 180:
			frappe.throw("Exam Duration must be between 1 and 180 minutes.")

		cap = int(self.capacity or 0)
		if cap < 1 or cap > 10000:
			frappe.throw("Capacity must be between 1 and 10,000.")

	def _validate_xp_non_negative(self):
		"""All XP reward fields must be >= 0."""
		for field in _XP_FIELDS:
			val = int(self.get(field) or 0)
			if val < 0:
				label = self.meta.get_label(field) or field
				frappe.throw(f"{label} must be non-negative (got {val}).")

	def _validate_status_transition(self):
		"""Enforce VALID_TRANSITIONS state machine."""
		if self.is_new():
			return
		if not self.has_value_changed("status"):
			return
		old_doc = self.get_doc_before_save()
		if not old_doc:
			return
		old_status = old_doc.status
		allowed = VALID_TRANSITIONS.get(old_status, set())
		if self.status not in allowed:
			frappe.throw(
				f"Invalid status transition: {old_status} -> {self.status}. "
				f"Allowed transitions from {old_status}: {', '.join(sorted(allowed)) or 'none (terminal)'}."
			)

	def _validate_questions_before_leaving_draft(self):
		"""At least one question is required before transitioning out of Draft."""
		if not self.is_new() and self.has_value_changed("status") and self.status != "Draft":
			if not self.questions or len(self.questions) == 0:
				frappe.throw("At least one question is required before the event can leave Draft status.")

	def _validate_freeze_after_draft(self):
		"""Prevent editing fields (other than status and counters) after leaving Draft."""
		if self.is_new():
			return
		old_doc = self.get_doc_before_save()
		if not old_doc or old_doc.status == "Draft":
			return

		for field in self.meta.get_valid_columns():
			if field in _MUTABLE_AFTER_DRAFT or field.startswith("_"):
				continue
			old_val = old_doc.get(field)
			new_val = self.get(field)
			if str(old_val or "") != str(new_val or ""):
				frappe.throw(
					f"Cannot modify '{self.meta.get_label(field) or field}' after the event has left Draft status."
				)

	def _validate_no_overlap(self):
		"""Reject overlapping schedules with 5-minute buffer against non-Draft events."""
		if not self.exam_start_ts or not self.exam_end_ts:
			return

		my_start = get_datetime(self.scheduled_start)
		my_end = get_datetime(self.exam_end_ts) + timedelta(seconds=OVERLAP_BUFFER)

		filters = {"status": ["not in", ["Draft"]]}
		if not self.is_new():
			filters["name"] = ["!=", self.name]

		existing = frappe.get_all(
			"Memora Live Challenge Event",
			filters=filters,
			fields=["name", "event_name", "scheduled_start", "exam_end_ts"],
		)

		for ev in existing:
			ev_start = get_datetime(ev.scheduled_start)
			ev_end = get_datetime(ev.exam_end_ts) + timedelta(seconds=OVERLAP_BUFFER)

			# Check overlap: my event's [start, end+buffer] overlaps with existing [start, end+buffer]
			if my_start < ev_end and my_end > ev_start:
				frappe.throw(
					f"Schedule conflicts with '{ev.event_name}' ({ev.name}). "
					f"There must be at least a 5-minute gap between events."
				)
