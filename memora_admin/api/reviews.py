"""Frappe whitelisted API for FSRS review operations.

Provides three endpoints:
1. get_review_overview - Due review counts per subject for a player
2. get_due_stages - Due stages for a specific subject (FIFO order)
3. submit_reviews - Batch submit review results with inline FSRS computation
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import frappe


@frappe.whitelist(allow_guest=False)
def get_review_overview(player_id: str) -> list[dict]:
	"""Get count of due reviews per subject for a player.

	Uses composite index on (player, subject, next_review).
	Returns: [{"subject": "SUBJ-00001", "due_count": 15}, ...]
	"""
	today = frappe.utils.today()  # Returns 'YYYY-MM-DD' string
	return frappe.db.sql(
		"""
		SELECT subject, COUNT(*) as due_count
		FROM `tabMemora Memory State`
		WHERE player = %(player)s
		  AND next_review <= %(today)s
		GROUP BY subject
		""",
		{"player": player_id, "today": today},
		as_dict=True,
	)


@frappe.whitelist(allow_guest=False)
def get_due_stages(player_id: str, subject_id: str, limit: int = 10) -> dict:
	"""Get up to N due stages for a subject, oldest first (FIFO).

	Validates that each stage still exists in its parent lesson
	(gracefully skips removed stages). Returns stage_type for client rendering.

	Returns: {"stages": [...], "has_more": bool}
	"""
	limit = int(limit)
	today = frappe.utils.today()

	# Fetch a few extra rows to account for removed stages being filtered out
	rows = frappe.db.sql(
		"""
		SELECT ms.name as memory_state_name, ms.stage_id, ms.lesson,
		       ms.stability, ms.difficulty, ms.next_review
		FROM `tabMemora Memory State` ms
		WHERE ms.player = %(player)s
		  AND ms.subject = %(subject)s
		  AND ms.next_review <= %(today)s
		ORDER BY ms.next_review ASC
		LIMIT %(fetch_limit)s
		""",
		{
			"player": player_id,
			"subject": subject_id,
			"today": today,
			"fetch_limit": limit + 5,
		},
		as_dict=True,
	)

	result = []
	for row in rows:
		if len(result) >= limit:
			break

		# Validate stage still exists in its lesson (gracefully skip removed stages)
		stage_info = frappe.db.get_value(
			"Memora Lesson Stage",
			{"parent": row.lesson, "stage_title": row.stage_id},
			["stage_type"],
			as_dict=True,
		)

		if stage_info:
			result.append(
				{
					"stage_id": row.stage_id,
					"lesson_id": row.lesson,
					"stage_type": stage_info.stage_type,
					"memory_state_name": row.memory_state_name,
					"stability": row.stability,
					"difficulty": row.difficulty,
				}
			)

	# Count total due for has_more indicator
	total_due = frappe.db.count(
		"Memora Memory State",
		{
			"player": player_id,
			"subject": subject_id,
			"next_review": ["<=", today],
		},
	)
	has_more = total_due > len(result)

	return {"stages": result, "has_more": has_more}


@frappe.whitelist(allow_guest=False)
def submit_reviews(player_id: str, subject_id: str, stages: str) -> dict:
	"""Accept batch review results and update Memory State with FSRS computation.

	Args:
		player_id: Player identifier
		subject_id: Subject identifier
		stages: JSON string of reviewed stages, each with:
			- stage_id (str): Stage identifier
			- fail_count (int): Number of errors (0=Good, 1=Hard, 2+=Again)

	Returns: {"processed": int, "remaining_due": int, "has_more": bool}
	"""
	from fsrs import Card, Rating

	if isinstance(stages, str):
		stages_list = json.loads(stages)
	else:
		stages_list = stages

	scheduler = _get_fsrs_scheduler()
	processed = 0
	now = datetime.now(timezone.utc)

	for stage_data in stages_list:
		stage_id = stage_data["stage_id"]
		fail_count = stage_data.get("fail_count", 0)

		# Look up existing Memory State
		memory_state = frappe.db.sql(
			"""
			SELECT name, stability, difficulty, next_review
			FROM `tabMemora Memory State`
			WHERE player = %(player)s
			  AND subject = %(subject)s
			  AND stage_id = %(stage_id)s
			LIMIT 1
			""",
			{
				"player": player_id,
				"subject": subject_id,
				"stage_id": stage_id,
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
		next_review_naive = datetime.combine(next_date, time.min)

		# Update Memory State record
		frappe.db.set_value(
			"Memora Memory State",
			ms.name,
			{
				"stability": card.stability,
				"difficulty": card.difficulty,
				"next_review": next_review_naive,
			},
			update_modified=True,
		)

		processed += 1

	if processed > 0:
		frappe.db.commit()

	# Return remaining due count for client
	today = frappe.utils.today()
	remaining_due = frappe.db.count(
		"Memora Memory State",
		{
			"player": player_id,
			"subject": subject_id,
			"next_review": ["<=", today],
		},
	)

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
