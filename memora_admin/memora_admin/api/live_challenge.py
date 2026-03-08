"""Frappe whitelist APIs for Live Challenge administration."""

import json

import frappe
from frappe.utils import now_datetime

# Mapping correct_choice (1-4) to letter (A-D)
_CHOICE_MAP = {1: "A", 2: "B", 3: "C", 4: "D"}


@frappe.whitelist(allow_guest=False)
def get_dashboard(event_id: str) -> dict:
	"""Get admin dashboard data for a live challenge event.

	For Active events: real-time counters and time remaining.
	For Ended events: aggregate statistics and full leaderboard.

	Args:
		event_id: Live Challenge Event name (e.g., LC-00001)

	Returns:
		dict with status-dependent dashboard data
	"""
	event = frappe.get_doc("Memora Live Challenge Event", event_id)

	if event.status == "Active":
		participant_count = event.participant_count or 0
		submitted_count = event.submitted_count or 0
		still_taking_count = max(0, participant_count - submitted_count)

		time_remaining = 0
		if event.exam_end_ts:
			now = now_datetime()
			diff = (event.exam_end_ts - now).total_seconds()
			time_remaining = max(0, int(diff))

		return {
			"status": "Active",
			"participant_count": participant_count,
			"submitted_count": submitted_count,
			"still_taking_count": still_taking_count,
			"time_remaining": time_remaining,
			"exam_end_ts": str(event.exam_end_ts) if event.exam_end_ts else None,
		}

	if event.status == "Ended":
		participant_count = event.participant_count or 0
		submitted_count = event.submitted_count or 0
		completion_rate = round((submitted_count / participant_count) * 100, 1) if participant_count else 0.0

		# Aggregate stats from Participation records
		stats = frappe.db.sql(
			"""
			SELECT
				AVG(score) as average_score,
				MAX(score) as highest_score
			FROM `tabMemora Live Challenge Participation`
			WHERE event = %s AND submitted_at IS NOT NULL
			""",
			(event_id,),
			as_dict=True,
		)

		average_score = round(float(stats[0].average_score or 0), 1) if stats else 0.0
		highest_score = round(float(stats[0].highest_score or 0), 1) if stats else 0.0

		leaderboard = []
		if event.leaderboard_json:
			try:
				leaderboard = json.loads(event.leaderboard_json)
			except (json.JSONDecodeError, TypeError):
				pass

		return {
			"status": "Ended",
			"participant_count": participant_count,
			"submitted_count": submitted_count,
			"completion_rate": completion_rate,
			"average_score": average_score,
			"highest_score": highest_score,
			"leaderboard": leaderboard,
		}

	# For Draft/Waiting, return basic status info
	return {
		"status": event.status,
		"participant_count": event.participant_count or 0,
		"submitted_count": event.submitted_count or 0,
	}


@frappe.whitelist(allow_guest=False)
def import_review_items(event_id: str, review_item_ids: str | list) -> dict:
	"""Import questions from Memora Review Items into an event's child table.

	Args:
		event_id: Live Challenge Event name (e.g., LC-00001)
		review_item_ids: List of Review Item names or JSON string

	Returns:
		dict with imported_count and questions list
	"""
	if isinstance(review_item_ids, str):
		try:
			review_item_ids = json.loads(review_item_ids)
		except (json.JSONDecodeError, TypeError):
			frappe.throw("review_item_ids must be a list or JSON array.")

	if not review_item_ids:
		frappe.throw("No Review Item IDs provided.")

	event = frappe.get_doc("Memora Live Challenge Event", event_id)

	if event.status != "Draft":
		frappe.throw("Questions can only be imported when the event is in Draft status.")

	items = frappe.get_all(
		"Memora Review Item",
		filters={"name": ["in", review_item_ids]},
		fields=["name", "question_text", "choice_1", "choice_2", "choice_3", "choice_4", "correct_choice"],
	)

	if not items:
		frappe.throw("No valid Review Items found for the given IDs.")

	imported = []
	for item in items:
		correct_choice = int(item.correct_choice or 0)
		correct_answer = _CHOICE_MAP.get(correct_choice, "A")

		event.append(
			"questions",
			{
				"question_text": item.question_text,
				"option_a": item.choice_1,
				"option_b": item.choice_2,
				"option_c": item.choice_3,
				"option_d": item.choice_4,
				"correct_answer": correct_answer,
				"source_review_item": item.name,
			},
		)
		imported.append(item.name)

	event.save(ignore_permissions=True)

	return {
		"imported_count": len(imported),
		"questions": [
			{
				"question_text": q.question_text,
				"option_a": q.option_a,
				"option_b": q.option_b,
				"option_c": q.option_c,
				"option_d": q.option_d,
				"correct_answer": q.correct_answer,
			}
			for q in event.questions[-len(imported) :]
		],
	}
