"""Frappe API for Official Exam operations.

Called by FastAPI ExamService via FrappeClient for DB operations
on raw SQL tables (tabMemora Exam Attempt, tabMemora Exam Attempt Detail).
"""

import frappe
from frappe.utils import now_datetime


@frappe.whitelist(allow_guest=False)
def get_player_exam_stats(player_id: str, exam_ids: list | str) -> dict:
	"""Get attempt stats for a player across multiple exams.

	Args:
	    player_id: Player's user ID
	    exam_ids: List of exam IDs (or JSON-encoded string)

	Returns:
	    Dict keyed by exam_id with attempt stats
	"""
	import json

	if isinstance(exam_ids, str):
		exam_ids = json.loads(exam_ids)

	if not exam_ids:
		return {}

	placeholders = ", ".join(["%s"] * len(exam_ids))
	rows = frappe.db.sql(
		f"""
		SELECT exam_id, attempt_count, best_score, best_total
		FROM `tabMemora Exam Attempt`
		WHERE player_id = %s AND exam_id IN ({placeholders})
		""",
		[player_id, *exam_ids],
		as_dict=True,
	)

	return {
		row["exam_id"]: {
			"attempt_count": row["attempt_count"],
			"best_score": row["best_score"],
			"best_total": row["best_total"],
		}
		for row in rows
	}


@frappe.whitelist(allow_guest=False)
def get_last_attempt_details(player_id: str, exam_id: str) -> list:
	"""Get per-question results from the player's last attempt.

	Returns:
	    List of dicts with question_idx and is_correct
	"""
	rows = frappe.db.sql(
		"""
		SELECT question_idx, is_correct
		FROM `tabMemora Exam Attempt Detail`
		WHERE player_id = %s AND exam_id = %s
		ORDER BY question_idx
		""",
		[player_id, exam_id],
		as_dict=True,
	)

	return [
		{"question_idx": row["question_idx"], "is_correct": bool(row["is_correct"])}
		for row in rows
	]


@frappe.whitelist(allow_guest=False)
def submit_exam_attempt(
	player_id: str,
	exam_id: str,
	score: int,
	total: int,
	results: list | str,
) -> dict:
	"""Record exam attempt results.

	All operations in a single transaction:
	1. UPSERT tabMemora Exam Attempt (increment count, GREATEST for best_score)
	2. DELETE existing detail rows for (player_id, exam_id)
	3. INSERT new detail rows

	Args:
	    player_id: Player's user ID
	    exam_id: Exam document name
	    score: Number of correct answers
	    total: Total number of questions
	    results: List of {question_idx, is_correct}

	Returns:
	    Updated attempt stats
	"""
	import json

	if isinstance(results, str):
		results = json.loads(results)

	score = int(score)
	total = int(total)
	now = now_datetime()

	# 0. Fetch current state for is_new_best comparison and retry debounce
	old_row = frappe.db.sql(
		"""
		SELECT attempt_count, best_score, best_total, last_attempt_at
		FROM `tabMemora Exam Attempt`
		WHERE player_id = %s AND exam_id = %s
		""",
		[player_id, exam_id],
		as_dict=True,
	)

	if old_row:
		old = old_row[0]
		time_since_last = (now - old["last_attempt_at"]).total_seconds()
		# Debounce: if last attempt was <10s ago, treat as client retry
		if time_since_last < 10:
			return {
				"attempt_count": old["attempt_count"],
				"best_score": max(old["best_score"], score),
				"best_total": old["best_total"],
				"is_new_best": score > old["best_score"],
			}
		old_best = old["best_score"]
	else:
		old_best = None

	# 1. UPSERT attempt aggregate
	frappe.db.sql(
		"""
		INSERT INTO `tabMemora Exam Attempt`
			(player_id, exam_id, attempt_count, best_score, best_total, last_attempt_at)
		VALUES (%s, %s, 1, %s, %s, %s)
		ON DUPLICATE KEY UPDATE
			attempt_count = attempt_count + 1,
			best_score = GREATEST(best_score, VALUES(best_score)),
			best_total = VALUES(best_total),
			last_attempt_at = VALUES(last_attempt_at)
		""",
		[player_id, exam_id, score, total, now],
	)

	# 2. DELETE old detail rows
	frappe.db.sql(
		"""
		DELETE FROM `tabMemora Exam Attempt Detail`
		WHERE player_id = %s AND exam_id = %s
		""",
		[player_id, exam_id],
	)

	# 3. INSERT new detail rows
	if results:
		values = []
		for r in results:
			values.append(
				(player_id, exam_id, int(r["question_idx"]), int(r["is_correct"]))
			)

		placeholders = ", ".join(["(%s, %s, %s, %s)"] * len(values))
		flat_params = [v for tup in values for v in tup]

		frappe.db.sql(
			f"""
			INSERT INTO `tabMemora Exam Attempt Detail`
				(player_id, exam_id, question_idx, is_correct)
			VALUES {placeholders}
			""",
			flat_params,
		)

	# Fetch post-UPSERT state
	row = frappe.db.sql(
		"""
		SELECT attempt_count, best_score, best_total
		FROM `tabMemora Exam Attempt`
		WHERE player_id = %s AND exam_id = %s
		""",
		[player_id, exam_id],
		as_dict=True,
	)

	if row:
		return {
			"attempt_count": row[0]["attempt_count"],
			"best_score": row[0]["best_score"],
			"best_total": row[0]["best_total"],
			"is_new_best": old_best is None or score > old_best,
		}

	return {"attempt_count": 1, "best_score": score, "best_total": total, "is_new_best": True}
