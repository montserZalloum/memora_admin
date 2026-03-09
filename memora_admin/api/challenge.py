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
def get_challenge_settings() -> dict:
	"""Get Challenge Hub settings from Memora Settings singleton.

	Called by ChallengeService._get_challenge_settings() on cache miss.

	Returns dict with xp_per_question, pass_threshold, lb_top_count, lb_refresh_interval.
	"""
	settings = frappe.get_single("Memora Settings")

	return {
		"xp_per_question": settings.challenge_xp_per_question
		if settings.challenge_xp_per_question is not None
		else 5,
		"pass_threshold": settings.challenge_pass_threshold
		if settings.challenge_pass_threshold is not None
		else 50,
		"lb_top_count": settings.challenge_lb_top_count
		if settings.challenge_lb_top_count is not None
		else 20,
		"lb_refresh_interval": settings.challenge_lb_refresh_interval
		if settings.challenge_lb_refresh_interval is not None
		else 300,
	}


@frappe.whitelist(allow_guest=False)
def get_topic_question_items(topic_id: str) -> list[dict]:
	"""Get Review Item records for a topic's MCQ questions.

	Returns item_id, lesson, stage_id for FSRS interaction push.
	Called by ChallengeService._get_question_lookup().
	"""
	records = frappe.get_all(
		"Memora Review Item",
		filters={"topic": topic_id},
		fields=["item_id", "lesson", "stage_id", "correct_choice"],
	)
	return records
