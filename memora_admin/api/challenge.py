"""Frappe API endpoints for Challenge Hub operations."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_player_challenge_progress(player_id: str, subject_id: str) -> list[dict]:
	"""Load Challenge Progress records for a player + subject in the current season.

	Called by ChallengeService.ensure_hydrated() on Redis cache miss.
	Filters by the player's active season to prevent archived old-season
	progress from being hydrated into the current session.

	Returns:
		List of dicts with topic, stamped, best_correct, best_score_pct,
		best_passing_pct, total_xp_earned, attempt_count fields.
	"""
	# Resolve the player's current active season
	season = frappe.db.get_value("Memora Player Profile", player_id, "season")
	if not season:
		return []

	records = frappe.get_all(
		"Memora Challenge Progress",
		filters={"player": player_id, "subject": subject_id, "season": season},
		fields=[
			"topic",
			"stamped",
			"best_correct",
			"best_score_pct",
			"best_passing_pct",
			"total_xp_earned",
			"attempt_count",
		],
	)
	return records


@frappe.whitelist(allow_guest=False)
def get_player_challenge_progress_bulk(player_id: str, subject_ids: str) -> dict[str, list[dict]]:
	"""Load Challenge Progress records for a player across multiple subjects in one call.

	Called by ChallengeService.ensure_hydrated_bulk() on Redis cache miss.
	Returns dict mapping subject_id → list of topic progress records.
	subject_ids is a JSON-encoded list of subject ID strings.
	"""
	import json as _json

	if isinstance(subject_ids, str):
		try:
			parsed_ids = _json.loads(subject_ids)
		except (ValueError, TypeError):
			frappe.throw("subject_ids must be a valid JSON array of strings", frappe.InvalidRequestError)
			return {}
	else:
		parsed_ids = subject_ids
	if not parsed_ids or not isinstance(parsed_ids, list):
		return {}

	season = frappe.db.get_value("Memora Player Profile", player_id, "season")
	if not season:
		return {}

	records = frappe.get_all(
		"Memora Challenge Progress",
		filters={"player": player_id, "subject": ["in", parsed_ids], "season": season},
		fields=[
			"subject",
			"topic",
			"stamped",
			"best_correct",
			"best_score_pct",
			"best_passing_pct",
			"total_xp_earned",
			"attempt_count",
		],
	)

	result: dict[str, list[dict]] = {sid: [] for sid in parsed_ids}
	for rec in records:
		sid = rec.pop("subject")
		if sid in result:
			result[sid].append(rec)
	return result


@frappe.whitelist(allow_guest=False)
def get_challenge_settings() -> dict:
	"""Get Challenge Hub settings from Memora Settings singleton.

	Called by ChallengeService._get_challenge_settings() on cache miss.

	Returns dict with xp_per_question, pass_threshold.
	"""
	settings = frappe.get_single("Memora Settings")

	return {
		"xp_per_question": settings.challenge_xp_per_question
		if settings.challenge_xp_per_question is not None
		else 5,
		"pass_threshold": settings.challenge_pass_threshold
		if settings.challenge_pass_threshold is not None
		else 50,
	}


@frappe.whitelist(allow_guest=False)
def get_topic_question_items(topic_id: str) -> list[dict]:
	"""Get Review Item records for a topic's MCQ questions.

	Returns item_id, lesson, correct_choice for grading and FSRS interaction push.
	Called by ChallengeService._get_question_lookup().
	"""
	records = frappe.get_all(
		"Memora Review Item",
		filters={"topic": topic_id},
		fields=["item_id", "lesson", "correct_choice"],
	)
	return records
