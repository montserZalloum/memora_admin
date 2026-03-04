"""Frappe whitelisted API for FSRS review operations.

Provides three endpoints:
1. get_review_overview - Due review counts per subject for a player (item-level)
2. get_due_items - Due items for a specific subject (FIFO order)
3. submit_reviews - Batch submit review results with inline FSRS computation (item-level)

All queries include season_seq for partition pruning on the Memory State table.

IMPORTANT -- RAW SQL ONLY:
  Memora Memory State is a RANGE-partitioned table designed for 10+ billion rows.
  Frappe ORM (get_doc, get_all, get_list, db.get_value, etc.) is FORBIDDEN because:
  1. Frappe ORM cannot handle BINARY(16) columns (item_id).
  2. Frappe ORM does not include season_seq in WHERE, breaking partition pruning.
  3. ORM-generated queries may cause full table scans on a 10B-row table.
  All queries MUST use frappe.db.sql() with:
  - season_seq in every WHERE clause (partition pruning)
  - UUID_TO_BIN() for item_id writes, BIN_TO_UUID() for reads
  See setup.py for full schema reference and safety rules.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import frappe

from memora_admin.api.utils import (
	get_player_season_seq as _get_player_season_seq,
)
from memora_admin.api.utils import (
	update_mastery_counters as _update_mastery_counters,
)
from memora_admin.utils.redis_connection import get_memora_redis


@frappe.whitelist(allow_guest=False)
def get_review_overview(player_id: str) -> list[dict]:
	"""Get count of due reviews per subject for a player.

	Counts items (each Memory State row = 1 item) with season_seq for partition pruning.
	Orphan filtering is skipped for counts (fsrs_processor validates stage existence on insert).

	Returns: [{"subject": "SUBJ-00001", "due_count": 15}, ...]
	"""
	today = frappe.utils.today()  # Returns 'YYYY-MM-DD' string
	season_seq = _get_player_season_seq(player_id)
	return frappe.db.sql(
		"""
		SELECT ms.subject, COUNT(*) as due_count
		FROM `tabMemora Memory State` ms
		WHERE ms.player = %(player)s
		  AND ms.next_review <= %(today)s
		  AND ms.season_seq = %(season_seq)s
		GROUP BY ms.subject
		""",
		{"player": player_id, "today": today, "season_seq": season_seq},
		as_dict=True,
	)


@frappe.whitelist(allow_guest=False)
def get_due_items(player_id: str, subject_id: str, limit: int = 0) -> dict:
	"""Get up to N due items for a subject, oldest first (FIFO).

	Returns items with their stage context (stage_id, lesson, stage_type).
	Uses BIN_TO_UUID polyfill to convert BINARY(16) item_id to string UUID.
	Includes season_seq for partition pruning.

	When limit=0, reads review_session_size from Memora Settings (default 10).

	Returns: {"items": [...], "has_more": bool}
	"""
	limit = int(limit)
	if limit <= 0:
		limit = frappe.db.get_single_value("Memora Settings", "review_session_size") or 10
		limit = int(limit)
	today = frappe.utils.today()
	season_seq = _get_player_season_seq(player_id)

	rows = frappe.db.sql(
		"""
		SELECT ms.name as memory_state_name,
		       BIN_TO_UUID(ms.item_id) as item_id,
		       ms.stage_id, ms.lesson,
		       ms.next_review,
		       ls.stage_type,
		       ri.question_text,
		       ri.choice_1,
		       ri.choice_2,
		       ri.choice_3,
		       ri.choice_4,
		       ri.correct_choice,
		       ri.content_json
		FROM `tabMemora Memory State` ms
		INNER JOIN `tabMemora Lesson Stage` ls
			ON ls.name = ms.stage_id AND ls.parent = ms.lesson
		LEFT JOIN `tabMemora Review Item` ri
			ON ri.name = BIN_TO_UUID(ms.item_id)
		WHERE ms.player = %(player)s
		  AND ms.subject = %(subject)s
		  AND ms.next_review <= %(today)s
		  AND ms.season_seq = %(season_seq)s
		ORDER BY ms.next_review ASC
		LIMIT %(fetch_limit)s
		""",
		{
			"player": player_id,
			"subject": subject_id,
			"today": today,
			"season_seq": season_seq,
			"fetch_limit": limit + 1,
		},
		as_dict=True,
	)

	has_more = len(rows) > limit

	result = []
	for row in rows[:limit]:
		# Assemble non-empty choices into a list
		choices = [c for c in (row.choice_1, row.choice_2, row.choice_3, row.choice_4) if c]

		# Parse content_json if present
		content_json = None
		if row.content_json:
			try:
				content_json = (
					json.loads(row.content_json) if isinstance(row.content_json, str) else row.content_json
				)
			except (json.JSONDecodeError, TypeError):
				content_json = None

		result.append(
			{
				"item_id": row.item_id,
				"stage_id": row.stage_id,
				"lesson_id": row.lesson,
				"stage_type": row.stage_type,
				"question_text": row.question_text,
				"choices": choices,
				"correct_choice": row.correct_choice,
				"content_json": content_json,
			}
		)

	return {"items": result, "has_more": has_more}


@frappe.whitelist(allow_guest=False)
def submit_reviews(player_id: str, subject_id: str, items: str) -> dict:
	"""Accept batch review results at item level and update Memory State with FSRS.

	Uses batched queries to avoid N+1 pattern on the 10B-row partitioned table:
	  1 batch SELECT (all items) + 1 batch UPDATE (CASE-based) + 1 remaining COUNT = 3 queries.

	Args:
		player_id: Player identifier
		subject_id: Subject identifier
		items: JSON string of reviewed items, each with:
			- item_id (str): UUID string
			- fail_count (int): Number of errors (0=Good, 1=Hard, 2+=Again)

	Returns: {"processed": int, "remaining_due": int, "has_more": bool}
	"""
	from fsrs import Card, Rating, State

	if isinstance(items, str):
		items_list = json.loads(items)
	else:
		items_list = items

	if not items_list:
		today = frappe.utils.today()
		season_seq = _get_player_season_seq(player_id)
		remaining_result = frappe.db.sql(
			"""
			SELECT COUNT(*) as cnt
			FROM `tabMemora Memory State` ms
			WHERE ms.player = %(player)s
			  AND ms.subject = %(subject)s
			  AND ms.next_review <= %(today)s
			  AND ms.season_seq = %(season_seq)s
			""",
			{"player": player_id, "subject": subject_id, "today": today, "season_seq": season_seq},
		)
		remaining_due = remaining_result[0][0] if remaining_result else 0
		return {"processed": 0, "remaining_due": remaining_due, "has_more": remaining_due > 0}

	scheduler = _get_fsrs_scheduler()
	now = datetime.now(timezone.utc)
	season_seq = _get_player_season_seq(player_id)

	# --- Query 1: Batch SELECT all items in one round-trip ---
	select_params = {"player": player_id, "season_seq": season_seq}
	in_parts = []
	for i, item_data in enumerate(items_list):
		key = f"id_{i}"
		select_params[key] = item_data["item_id"]
		in_parts.append(f"UUID_TO_BIN(%({key})s)")

	rows = frappe.db.sql(
		f"""
		SELECT name, BIN_TO_UUID(item_id) as item_id, stability, difficulty,
		       next_review, state, step, last_review
		FROM `tabMemora Memory State`
		WHERE player = %(player)s
		  AND season_seq = %(season_seq)s
		  AND item_id IN ({", ".join(in_parts)})
		""",
		select_params,
		as_dict=True,
	)

	# Index by item_id for O(1) lookup (order-independent)
	state_by_item = {row.item_id: row for row in rows}

	# --- FSRS computation per item (unchanged logic) ---
	# Collect (name, stability, difficulty, next_review, state, step, last_review) tuples
	updates = []
	tomorrow = date.today() + timedelta(days=1)

	for item_data in items_list:
		item_id = item_data["item_id"]
		fail_count = item_data.get("fail_count", 0)

		ms = state_by_item.get(item_id)
		if not ms:
			continue

		# Reconstruct FSRS Card from stored state
		card = Card()
		if ms.stability and ms.stability > 0:
			card.stability = ms.stability
			card.difficulty = ms.difficulty
			if ms.next_review:
				if isinstance(ms.next_review, date) and not isinstance(ms.next_review, datetime):
					card.due = datetime.combine(ms.next_review, time.min, tzinfo=timezone.utc)
				else:
					card.due = ms.next_review
			else:
				card.due = now

			# Restore state and step unconditionally (step=None for Review cards)
			card.state = State(int(ms.state)) if ms.state is not None else State.Learning
			card.step = int(ms.step) if ms.step is not None else None
			# Restore last_review (NULL = never reviewed)
			if ms.last_review is not None:
				lr = ms.last_review
				if lr.tzinfo is None:
					lr = lr.replace(tzinfo=timezone.utc)
				card.last_review = lr

		# Map fail_count to FSRS rating
		if fail_count == 0:
			rating = Rating.Good
		elif fail_count == 1:
			rating = Rating.Hard
		else:
			rating = Rating.Again

		card, _log = scheduler.review_card(card, rating, now)

		# Clamp next_review to date-only (midnight), minimum tomorrow
		next_date = card.due.date()
		if next_date < tomorrow:
			next_date = tomorrow
		max_date = date.today() + timedelta(days=90)
		if next_date > max_date:
			next_date = max_date

		card_last_review = card.last_review.replace(tzinfo=None) if card.last_review else None

		updates.append(
			{
				"name": ms.name,
				"old_stability": ms.stability,
				"stability": card.stability,
				"difficulty": card.difficulty,
				"next_review": next_date,
				"state": card.state.value,
				"step": card.step,
				"last_review": card_last_review,
			}
		)

	# --- Query 2: Batch UPDATE via CASE expressions (1 query instead of N) ---
	processed = len(updates)
	if processed > 0:
		update_params = {"season_seq": season_seq}
		case_stability = []
		case_difficulty = []
		case_next_review = []
		case_state = []
		case_step = []
		case_last_review = []
		name_list = []

		for i, u in enumerate(updates):
			nk = f"n_{i}"
			update_params[nk] = u["name"]
			name_list.append(f"%({nk})s")

			update_params[f"s_{i}"] = u["stability"]
			case_stability.append(f"WHEN %({nk})s THEN %(s_{i})s")

			update_params[f"d_{i}"] = u["difficulty"]
			case_difficulty.append(f"WHEN %({nk})s THEN %(d_{i})s")

			update_params[f"nr_{i}"] = u["next_review"]
			case_next_review.append(f"WHEN %({nk})s THEN %(nr_{i})s")

			update_params[f"st_{i}"] = u["state"]
			case_state.append(f"WHEN %({nk})s THEN %(st_{i})s")

			update_params[f"stp_{i}"] = u["step"]
			case_step.append(f"WHEN %({nk})s THEN %(stp_{i})s")

			update_params[f"lr_{i}"] = u["last_review"]
			case_last_review.append(f"WHEN %({nk})s THEN %(lr_{i})s")

		frappe.db.sql(
			f"""
			UPDATE `tabMemora Memory State`
			SET stability   = CASE name {" ".join(case_stability)} END,
			    difficulty   = CASE name {" ".join(case_difficulty)} END,
			    next_review  = CASE name {" ".join(case_next_review)} END,
			    state        = CASE name {" ".join(case_state)} END,
			    step         = CASE name {" ".join(case_step)} END,
			    last_review  = CASE name {" ".join(case_last_review)} END,
			    modified     = NOW(6)
			WHERE name IN ({", ".join(name_list)})
			  AND season_seq = %(season_seq)s
			""",
			update_params,
		)
		frappe.db.commit()

		# --- Update mastery counters in Redis ---
		try:
			r = get_memora_redis()
			for u in updates:
				_update_mastery_counters(
					r,
					player_id,
					subject_id,
					season_seq,
					u["old_stability"],
					u["stability"],
				)
		except Exception:
			pass  # Best-effort; counters self-heal on next read

	# --- Query 3: Remaining due count ---
	today = frappe.utils.today()
	remaining_result = frappe.db.sql(
		"""
		SELECT COUNT(*) as cnt
		FROM `tabMemora Memory State` ms
		WHERE ms.player = %(player)s
		  AND ms.subject = %(subject)s
		  AND ms.next_review <= %(today)s
		  AND ms.season_seq = %(season_seq)s
		""",
		{"player": player_id, "subject": subject_id, "today": today, "season_seq": season_seq},
	)
	remaining_due = remaining_result[0][0] if remaining_result else 0

	return {
		"processed": processed,
		"remaining_due": remaining_due,
		"has_more": remaining_due > 0,
	}


def _get_fsrs_scheduler():
	"""Create FSRS scheduler with default weights and 90-day cap."""
	from fsrs import Scheduler

	return Scheduler(maximum_interval=90)
