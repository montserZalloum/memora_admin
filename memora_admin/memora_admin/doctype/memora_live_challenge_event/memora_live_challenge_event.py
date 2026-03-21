# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import json
import math
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
	def after_save(self):
		"""Populate Redis with event data when saved as Draft.

		This makes the event available to the FastAPI status endpoint immediately,
		so client-driven transitions can work without waiting for the cron.
		Re-populates on every Draft save to pick up edits (questions, schedule).
		Skips if client-driven transitions have already advanced the status beyond draft.
		"""
		if self.status != "Draft":
			return

		from fastapi_app.core.redis_keys import (
			LC_KEY_TTL,
			lc_count_key,
			lc_meta_key,
			lc_questions_key,
			lc_status_key,
		)
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()

		# Don't overwrite if client-driven transitions already advanced beyond draft
		current_status = r.get(lc_status_key(self.name))
		if current_status is not None:
			# Redis may return bytes or str depending on decode_responses setting
			status_str = current_status.decode() if isinstance(current_status, bytes) else current_status
			if status_str not in ("draft", ""):
				return

		pipe = r.pipeline()

		# Status (only set if not already present — avoids brief race with transitions)
		pipe.setnx(lc_status_key(self.name), "draft")
		pipe.expire(lc_status_key(self.name), LC_KEY_TTL)

		# Questions JSON
		questions = []
		for q in self.questions:
			questions.append({
				"idx": q.idx - 1,
				"question_text": q.question_text,
				"option_a": q.option_a,
				"option_b": q.option_b,
				"option_c": q.option_c,
				"option_d": q.option_d,
				"correct_answer": q.correct_answer,
			})
		pipe.set(lc_questions_key(self.name), json.dumps(questions), ex=LC_KEY_TTL)

		# Meta hash — includes ALL fields needed by get_event_detail (Redis-only reads)
		eligible_plans = [ep.plan for ep in (self.eligible_plans or [])]
		meta = {
			"scheduled_start": str(self.scheduled_start),
			"exam_start_ts": str(self.exam_start_ts),
			"exam_end_ts": str(self.exam_end_ts),
			"capacity": str(self.capacity),
			"enable_question_timer": str(int(self.enable_question_timer)),
			"question_time_limit": str(self.question_time_limit or 30),
			"waiting_room_duration": str(self.waiting_room_duration),
			"eligible_plans": json.dumps(eligible_plans),
			"event_name": self.event_name or "",
			"description": self.description or "",
			"exam_duration": str(self.exam_duration),
			"is_paid": str(int(self.is_paid)),
			"price": str(self.price or 0),
			"currency": self.currency or "JOD",
			"participation_xp": str(self.participation_xp or 0),
			"first_place_xp": str(self.first_place_xp or 0),
			"second_place_xp": str(self.second_place_xp or 0),
			"third_place_xp": str(self.third_place_xp or 0),
			"default_xp": str(self.default_xp or 0),
		}
		meta_key = lc_meta_key(self.name)
		# hset with all fields is an atomic overwrite — no delete needed
		pipe.hset(meta_key, mapping=meta)
		pipe.expire(meta_key, LC_KEY_TTL)

		# Count key (preserve existing count if already set, else init to 0)
		pipe.setnx(lc_count_key(self.name), "0")
		pipe.expire(lc_count_key(self.name), LC_KEY_TTL)

		pipe.execute()

	def validate(self):
		self._auto_calc_exam_duration()
		self._compute_timestamps()
		self._validate_ranges()
		self._validate_xp_non_negative()
		self._validate_paid_event_fields()
		self._validate_status_transition()
		self._validate_questions_before_leaving_draft()
		self._validate_freeze_after_draft()
		if self.status == "Draft":
			self._validate_no_overlap()

	def _auto_calc_exam_duration(self):
		"""Auto-calculate exam_duration when question timer is enabled."""
		if not self.enable_question_timer:
			return
		question_count = len(self.questions or [])
		time_limit = int(self.question_time_limit or 30)
		self.exam_duration = max(math.ceil((time_limit * question_count) / 60), 1)

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
		if cap < 0 or cap > 10000:
			frappe.throw("Capacity must be between 0 and 10,000 (0 = unlimited).")

	def _validate_xp_non_negative(self):
		"""All XP reward fields must be >= 0."""
		for field in _XP_FIELDS:
			val = int(self.get(field) or 0)
			if val < 0:
				label = self.meta.get_label(field) or field
				frappe.throw(f"{label} must be non-negative (got {val}).")

	def _validate_paid_event_fields(self):
		"""When is_paid is checked, price is required."""
		if not self.is_paid:
			return
		price = self.price or 0
		if price <= 0:
			frappe.throw("Price must be greater than 0 for paid events.")

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
