"""Frappe whitelisted API for FSRS review operations.

Provides three endpoints:
1. get_review_overview - Due review counts per subject for a player (item-level)
2. get_due_items - Due items for a specific subject (FIFO order)
3. submit_reviews - Batch submit review results with inline FSRS computation (item-level)

All queries include season_seq for partition pruning on the Memory State table.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import frappe


def _get_active_season_seq() -> int:
	"""Get the active season's sequence number for partition pruning."""
	today = date.today()
	result = frappe.db.get_value(
		"Memora Season",
		{"is_published": 1, "start_date": ["<=", today], "end_date": [">=", today]},
		"season_seq",
	)
	return int(result) if result else 1


@frappe.whitelist(allow_guest=False)
def get_review_overview(player_id: str) -> list[dict]:
	"""Get count of due reviews per subject for a player.

	Counts items (each Memory State row = 1 item) with season_seq for partition pruning.
	Only counts items whose stage still exists in the parent lesson
	(INNER JOIN with Memora Lesson Stage).

	Returns: [{"subject": "SUBJ-00001", "due_count": 15}, ...]
	"""
	today = frappe.utils.today()  # Returns 'YYYY-MM-DD' string
	season_seq = _get_active_season_seq()
	return frappe.db.sql(
		"""
		SELECT ms.subject, COUNT(*) as due_count
		FROM `tabMemora Memory State` ms
		INNER JOIN `tabMemora Lesson Stage` ls
			ON ls.name = ms.stage_id AND ls.parent = ms.lesson
		WHERE ms.player = %(player)s
		  AND ms.next_review <= %(today)s
		  AND ms.season_seq = %(season_seq)s
		GROUP BY ms.subject
		""",
		{"player": player_id, "today": today, "season_seq": season_seq},
		as_dict=True,
	)


@frappe.whitelist(allow_guest=False)
def get_due_items(player_id: str, subject_id: str, limit: int = 10) -> dict:
	"""Get up to N due items for a subject, oldest first (FIFO).

	Returns items with their stage context (stage_id, lesson, stage_type).
	Uses BIN_TO_UUID polyfill to convert BINARY(16) item_id to string UUID.
	Includes season_seq for partition pruning.

	Returns: {"items": [...], "has_more": bool}
	"""
	limit = int(limit)
	today = frappe.utils.today()
	season_seq = _get_active_season_seq()

	rows = frappe.db.sql(
		"""
		SELECT ms.name as memory_state_name,
		       BIN_TO_UUID(ms.item_id) as item_id,
		       ms.stage_id, ms.lesson,
		       ms.stability, ms.difficulty, ms.next_review,
		       ls.stage_type
		FROM `tabMemora Memory State` ms
		INNER JOIN `tabMemora Lesson Stage` ls
			ON ls.name = ms.stage_id AND ls.parent = ms.lesson
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

	result = [
		{
			"item_id": row.item_id,
			"stage_id": row.stage_id,
			"lesson_id": row.lesson,
			"stage_type": row.stage_type,
			"stability": row.stability,
			"difficulty": row.difficulty,
		}
		for row in rows[:limit]
	]

	return {"items": result, "has_more": has_more}


@frappe.whitelist(allow_guest=False)
def submit_reviews(player_id: str, subject_id: str, items: str) -> dict:
	"""Accept batch review results at item level and update Memory State with FSRS.

	Args:
		player_id: Player identifier
		subject_id: Subject identifier
		items: JSON string of reviewed items, each with:
			- item_id (str): UUID string
			- fail_count (int): Number of errors (0=Good, 1=Hard, 2+=Again)

	Returns: {"processed": int, "remaining_due": int, "has_more": bool}
	"""
	from fsrs import Card, Rating

	if isinstance(items, str):
		items_list = json.loads(items)
	else:
		items_list = items

	scheduler = _get_fsrs_scheduler()
	processed = 0
	now = datetime.now(timezone.utc)
	season_seq = _get_active_season_seq()

	for item_data in items_list:
		item_id = item_data["item_id"]
		fail_count = item_data.get("fail_count", 0)

		# Look up existing Memory State by (player, item_id, season_seq)
		memory_state = frappe.db.sql(
			"""
			SELECT name, stability, difficulty, next_review
			FROM `tabMemora Memory State`
			WHERE player = %(player)s
			  AND item_id = UUID_TO_BIN(%(item_id)s)
			  AND season_seq = %(season_seq)s
			LIMIT 1
			""",
			{
				"player": player_id,
				"item_id": item_id,
				"season_seq": season_seq,
			},
			as_dict=True,
		)

		if not memory_state:
			continue

		ms = memory_state[0]

		# Reconstruct FSRS Card from stored state
		card = Card()
		card.stability = ms.stability or 0
		card.difficulty = ms.difficulty or 0
		card.due = ms.next_review if ms.next_review else now

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
		tomorrow = date.today() + timedelta(days=1)
		if next_date < tomorrow:
			next_date = tomorrow
		next_review_date = next_date

		# Update via raw SQL (partition-aware)
		frappe.db.sql(
			"""
			UPDATE `tabMemora Memory State`
			SET stability = %(stability)s,
			    difficulty = %(difficulty)s,
			    next_review = %(next_review)s,
			    modified = NOW(6)
			WHERE name = %(name)s
			  AND season_seq = %(season_seq)s
			""",
			{
				"name": ms.name,
				"season_seq": season_seq,
				"stability": card.stability,
				"difficulty": card.difficulty,
				"next_review": next_review_date,
			},
		)

		processed += 1

	if processed > 0:
		frappe.db.commit()

	# Remaining due count (items whose stage still exists)
	today = frappe.utils.today()
	remaining_result = frappe.db.sql(
		"""
		SELECT COUNT(*) as cnt
		FROM `tabMemora Memory State` ms
		INNER JOIN `tabMemora Lesson Stage` ls
			ON ls.name = ms.stage_id AND ls.parent = ms.lesson
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
	"""Create FSRS scheduler with weights from Memora Settings.

	Returns:
		fsrs.Scheduler instance configured with admin weights (if set).
	"""
	from fsrs import Scheduler

	settings = frappe.get_single("Memora Settings")
	weights_str = settings.fsrs_weights

	if weights_str and weights_str.strip():
		try:
			weights = json.loads(weights_str)
			return Scheduler(parameters=weights)
		except (json.JSONDecodeError, ValueError, TypeError):
			pass

	return Scheduler()
