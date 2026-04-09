# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import json
import math
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

from memora_admin.memora_admin.doctype.memora_challenge_reward.memora_challenge_reward import rewards_to_dicts

_EXCEL_REQUIRED_COLUMNS = {"question_text", "option_a", "option_b", "option_c", "option_d", "correct_answer"}
_VALID_ANSWERS = {"A", "B", "C", "D"}


@frappe.whitelist()
def import_questions_from_excel(file_content):
	"""Parse an .xlsx file sent as base64 and return a list of question dicts for the child table.

	Expected header row: question_text, option_a, option_b, option_c, option_d, correct_answer
	"""
	import base64
	import io

	import openpyxl

	try:
		fcontent = base64.b64decode(file_content)
	except Exception as e:
		frappe.throw(f"Invalid file content: {e}")

	try:
		wb = openpyxl.load_workbook(io.BytesIO(fcontent), read_only=True, data_only=True)
	except Exception as e:
		frappe.throw(f"Could not open Excel file: {e}")

	ws = wb.active
	all_rows = list(ws.iter_rows(values_only=True))
	if not all_rows:
		frappe.throw("The Excel file is empty.")

	headers = [str(h).strip().lower() if h is not None else "" for h in all_rows[0]]
	missing = _EXCEL_REQUIRED_COLUMNS - set(headers)
	if missing:
		frappe.throw(f"Missing required column(s): {', '.join(sorted(missing))}")

	col = {h: i for i, h in enumerate(headers)}
	questions = []
	for row_num, row in enumerate(all_rows[1:], start=2):
		q_text = str(row[col["question_text"]] or "").strip()
		if not q_text:
			continue  # skip blank rows

		answer = str(row[col["correct_answer"]] or "").strip().upper()
		if answer not in _VALID_ANSWERS:
			frappe.throw(f"Row {row_num}: correct_answer must be A, B, C, or D (got '{answer}')")

		questions.append(
			{
				"question_text": q_text,
				"option_a": str(row[col["option_a"]] or "").strip(),
				"option_b": str(row[col["option_b"]] or "").strip(),
				"option_c": str(row[col["option_c"]] or "").strip(),
				"option_d": str(row[col["option_d"]] or "").strip(),
				"correct_answer": answer,
			}
		)

	return questions


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


class MemoraLiveChallengeEvent(Document):
	def before_save(self):
		"""Enforce immutable mode after creation."""
		if not self.is_new() and self.has_value_changed("mode"):
			frappe.throw("Mode cannot be changed after creation")

	def on_update(self):
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
			lc_mode_key,
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
			questions.append(
				{
					"idx": q.idx - 1,
					"question_text": q.question_text,
					"option_a": q.option_a,
					"option_b": q.option_b,
					"option_c": q.option_c,
					"option_d": q.option_d,
					"correct_answer": q.correct_answer,
				}
			)
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
			"rewards_json": json.dumps(rewards_to_dicts(self.rewards)),
			"mode": self.mode or "exam",
			"starting_hearts": str(self.starting_hearts or 3),
			"result_window_duration": str(self.result_window_duration or 3),
		}
		meta_key = lc_meta_key(self.name)
		# hset with all fields is an atomic overwrite — no delete needed
		pipe.hset(meta_key, mapping=meta)
		pipe.expire(meta_key, LC_KEY_TTL)

		# Mode key (needed by join/grade to distinguish exam vs last_stand)
		pipe.set(lc_mode_key(self.name), self.mode or "exam", ex=LC_KEY_TTL)

		# Count key (preserve existing count if already set, else init to 0)
		pipe.setnx(lc_count_key(self.name), "0")
		pipe.expire(lc_count_key(self.name), LC_KEY_TTL)

		pipe.execute()

	def validate(self):
		self._validate_last_stand_fields()
		self._auto_calc_exam_duration()
		self._compute_timestamps()
		self._validate_ranges()
		self._validate_rewards()
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
		if self.mode == "last_stand":
			result_window = int(self.result_window_duration or 3)
			total_seconds = question_count * (time_limit + result_window)
			# Add 1 minute buffer for transitions and final reconciliation
			self.exam_duration = max(math.ceil(total_seconds / 60) + 1, 1)
		else:
			self.exam_duration = max(math.ceil((time_limit * question_count) / 60), 1)

	def _validate_last_stand_fields(self):
		"""Validate Last Stand-specific fields when mode is last_stand."""
		if self.mode != "last_stand":
			return
		if not self.enable_question_timer:
			frappe.throw("Question timer must be enabled for Last Stand mode")
		hearts = int(self.starting_hearts or 0)
		if not (1 <= hearts <= 10):
			frappe.throw("Starting hearts must be between 1 and 10")
		rwd = int(self.result_window_duration or 0)
		if not (1 <= rwd <= 10):
			frappe.throw("Result window duration must be between 1 and 10 seconds")

	def _compute_timestamps(self):
		"""Compute exam_start_ts / exam_end_ts from scheduled_start.

		Timestamps are stored as naive datetimes in server-local time.
		The FastAPI service compares them against datetime.now() (no TZ).
		"""
		if self.scheduled_start and self.waiting_room_duration is not None and self.exam_duration is not None:
			start = get_datetime(self.scheduled_start)
			self.exam_start_ts = start + timedelta(seconds=int(self.waiting_room_duration))
			self.exam_end_ts = self.exam_start_ts + timedelta(minutes=int(self.exam_duration))

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

	def _validate_rewards(self):
		"""Validate reward child table rows."""
		has_fallback = False
		for row in self.rewards or []:
			if int(row.rank or 0) < 0:
				frappe.throw(f"Reward row {row.idx}: Rank must be non-negative (got {row.rank}).")
			if row.reward_type == "XP":
				xp = int(row.xp_amount or 0)
				if xp < 0:
					frappe.throw(f"Reward row {row.idx}: XP amount must be non-negative (got {xp}).")
			elif row.reward_type == "Prize":
				if not (row.prize_description or "").strip():
					frappe.throw(
						f"Reward row {row.idx}: Prize description is required when reward type is Prize."
					)
			if row.rank == 0:
				has_fallback = True
		if (self.rewards or []) and not has_fallback:
			frappe.msgprint(
				"No fallback reward (rank 0) defined. Players with unmatched ranks will receive nothing."
			)

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
