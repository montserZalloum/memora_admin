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

		result = {
			"status": "Active",
			"mode": getattr(event, "mode", None) or "exam",
			"participant_count": participant_count,
			"submitted_count": submitted_count,
			"still_taking_count": still_taking_count,
			"time_remaining": time_remaining,
			"exam_end_ts": str(event.exam_end_ts) if event.exam_end_ts else None,
		}

		# Last Stand: live stats from Redis
		mode = getattr(event, "mode", None) or "exam"
		if mode == "last_stand":
			from fastapi_app.core.redis_keys import (
				lc_alive_key,
				lc_eliminated_key,
				lc_round_key,
			)
			from memora_admin.utils.redis_connection import get_memora_redis

			r = get_memora_redis()
			alive_count = r.scard(lc_alive_key(event_id))
			eliminated_count = r.scard(lc_eliminated_key(event_id))
			round_data = r.hgetall(lc_round_key(event_id))
			# Decode bytes if needed
			if round_data:
				round_data = {
					(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
					for k, v in round_data.items()
				}
			result["alive_count"] = alive_count or 0
			result["eliminated_count"] = eliminated_count or 0
			result["current_round"] = int(round_data.get("question_idx", "0")) if round_data else 0
			result["total_rounds"] = len(event.questions) if hasattr(event, "questions") else 0

		return result

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
def get_full_leaderboard(event_id: str) -> list:
	"""Return the full ranked leaderboard for an ended event.

	Args:
		event_id: Live Challenge Event name

	Returns:
		List of dicts with rank, player, display_name, score for every
		submitted participant, ordered by rank ascending.
	"""
	event = frappe.get_doc("Memora Live Challenge Event", event_id)
	if not event.leaderboard_json:
		frappe.throw("Leaderboard is not available yet for this event.")

	rows = frappe.db.sql(
		"""
		SELECT p.player, p.score, pp.display_name
		FROM `tabMemora Live Challenge Participation` p
		LEFT JOIN `tabMemora Player Profile` pp ON pp.name = p.player
		WHERE p.event = %s AND p.submitted_at IS NOT NULL
		ORDER BY p.score DESC
		""",
		(event_id,),
		as_dict=True,
	)

	# Compute standard competition ranking in Python (rank column may be 0)
	result = []
	for i, r in enumerate(rows):
		score = round(float(r.score or 0), 1)
		if i == 0 or score < round(float(rows[i - 1].score or 0), 1):
			current_rank = i + 1
		result.append({
			"rank": current_rank,
			"player": r.player,
			"display_name": r.display_name or r.player,
			"score": score,
		})

	return result


@frappe.whitelist(allow_guest=False)
def get_live_participants(event_id: str) -> dict:
	"""Return real-time participant data from Redis for an Active event.

	Args:
		event_id: Live Challenge Event name

	Returns:
		dict with joined_count, submitted_count, and participants list.
	"""
	event = frappe.get_doc("Memora Live Challenge Event", event_id)
	if event.status != "Active":
		return {"ended": True, "status": event.status}

	from fastapi_app.core.redis_keys import (
		lc_join_times_key,
		lc_joined_key,
		lc_results_key,
		lc_submitted_key,
	)
	from memora_admin.utils.redis_connection import get_memora_redis

	r = get_memora_redis()

	joined_raw = r.smembers(lc_joined_key(event_id))
	submitted_raw = r.smembers(lc_submitted_key(event_id))
	join_times_raw = r.hgetall(lc_join_times_key(event_id))
	results_raw = r.hgetall(lc_results_key(event_id))

	def _dec(val):
		return val.decode() if isinstance(val, bytes) else val

	joined_ids = {_dec(p) for p in joined_raw} if joined_raw else set()
	submitted_ids = {_dec(p) for p in submitted_raw} if submitted_raw else set()
	join_times = {_dec(k): _dec(v) for k, v in join_times_raw.items()} if join_times_raw else {}
	results = {}
	for k, v in (results_raw or {}).items():
		pid = _dec(k)
		try:
			results[pid] = json.loads(_dec(v))
		except (json.JSONDecodeError, TypeError):
			pass

	# Batch-resolve display names
	player_ids = list(joined_ids)
	display_names = {}
	if player_ids:
		profiles = frappe.get_all(
			"Memora Player Profile",
			filters={"name": ["in", player_ids]},
			fields=["name", "display_name"],
			limit_page_length=0,
		)
		display_names = {p["name"]: p["display_name"] for p in profiles if p.get("display_name")}

	# Build participant list — submitted first (sorted by score desc), then joined-only
	submitted_list = []
	taking_list = []
	for pid in joined_ids:
		entry = {
			"player": pid,
			"display_name": display_names.get(pid, pid),
			"joined_at": join_times.get(pid, ""),
		}
		r_data = results.get(pid)
		if pid in submitted_ids and r_data:
			entry["status"] = "Submitted"
			entry["score"] = round(float(r_data.get("score", 0)), 1)
			entry["submitted_at"] = r_data.get("submitted_at", "")
			submitted_list.append(entry)
		else:
			entry["status"] = "Taking exam"
			entry["score"] = None
			entry["submitted_at"] = ""
			taking_list.append(entry)

	# Sort submitted by score descending
	submitted_list.sort(key=lambda x: x["score"], reverse=True)

	return {
		"joined_count": len(joined_ids),
		"submitted_count": len(submitted_ids),
		"still_taking": len(joined_ids) - len(submitted_ids),
		"participants": submitted_list + taking_list,
	}


@frappe.whitelist(allow_guest=False)
def reconcile_event_status(
	event_id: str, status: str, participant_count: int, submitted_count: int,
) -> dict:
	"""Post-event reconciliation: update status + counters without triggering validate.

	Called by FastAPI LiveChallengeService._reconcile_event() after writing
	participation records.  Uses frappe.db.set_value (raw SQL) to bypass
	DocType validation which rejects saves on events with short waiting rooms
	or other draft-time constraints.

	Args:
		event_id: Live Challenge Event name
		status: Target status (typically "Ended")
		participant_count: Total joined players
		submitted_count: Total submitted/graded players
	"""
	if not frappe.db.exists("Memora Live Challenge Event", event_id):
		frappe.throw(f"Event {event_id} not found.")

	frappe.db.set_value(
		"Memora Live Challenge Event",
		event_id,
		{
			"status": status,
			"participant_count": int(participant_count),
			"submitted_count": int(submitted_count),
		},
		update_modified=True,
	)
	frappe.db.commit()

	return {"ok": True, "event_id": event_id, "status": status}


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
