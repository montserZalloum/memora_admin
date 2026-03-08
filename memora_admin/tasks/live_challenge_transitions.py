"""Scheduled task for Live Challenge event state transitions.

Runs every 60 seconds. Handles:
1. Draft -> Waiting: when scheduled_start <= now
2. Waiting -> Active: when exam_start_ts <= now
3. Active -> Ended: when exam_end_ts <= now

On Draft -> Waiting: populates Redis keys (status, questions, meta, count).
On Active -> Ended: triggers post-event processing (leaderboard + XP).
"""

import json

import frappe
from frappe.utils import now_datetime

from fastapi_app.core.redis_keys import (
	LC_KEY_TTL,
	dirty_wallets_key,
	lc_count_key,
	lc_meta_key,
	lc_questions_key,
	lc_status_key,
	lc_submitted_key,
	wallet_key,
)
from memora_admin.utils.redis_connection import get_memora_redis


def process_live_challenge_transitions():
	"""Process all pending state transitions for live challenge events."""
	now = now_datetime()

	# 1. Draft -> Waiting: scheduled_start <= now
	draft_events = frappe.get_all(
		"Memora Live Challenge Event",
		filters={"status": "Draft", "scheduled_start": ["<=", now]},
		fields=["name"],
	)
	for ev in draft_events:
		try:
			_transition_to_waiting(ev.name)
		except Exception:
			frappe.log_error(title=f"LC transition Draft->Waiting failed: {ev.name}")

	# 2. Waiting -> Active: exam_start_ts <= now
	waiting_events = frappe.get_all(
		"Memora Live Challenge Event",
		filters={"status": "Waiting", "exam_start_ts": ["<=", now]},
		fields=["name"],
	)
	for ev in waiting_events:
		try:
			_transition_to_active(ev.name)
		except Exception:
			frappe.log_error(title=f"LC transition Waiting->Active failed: {ev.name}")

	# 3. Active -> Ended: exam_end_ts <= now
	active_events = frappe.get_all(
		"Memora Live Challenge Event",
		filters={"status": "Active", "exam_end_ts": ["<=", now]},
		fields=["name"],
	)
	for ev in active_events:
		try:
			_transition_to_ended(ev.name)
		except Exception:
			frappe.log_error(title=f"LC transition Active->Ended failed: {ev.name}")

	# 4. Finalize recently ended events (deferred post-event processing)
	ended_events = frappe.get_all(
		"Memora Live Challenge Event",
		filters={
			"status": "Ended",
			"leaderboard_json": ["is", "not set"],
		},
		fields=["name", "exam_end_ts"],
	)
	for ev in ended_events:
		try:
			_try_finalize_event(ev.name, ev.exam_end_ts)
		except Exception:
			frappe.log_error(title=f"LC finalization failed: {ev.name}")

	if draft_events or waiting_events or active_events or ended_events:
		frappe.db.commit()


def _transition_to_waiting(event_name: str):
	"""Transition event from Draft to Waiting and populate Redis keys."""
	event = frappe.get_doc("Memora Live Challenge Event", event_name)
	if event.status != "Draft":
		return  # Already transitioned

	# Populate Redis before changing status
	r = get_memora_redis()
	pipe = r.pipeline()

	# Status key
	pipe.set(lc_status_key(event_name), "waiting", ex=LC_KEY_TTL)

	# Questions JSON (with correct answers — NEVER served to client directly)
	questions = []
	for q in event.questions:
		questions.append(
			{
				"idx": q.idx - 1,  # 0-based for API
				"question_text": q.question_text,
				"option_a": q.option_a,
				"option_b": q.option_b,
				"option_c": q.option_c,
				"option_d": q.option_d,
				"correct_answer": q.correct_answer,
			}
		)
	pipe.set(lc_questions_key(event_name), json.dumps(questions), ex=LC_KEY_TTL)

	# Meta hash
	eligible_plans = [ep.plan for ep in (event.eligible_plans or [])]
	meta = {
		"exam_start_ts": str(event.exam_start_ts),
		"exam_end_ts": str(event.exam_end_ts),
		"capacity": str(event.capacity),
		"show_correct_answers": str(int(event.show_correct_answers)),
		"show_student_rank": str(int(event.show_student_rank)),
		"enable_question_timer": str(int(event.enable_question_timer)),
		"question_time_limit": str(event.question_time_limit or 30),
		"waiting_room_duration": str(event.waiting_room_duration),
		"eligible_plans": json.dumps(eligible_plans),
	}
	meta_key = lc_meta_key(event_name)
	pipe.delete(meta_key)
	pipe.hset(meta_key, mapping=meta)
	pipe.expire(meta_key, LC_KEY_TTL)

	# Count key (initialize to 0)
	pipe.set(lc_count_key(event_name), "0", ex=LC_KEY_TTL)

	pipe.execute()

	# Transition MariaDB status
	event.status = "Waiting"
	event.flags.ignore_validate = True
	event.save(ignore_permissions=True)


def _transition_to_active(event_name: str):
	"""Transition event from Waiting to Active. Redis status is set idempotently."""
	event = frappe.get_doc("Memora Live Challenge Event", event_name)
	if event.status != "Waiting":
		return

	r = get_memora_redis()
	# Idempotent SET — FastAPI may have already set this via WebSocket countdown
	r.set(lc_status_key(event_name), "active", ex=LC_KEY_TTL)

	event.status = "Active"
	event.flags.ignore_validate = True
	event.save(ignore_permissions=True)


def _transition_to_ended(event_name: str):
	"""Transition event from Active to Ended.

	Post-event processing (leaderboard + XP) is deferred to _try_finalize_event()
	to ensure all queued submissions have been flushed to MariaDB first.
	"""
	event = frappe.get_doc("Memora Live Challenge Event", event_name)
	if event.status != "Active":
		return

	r = get_memora_redis()
	r.set(lc_status_key(event_name), "ended", ex=LC_KEY_TTL)

	event.status = "Ended"
	event.flags.ignore_validate = True
	event.save(ignore_permissions=True)


def _try_finalize_event(event_name: str, exam_end_ts=None):
	"""Attempt post-event processing only if all submissions have been flushed.

	Compares Redis submitted set count vs MariaDB participation records with
	submitted_at set. If there's a mismatch (queue hasn't fully flushed),
	skips and retries on next cron run. Forces processing after 5 minutes.
	"""
	r = get_memora_redis()
	redis_submitted = r.scard(lc_submitted_key(event_name))
	db_submitted = frappe.db.count(
		"Memora Live Challenge Participation",
		filters={"event": event_name, "submitted_at": ["is", "set"]},
	)

	if redis_submitted > db_submitted:
		# Check safety timeout: force processing if event ended >5 minutes ago
		if exam_end_ts:
			elapsed = (now_datetime() - exam_end_ts).total_seconds()
			if elapsed < 300:
				return

	try:
		_post_event_processing(event_name)
	except Exception:
		frappe.log_error(title=f"LC post-event processing failed: {event_name}")


def compute_ranking(
	participants: list[dict],
	display_names: dict[str, str],
) -> tuple[list[dict], list[dict]]:
	"""Compute standard competition ranking for all participants (pure function).

	Standard competition ranking: tied scores share the same rank, and the next
	rank equals the number of players ranked above (e.g., 1, 1, 3, 4).

	Args:
		participants: List of dicts with 'name', 'player', 'score' keys,
			pre-sorted by score DESC.
		display_names: Mapping of player_id -> display_name.

	Returns:
		Tuple of (all_ranked, top_20):
		- all_ranked: Every participant with their rank added.
		- top_20: First 20 entries formatted for leaderboard_json.
	"""
	if not participants:
		return [], []

	# Sort by score descending (ensure stable order)
	sorted_parts = sorted(participants, key=lambda p: p["score"], reverse=True)

	ranked: list[dict] = []
	for i, p in enumerate(sorted_parts):
		if i == 0 or p["score"] < sorted_parts[i - 1]["score"]:
			current_rank = i + 1  # Standard competition: rank = position number
		ranked.append({
			"name": p["name"],
			"player": p["player"],
			"score": p["score"],
			"rank": current_rank,
			"display_name": display_names.get(p["player"], p["player"]),
		})

	top_20 = [
		{
			"rank": r["rank"],
			"player": r["player"],
			"display_name": r["display_name"],
			"score": r["score"],
		}
		for r in ranked[:20]
	]

	return ranked, top_20


def compute_xp_awards(
	ranked: list[dict],
	xp_config: dict,
) -> list[dict]:
	"""Compute XP awards for ranked participants (pure function).

	Each participant receives:
	- participation_xp (flat amount for all submitters)
	- rank bonus: first_place_xp (rank 1), second_place_xp (rank 2),
	  third_place_xp (rank 3), default_xp (rank 4+)
	- total_xp = participation_xp + rank_bonus

	Tied ranks all receive the same rank's XP (e.g., two rank-1 players
	both get first_place_xp).

	Args:
		ranked: List of dicts with 'name', 'player', 'rank' keys
			(output of compute_ranking).
		xp_config: Dict with participation_xp, first_place_xp,
			second_place_xp, third_place_xp, default_xp.

	Returns:
		List of dicts with 'name', 'player', 'total_xp' for each participant.
	"""
	if not ranked:
		return []

	participation_xp = xp_config.get("participation_xp", 0)
	rank_bonus_map = {
		1: xp_config.get("first_place_xp", 0),
		2: xp_config.get("second_place_xp", 0),
		3: xp_config.get("third_place_xp", 0),
	}
	default_bonus = xp_config.get("default_xp", 0)

	awards = []
	for entry in ranked:
		rank_bonus = rank_bonus_map.get(entry["rank"], default_bonus)
		total_xp = participation_xp + rank_bonus
		awards.append({
			"name": entry["name"],
			"player": entry["player"],
			"total_xp": total_xp,
		})

	return awards


def _post_event_processing(event_name: str):
	"""Post-event processing: compute rankings, distribute XP, then save leaderboard.

	leaderboard_json is saved LAST so the event remains in the retry queue
	until ALL steps succeed. Both sub-steps are independently idempotent.
	"""
	event = frappe.get_doc("Memora Live Challenge Event", event_name)

	# Idempotency: skip if fully complete
	if event.leaderboard_json:
		return

	top_20, participant_count, submitted_count = _compute_and_store_rankings(event_name)
	_distribute_xp(event_name)

	# Mark complete: save leaderboard_json LAST as completion marker
	event.reload()
	event.leaderboard_json = json.dumps(top_20)
	event.participant_count = participant_count
	event.submitted_count = submitted_count
	event.flags.ignore_validate = True
	event.save(ignore_permissions=True)


def _compute_and_store_rankings(event_name: str) -> tuple[list, int, int]:
	"""Compute ranked leaderboard and store ranks on participation records.

	Returns (top_20, participant_count, submitted_count).
	Does NOT save leaderboard_json — caller saves it after XP distribution
	so that a transient XP failure doesn't prevent retries.
	Idempotent: set_value overwrites existing rank values safely.
	"""
	# Query all submitted participations ordered by score DESC
	participations = frappe.get_all(
		"Memora Live Challenge Participation",
		filters={"event": event_name, "submitted_at": ["is", "set"]},
		fields=["name", "player", "score"],
		order_by="score desc",
		limit_page_length=0,
	)

	participant_count = frappe.db.count(
		"Memora Live Challenge Participation",
		filters={"event": event_name},
	)

	if not participations:
		return [], participant_count, 0

	# Resolve display_name for all participants
	player_ids = list({p["player"] for p in participations})
	display_names = {}
	for pid in player_ids:
		try:
			dn = frappe.db.get_value("Memora Player Profile", pid, "display_name")
			if dn:
				display_names[pid] = dn
		except Exception:
			pass

	# Compute ranking
	ranked, top_20 = compute_ranking(participations, display_names)

	# Store individual rank on each Participation record (idempotent bulk update)
	for entry in ranked:
		frappe.db.set_value(
			"Memora Live Challenge Participation",
			entry["name"],
			"rank",
			entry["rank"],
			update_modified=False,
		)

	submitted_count = len(participations)

	return top_20, participant_count, submitted_count


def _distribute_xp(event_name: str):
	"""Distribute XP to all ranked participants via Redis wallet + dirty set.

	Idempotency: skips if any Participation record already has xp_awarded > 0.
	Uses existing wallet pattern: HINCRBY on wallet hash + SADD to dirty:wallets.
	"""
	# Idempotency check: if any participation already has XP, skip
	already_awarded = frappe.db.count(
		"Memora Live Challenge Participation",
		filters={"event": event_name, "xp_awarded": [">", 0]},
	)
	if already_awarded:
		return

	# Load event XP config
	event = frappe.get_doc("Memora Live Challenge Event", event_name)
	xp_config = {
		"participation_xp": event.participation_xp or 0,
		"first_place_xp": event.first_place_xp or 0,
		"second_place_xp": event.second_place_xp or 0,
		"third_place_xp": event.third_place_xp or 0,
		"default_xp": event.default_xp or 0,
	}

	# Skip if all XP values are 0
	if not any(xp_config.values()):
		return

	# Query all ranked participations (submitted + ranked)
	participations = frappe.get_all(
		"Memora Live Challenge Participation",
		filters={"event": event_name, "submitted_at": ["is", "set"], "rank": [">", 0]},
		fields=["name", "player", "rank"],
		limit_page_length=0,
	)

	if not participations:
		return

	# Build ranked list for compute_xp_awards
	ranked = [
		{"name": p["name"], "player": p["player"], "rank": p["rank"]}
		for p in participations
	]

	awards = compute_xp_awards(ranked, xp_config)

	# Award XP via Redis wallet HINCRBY + dirty set SADD
	r = get_memora_redis()
	pipe = r.pipeline()

	for award in awards:
		if award["total_xp"] > 0:
			pipe.hincrby(wallet_key(award["player"]), "xp", award["total_xp"])
			pipe.sadd(dirty_wallets_key(), award["player"])

	pipe.execute()

	# Update xp_awarded on each Participation record
	for award in awards:
		frappe.db.set_value(
			"Memora Live Challenge Participation",
			award["name"],
			"xp_awarded",
			award["total_xp"],
			update_modified=False,
		)
