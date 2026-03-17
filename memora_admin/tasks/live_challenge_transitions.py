"""Scheduled task for Live Challenge event state transitions.

Runs every 60 seconds. Handles:
1. Draft -> Waiting: when scheduled_start <= now
2. Waiting -> Active: when exam_start_ts <= now
3. Active -> Ended: when exam_end_ts <= now
4. Reconciliation: flush Redis data to MariaDB after event ends
5. Finalization: compute leaderboard + distribute XP

On Draft -> Waiting: populates Redis keys (status, questions, meta, count).
On Active -> Ended: triggers reconciliation + post-event processing.
"""

import json

import frappe
from frappe.utils import now_datetime

from fastapi_app.core.redis_keys import (
	LC_KEY_TTL,
	dirty_wallets_key,
	lc_count_key,
	lc_join_times_key,
	lc_joined_key,
	lc_meta_key,
	lc_questions_key,
	lc_reconcile_lock_key,
	lc_reconciled_key,
	lc_results_key,
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
	"""Transition event from Draft to Waiting and populate Redis keys.

	Uses SET NX for count key to avoid resetting participant count if
	FastAPI already advanced the event and users have joined.
	"""
	event = frappe.get_doc("Memora Live Challenge Event", event_name)
	if event.status != "Draft":
		return  # Already transitioned

	r = get_memora_redis()

	# Guard: if Redis status is already beyond "draft", FastAPI already
	# advanced this event. Only populate keys that don't exist yet.
	current_status = r.get(lc_status_key(event_name))
	if isinstance(current_status, bytes):
		current_status = current_status.decode()
	if current_status in ("waiting", "active", "ended"):
		# FastAPI already set status — just sync DB and skip key writes
		event.status = "Waiting"
		event.flags.ignore_validate = True
		event.save(ignore_permissions=True)
		return

	# Populate Redis before changing status
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
		"event_name": event.event_name or "",
		"description": event.description or "",
		"scheduled_start": str(event.scheduled_start or ""),
		"exam_start_ts": str(event.exam_start_ts),
		"exam_end_ts": str(event.exam_end_ts),
		"exam_duration": str(event.exam_duration or 10),
		"capacity": str(event.capacity),
		"is_paid": str(int(event.is_paid or 0)),
		"show_correct_answers": str(int(event.show_correct_answers)),
		"show_student_rank": str(int(event.show_student_rank)),
		"enable_question_timer": str(int(event.enable_question_timer)),
		"question_time_limit": str(event.question_time_limit or 30),
		"waiting_room_duration": str(event.waiting_room_duration),
		"participation_xp": str(event.participation_xp or 0),
		"first_place_xp": str(event.first_place_xp or 0),
		"second_place_xp": str(event.second_place_xp or 0),
		"third_place_xp": str(event.third_place_xp or 0),
		"default_xp": str(event.default_xp or 0),
		"eligible_plans": json.dumps(eligible_plans),
	}
	meta_key = lc_meta_key(event_name)
	pipe.delete(meta_key)
	pipe.hset(meta_key, mapping=meta)
	pipe.expire(meta_key, LC_KEY_TTL)

	pipe.execute()

	# Count key — SETNX: do NOT reset if FastAPI already started counting
	r.set(lc_count_key(event_name), "0", nx=True, ex=LC_KEY_TTL)

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
	"""Transition event from Active to Ended, then reconcile Redis → DB.

	Reconciliation runs here (synchronously) so data is persisted even
	if no FastAPI client hits after the event ends.
	"""
	event = frappe.get_doc("Memora Live Challenge Event", event_name)
	if event.status != "Active":
		return

	r = get_memora_redis()
	r.set(lc_status_key(event_name), "ended", ex=LC_KEY_TTL)

	event.status = "Ended"
	event.flags.ignore_validate = True
	event.save(ignore_permissions=True)

	# Reconcile immediately — the authoritative path for Redis → DB flush.
	_cron_reconcile_event(event_name)


def _try_finalize_event(event_name: str, exam_end_ts=None):
	"""Attempt post-event processing only if reconciliation is complete.

	Step 1: Ensure reconciliation has happened (Redis → DB).
	Step 2: Compare submitted counts to verify data completeness.
	Step 3: Run leaderboard + XP processing.
	"""
	r = get_memora_redis()

	# Step 1: Ensure reconciliation ran (safety net if _transition_to_ended missed it)
	reconciled = r.get(lc_reconciled_key(event_name))
	if not reconciled:
		_cron_reconcile_event(event_name)

	# Step 2: Verify data completeness
	redis_submitted = r.scard(lc_submitted_key(event_name))
	db_submitted = frappe.db.count(
		"Memora Live Challenge Participation",
		filters={"event": event_name, "submitted_at": ["is", "set"]},
	)

	if redis_submitted and redis_submitted > db_submitted:
		# Data still in Redis but not DB — reconciliation may have partially failed.
		# Retry reconciliation.
		_cron_reconcile_event(event_name)
		# Re-check after retry
		db_submitted = frappe.db.count(
			"Memora Live Challenge Participation",
			filters={"event": event_name, "submitted_at": ["is", "set"]},
		)
		if redis_submitted > db_submitted:
			# Still mismatched — force after 5 minutes
			if exam_end_ts:
				elapsed = (now_datetime() - exam_end_ts).total_seconds()
				if elapsed < 300:
					return

	try:
		_post_event_processing(event_name)
	except Exception:
		frappe.log_error(title=f"LC post-event processing failed: {event_name}")


def _decode(val):
	"""Decode bytes to str (sync Redis returns bytes)."""
	return val.decode() if isinstance(val, bytes) else val


def _cron_reconcile_event(event_name: str) -> bool:
	"""Synchronous reconciliation: flush Redis join/result data to MariaDB.

	This is the authoritative reconciliation path — runs from the cron job
	so it does NOT depend on any FastAPI client hitting after the event ends.
	Uses direct SQL for bulk inserts (no Frappe RPC overhead).

	Returns True if reconciliation succeeded or was already done.
	"""
	r = get_memora_redis()

	# Already reconciled?
	if r.exists(lc_reconciled_key(event_name)):
		return True

	# Acquire distributed lock (3600s TTL)
	acquired = r.set(lc_reconcile_lock_key(event_name), "1", nx=True, ex=3600)
	if not acquired:
		return False

	try:
		# Read snapshot from Redis
		player_ids_raw = r.smembers(lc_joined_key(event_name))
		if not player_ids_raw:
			# No joined players — either already reconciled by FastAPI or empty event
			r.set(lc_reconciled_key(event_name), "1", ex=LC_KEY_TTL)
			_cleanup_all_redis_keys(r, event_name)
			return True

		count_raw = r.get(lc_count_key(event_name))
		count = int(_decode(count_raw)) if count_raw else len(player_ids_raw)
		join_times_raw = r.hgetall(lc_join_times_key(event_name))
		results_raw = r.hgetall(lc_results_key(event_name))
		meta_raw = r.hgetall(lc_meta_key(event_name))

		# Decode meta
		meta = {_decode(k): _decode(v) for k, v in meta_raw.items()} if meta_raw else {}
		default_joined_at = meta.get("exam_start_ts") or now_datetime().strftime("%Y-%m-%d %H:%M:%S")

		# Decode join times
		join_times = {}
		for k, v in join_times_raw.items():
			join_times[_decode(k)] = _decode(v)

		# Parse results
		results = {}
		for k, v in results_raw.items():
			pid = _decode(k)
			try:
				results[pid] = json.loads(_decode(v))
			except (json.JSONDecodeError, TypeError):
				pass

		# Find existing participation records to avoid duplicates
		existing = set()
		existing_rows = frappe.db.sql(
			"SELECT player FROM `tabMemora Live Challenge Participation` WHERE event = %s",
			(event_name,),
			as_dict=False,
		)
		for row in existing_rows:
			existing.add(row[0])

		# Build rows for INSERT (only new players)
		now_str = now_datetime().strftime("%Y-%m-%d %H:%M:%S")
		insert_values = []
		update_values = []

		for pid_raw in player_ids_raw:
			pid = _decode(pid_raw)
			joined_at = join_times.get(pid) or default_joined_at
			r_data = results.get(pid)
			score = float(r_data["score"]) if r_data and r_data.get("score") is not None else None
			submitted_at = r_data.get("submitted_at") if r_data else None
			answers_json = r_data.get("answers_json") if r_data else None

			if pid in existing:
				# Already inserted (by FastAPI reconciliation or prior cron run) — update score
				if r_data:
					update_values.append((score, submitted_at, answers_json, event_name, pid))
			else:
				name = frappe.generate_hash(length=10)
				insert_values.append((
					name, event_name, pid, joined_at,
					score, submitted_at, answers_json,
					now_str, now_str, "Administrator", "Administrator",
				))

		# Bulk INSERT new participation records
		batch_size = 500
		for i in range(0, len(insert_values), batch_size):
			batch = insert_values[i : i + batch_size]
			placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(batch))
			flat = [v for row in batch for v in row]
			frappe.db.sql(
				f"""INSERT INTO `tabMemora Live Challenge Participation`
				(name, event, player, joined_at, score, submitted_at, answers_json,
				 creation, modified, owner, modified_by)
				VALUES {placeholders}""",
				flat,
			)

		# Bulk UPDATE existing records with score data (batched CASE)
		batch_size = 500
		for i in range(0, len(update_values), batch_size):
			batch = update_values[i : i + batch_size]
			if not batch:
				continue
			score_cases = []
			submitted_cases = []
			answers_cases = []
			players = []
			for score, submitted_at, answers_json, ev, pid in batch:
				escaped_pid = frappe.db.escape(pid)
				players.append(escaped_pid)
				score_cases.append(f"WHEN player = {escaped_pid} THEN {float(score) if score is not None else 'NULL'}")
				sub_val = f"'{submitted_at}'" if submitted_at else "NULL"
				submitted_cases.append(f"WHEN player = {escaped_pid} THEN {sub_val}")
				ans_val = frappe.db.escape(answers_json) if answers_json else "NULL"
				answers_cases.append(f"WHEN player = {escaped_pid} THEN {ans_val}")
			escaped_event = frappe.db.escape(event_name)
			frappe.db.sql(f"""
				UPDATE `tabMemora Live Challenge Participation`
				SET score = CASE {' '.join(score_cases)} ELSE score END,
				    submitted_at = CASE {' '.join(submitted_cases)} ELSE submitted_at END,
				    answers_json = CASE {' '.join(answers_cases)} ELSE answers_json END
				WHERE event = {escaped_event} AND player IN ({','.join(players)})
			""")

		# Sync counters to event
		submitted_count = len(results)
		frappe.db.set_value(
			"Memora Live Challenge Event", event_name,
			{"participant_count": count, "submitted_count": submitted_count},
			update_modified=False,
		)

		# Mark reconciled + cleanup ALL Redis keys
		r.set(lc_reconciled_key(event_name), "1", ex=LC_KEY_TTL)
		_cleanup_all_redis_keys(r, event_name)

		frappe.logger().info(
			f"LC cron reconcile complete: {event_name} "
			f"(inserted={len(insert_values)}, updated={len(update_values)}, "
			f"participants={count}, submitted={submitted_count})"
		)
		return True

	except Exception:
		r.delete(lc_reconcile_lock_key(event_name))
		frappe.log_error(title=f"LC cron reconciliation failed: {event_name}")
		return False


def _cleanup_all_redis_keys(r, event_name: str) -> None:
	"""Delete ephemeral Redis keys for an event after reconciliation.

	Status key is KEPT ("ended") — it is the single routing signal for
	_resolve_event_source(). TTL is refreshed so it survives 24h
	after reconciliation.
	"""
	pipe = r.pipeline()
	# Keep status key alive as routing signal; refresh its TTL
	pipe.expire(lc_status_key(event_name), LC_KEY_TTL)
	# Delete ephemeral keys — DB is now source of truth
	pipe.delete(lc_count_key(event_name))
	pipe.delete(lc_meta_key(event_name))
	pipe.delete(lc_questions_key(event_name))
	pipe.delete(lc_joined_key(event_name))
	pipe.delete(lc_submitted_key(event_name))
	pipe.delete(lc_join_times_key(event_name))
	pipe.delete(lc_results_key(event_name))
	pipe.delete(lc_reconcile_lock_key(event_name))
	pipe.execute()


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
	Idempotent: CASE-based UPDATE overwrites existing rank values safely.
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

	# Batch-resolve display_names in one query instead of N+1
	player_ids = list({p["player"] for p in participations})
	display_names = {}
	if player_ids:
		profiles = frappe.get_all(
			"Memora Player Profile",
			filters={"name": ["in", player_ids]},
			fields=["name", "display_name"],
			limit_page_length=0,
		)
		display_names = {p["name"]: p["display_name"] for p in profiles if p.get("display_name")}

	# Compute ranking
	ranked, top_20 = compute_ranking(participations, display_names)

	# Bulk update ranks using batched CASE statements (instead of 10k individual UPDATEs)
	_batch_update_field(ranked, "rank")

	submitted_count = len(participations)

	return top_20, participant_count, submitted_count


def _batch_update_field(entries: list[dict], field: str, batch_size: int = 500) -> None:
	"""Bulk-update a single field on Participation records using CASE statements.

	For 10k records at batch_size=500, this is 20 queries instead of 10k.
	"""
	for i in range(0, len(entries), batch_size):
		batch = entries[i : i + batch_size]
		if not batch:
			continue
		case_parts = []
		names = []
		for entry in batch:
			escaped_name = frappe.db.escape(entry["name"])
			names.append(escaped_name)
			case_parts.append(f"WHEN name = {escaped_name} THEN {int(entry[field])}")

		sql = f"""
			UPDATE `tabMemora Live Challenge Participation`
			SET `{field}` = CASE {' '.join(case_parts)} END
			WHERE name IN ({','.join(names)})
		"""
		frappe.db.sql(sql)


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

	# Bulk update xp_awarded on Participation records (batched CASE)
	xp_entries = [{"name": a["name"], "xp_awarded": a["total_xp"]} for a in awards if a["total_xp"] > 0]
	_batch_update_field(xp_entries, "xp_awarded")
