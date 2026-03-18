"""Live Challenge service — join, grade, WebSocket management, post-event reconciliation."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog
from fastapi import WebSocket

from fastapi_app.core.config import get_settings
from fastapi_app.core.redis_keys import (
	LC_KEY_TTL,
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
)
from fastapi_app.services.waiting_room_reactions import ReactionEngine

if TYPE_CHECKING:
	from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()

# Asia/Amman timezone — Frappe stores all datetimes in this timezone.
# FastAPI must use the same zone for countdown comparisons, joined_at, submitted_at.
AMMAN_TZ = ZoneInfo("Asia/Amman")


def _now_naive() -> datetime:
	"""Return current time in Asia/Amman as a naive datetime (matches Frappe timestamps)."""
	return datetime.now(AMMAN_TZ).replace(tzinfo=None)


# Lua script for atomic join: status check + uniqueness + capacity + SADD + EXPIRE in one call.
# KEYS: [1] joined_set, [2] submitted_set, [3] count_key, [4] status_key
# ARGV: [1] player_id, [2] capacity, [3] joined_set_ttl
# Returns: position (>0) on success, -1 capacity full, -2 already joined,
#          -3 already submitted, -4 event not joinable
_ATOMIC_JOIN_LUA = """
local status = redis.call('GET', KEYS[4])
if status ~= 'waiting' and status ~= 'active' then
    return -4
end
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then
    return -2
end
if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
    return -3
end
local current = tonumber(redis.call('GET', KEYS[3]) or '0')
if current >= tonumber(ARGV[2]) then
    return -1
end
local pos = redis.call('INCR', KEYS[3])
redis.call('SADD', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return pos
"""

# Lua script for atomic status transition (CAS — compare-and-swap).
# KEYS: [1] status_key
# ARGV: [1] expected_current_status, [2] new_status, [3] status_ttl
# Returns: new status on success, current status if already changed, nil if key missing.
# Lua scripts are atomic in Redis, so no external lock is needed.
_ATOMIC_TRANSITION_LUA = """
local current = redis.call('GET', KEYS[1])
if not current then return nil end
if current ~= ARGV[1] then return current end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return ARGV[2]
"""

# Broadcast: max concurrent WebSocket sends.
# 2000 keeps per-tick broadcast under ~500ms for 10k connections.
_BROADCAST_CONCURRENCY = 2000
# Reconciliation: chunk size for insert_many calls
_RECONCILE_BATCH_SIZE = 500


def _strip_correct_answers(questions: list[dict]) -> list[dict]:
	"""Return questions with correct_answer removed (safe for client)."""
	return [
		{
			"idx": q["idx"],
			"question_text": q["question_text"],
			"option_a": q["option_a"],
			"option_b": q["option_b"],
			"option_c": q["option_c"],
			"option_d": q["option_d"],
		}
		for q in questions
	]


def _build_exam_start_msg(safe_questions: list[dict], meta: dict) -> dict:
	"""Build the exam_start message payload from stripped questions and Redis meta."""
	return {
		"type": "exam_start",
		"exam_end_ts": meta.get("exam_end_ts", ""),
		"total_questions": len(safe_questions),
		"enable_question_timer": bool(int(meta.get("enable_question_timer", "0"))),
		"question_time_limit": int(meta.get("question_time_limit", "30")),
		"questions": safe_questions,
	}


def grade_answers(
	questions: list[dict],
	answers: list[dict],
	*,
	show_correct_answers: bool,
) -> dict[str, Any]:
	"""Grade submitted answers against correct answers (pure function).

	Args:
		questions: List of question dicts with 'idx' and 'correct_answer' fields.
		answers: List of answer dicts with 'question_idx' and 'selected' fields.
		show_correct_answers: If True, return corrections list; if False, return None.

	Returns:
		Dict with score, correct_count, total_questions, and corrections.
	"""
	total = len(questions)
	correct_count = 0

	# Build lookup: question_idx -> selected
	answer_map = {a["question_idx"]: a["selected"] for a in answers}

	corrections = []
	for q in questions:
		idx = q["idx"]
		selected = answer_map.get(idx)
		correct = q["correct_answer"]

		if selected == correct:
			correct_count += 1
		else:
			corrections.append(
				{
					"question_idx": idx,
					"selected": selected,
					"correct_answer": correct,
				}
			)

	score = (correct_count / total) * 100 if total > 0 else 0.0

	return {
		"score": score,
		"correct_count": correct_count,
		"total_questions": total,
		"corrections": corrections if show_correct_answers else None,
	}


class LiveChallengeService:
	def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient):
		self.redis = redis_client
		self.frappe = frappe_client
		self._shutting_down = False
		self._join_script: Any = None
		self._cas_script: Any = None
		# WebSocket connection tracking: event_id -> set[WebSocket]
		self._ws_connections: dict[str, set[WebSocket]] = {}
		# Per-event countdown loop tasks
		self._countdown_tasks: dict[str, asyncio.Task] = {}
		# Waiting room reaction engine
		self._reaction_engine = ReactionEngine(
			settings=get_settings(),
			broadcast=self._broadcast_json,
			redis=self.redis,
		)

	async def _get_join_script(self):
		"""Get or register the Lua atomic join script."""
		if self._join_script is None:
			self._join_script = self.redis.register_script(_ATOMIC_JOIN_LUA)
		return self._join_script

	async def _get_cas_script(self):
		"""Get or register the Lua atomic CAS transition script."""
		if self._cas_script is None:
			self._cas_script = self.redis.register_script(_ATOMIC_TRANSITION_LUA)
		return self._cas_script

	# -------------------------------------------------------------------------
	# Status endpoint (client-driven transitions)
	# -------------------------------------------------------------------------

	async def _hydrate_event_to_redis(self, event_id: str) -> bool:
		"""One-time hydration: fetch event from Frappe and populate Redis.

		For events saved before the after_save hook existed.
		Returns True if hydrated successfully, False if event not found.
		"""
		try:
			event = await self.frappe.call(
				"frappe.client.get",
				{"doctype": "Memora Live Challenge Event", "name": event_id},
			)
		except Exception:
			logger.exception("lc_hydrate_frappe_call_failed", event_id=event_id)
			return False
		if not event:
			return False

		frappe_status = (event.get("status") or "Draft").lower()
		# Map Frappe status to Redis status
		status_map = {"draft": "draft", "waiting": "waiting", "active": "active", "ended": "ended"}
		redis_status = status_map.get(frappe_status, "draft")

		# For ended events, set only status + reconciled flag + count.
		# Meta is NOT hydrated because Redis participant sets cannot be
		# reconstructed post-event. _resolve_event_source() sees status="ended"
		# and routes to _get_event_detail_from_db() which reads Frappe directly.
		if redis_status == "ended":
			pipe = self.redis.pipeline()
			pipe.set(lc_status_key(event_id), redis_status, ex=LC_KEY_TTL)
			pipe.set(lc_reconciled_key(event_id), "1", ex=LC_KEY_TTL)
			pipe.set(lc_count_key(event_id), str(event.get("participant_count", 0)), ex=LC_KEY_TTL)
			await pipe.execute()
			logger.info("lc_hydrated_ended_event", event_id=event_id)
			return True

		pipe = self.redis.pipeline()
		pipe.set(lc_status_key(event_id), redis_status, ex=LC_KEY_TTL)

		# Questions
		questions = []
		for q in event.get("questions") or []:
			raw_idx = q.get("idx")
			questions.append({
				"idx": (int(raw_idx) - 1) if raw_idx is not None else 0,
				"question_text": q.get("question_text", ""),
				"option_a": q.get("option_a", ""),
				"option_b": q.get("option_b", ""),
				"option_c": q.get("option_c", ""),
				"option_d": q.get("option_d", ""),
				"correct_answer": q.get("correct_answer", ""),
			})
		pipe.set(lc_questions_key(event_id), json.dumps(questions), ex=LC_KEY_TTL)

		# Meta hash — includes ALL fields needed by get_event_detail (Redis-only reads)
		eligible_plans = [ep.get("plan", "") for ep in (event.get("eligible_plans") or []) if isinstance(ep, dict)]
		meta = {
			"scheduled_start": str(event.get("scheduled_start", "")),
			"exam_start_ts": str(event.get("exam_start_ts", "")),
			"exam_end_ts": str(event.get("exam_end_ts", "")),
			"capacity": str(event.get("capacity", 100)),
			"show_correct_answers": str(int(event.get("show_correct_answers", 0))),
			"show_student_rank": str(int(event.get("show_student_rank", 0))),
			"enable_question_timer": str(int(event.get("enable_question_timer", 0))),
			"question_time_limit": str(event.get("question_time_limit", 30)),
			"waiting_room_duration": str(event.get("waiting_room_duration", 180)),
			"eligible_plans": json.dumps(eligible_plans),
			"event_name": event.get("event_name", ""),
			"description": event.get("description") or "",
			"exam_duration": str(event.get("exam_duration", 10)),
			"is_paid": str(int(event.get("is_paid", 0))),
			"participation_xp": str(event.get("participation_xp", 0)),
			"first_place_xp": str(event.get("first_place_xp", 0)),
			"second_place_xp": str(event.get("second_place_xp", 0)),
			"third_place_xp": str(event.get("third_place_xp", 0)),
			"default_xp": str(event.get("default_xp", 0)),
		}
		meta_key = lc_meta_key(event_id)
		# hset with all fields is an atomic overwrite — no delete needed
		pipe.hset(meta_key, mapping=meta)
		pipe.expire(meta_key, LC_KEY_TTL)

		# Count (preserve existing, else 0)
		count_key = lc_count_key(event_id)
		pipe.setnx(count_key, "0")
		pipe.expire(count_key, LC_KEY_TTL)

		await pipe.execute()
		logger.info("lc_hydrated_to_redis", event_id=event_id, status=redis_status)
		return True

	async def get_status(self, event_id: str) -> dict[str, Any] | None:
		"""Get current event status, triggering transitions if due.

		Redis-first — hydrates from Frappe on first access if needed.
		The first request arriving after a transition threshold atomically
		advances the status; concurrent requests get the updated value.
		"""
		current_status = await self.redis.get(lc_status_key(event_id))
		if current_status is None:
			# Keys fully deleted after reconciliation — check reconciled flag
			if await self.redis.exists(lc_reconciled_key(event_id)):
				# Event ended and fully reconciled — DB is source of truth.
				# Fetch participant_count from Frappe.
				try:
					count = await self.frappe.call(
						"frappe.client.get_value",
						{
							"doctype": "Memora Live Challenge Event",
							"fieldname": "participant_count",
							"filters": event_id,
						},
					)
					pc = int(count.get("participant_count", 0)) if count else 0
				except Exception:
					pc = 0
				return {"status": "ended", "participant_count": pc}
			# Not in Redis yet — hydrate from Frappe (one-time)
			if not await self._hydrate_event_to_redis(event_id):
				return None
			current_status = await self.redis.get(lc_status_key(event_id))
			if current_status is None:
				return None

		# Pipeline the two reads into a single round-trip
		pipe = self.redis.pipeline()
		pipe.hgetall(lc_meta_key(event_id))
		pipe.get(lc_count_key(event_id))
		meta, count_raw = await pipe.execute()
		participant_count = int(count_raw or "0")

		if not meta:
			# Meta deleted after reconciliation — status is terminal "ended"
			if current_status == "ended":
				return {"status": "ended", "participant_count": participant_count}
			return None

		# Guard against empty/missing timestamps (event may lack computed times)
		exam_start_raw = meta.get("exam_start_ts", "")
		exam_end_raw = meta.get("exam_end_ts", "")
		if not exam_start_raw or not exam_end_raw:
			return {"status": current_status, "participant_count": participant_count}

		try:
			exam_start_ts = datetime.fromisoformat(exam_start_raw)
			exam_end_ts = datetime.fromisoformat(exam_end_raw)
		except ValueError:
			logger.warning("lc_status_bad_timestamps", event_id=event_id,
				exam_start_ts=exam_start_raw, exam_end_ts=exam_end_raw)
			return {"status": current_status, "participant_count": participant_count}

		# Determine what the status SHOULD be based on server time
		now = _now_naive()
		scheduled_raw = meta.get("scheduled_start", "")
		if scheduled_raw:
			try:
				scheduled_start = datetime.fromisoformat(scheduled_raw)
			except ValueError:
				wr = int(meta.get("waiting_room_duration", "180"))
				scheduled_start = exam_start_ts - timedelta(seconds=wr)
		else:
			wr = int(meta.get("waiting_room_duration", "180"))
			scheduled_start = exam_start_ts - timedelta(seconds=wr)

		if now >= exam_end_ts:
			expected = "ended"
		elif now >= exam_start_ts:
			expected = "active"
		elif now >= scheduled_start:
			expected = "waiting"
		else:
			expected = "draft"

		# If no transition needed, return immediately (fast path ~1ms)
		if expected == current_status:
			# For ended events, ensure reconciliation has been kicked off (non-blocking)
			if current_status == "ended" and not await self.redis.exists(lc_reconciled_key(event_id)):
				asyncio.create_task(self._reconcile_event(event_id))
			return {"status": current_status, "participant_count": participant_count}

		# Transition needed — use atomic CAS Lua script
		script = await self._get_cas_script()

		# Walk through each required transition step in order
		transitions = [("draft", "waiting"), ("waiting", "active"), ("active", "ended")]
		resolved_status = current_status
		for from_status, to_status in transitions:
			if resolved_status != from_status:
				continue
			# Check if this transition is due
			if to_status == "waiting" and now < scheduled_start:
				break
			if to_status == "active" and now < exam_start_ts:
				break
			if to_status == "ended" and now < exam_end_ts:
				break

			result = await script(
				keys=[lc_status_key(event_id)],
				args=[from_status, to_status, str(LC_KEY_TTL)],
			)
			if result is None:
				break
			result_str = result if isinstance(result, str) else result.decode()
			resolved_status = result_str
			# Trigger reconciliation when transitioning to ended (non-blocking)
			if to_status == "ended" and result_str == "ended":
				asyncio.create_task(self._reconcile_event(event_id))

		return {"status": resolved_status, "participant_count": participant_count}

	# -------------------------------------------------------------------------
	# Lifecycle
	# -------------------------------------------------------------------------

	async def start_reaction_subscriber(self) -> None:
		"""Start the cross-worker reaction burst pub/sub subscriber."""
		await self._reaction_engine.start_subscriber()

	async def shutdown(self):
		"""Signal all background loops to stop and cancel countdown tasks."""
		self._shutting_down = True
		await self._reaction_engine.stop_subscriber()
		for event_id in list(self._countdown_tasks):
			self.stop_countdown_loop(event_id)

	# -------------------------------------------------------------------------
	# Join (T021)
	# -------------------------------------------------------------------------

	async def join(
		self,
		event_id: str,
		player_id: str,
		player_plan: str | None = None,
	) -> dict[str, Any]:
		"""Join a live challenge event (pure Redis — zero Frappe calls).

		Returns dict with position, countdown_remaining, waiting_room_duration.
		Raises ValueError with error code on failure.
		"""
		# 1. Fast-path status + meta in one round trip
		pipe = self.redis.pipeline()
		pipe.get(lc_status_key(event_id))
		pipe.hgetall(lc_meta_key(event_id))
		status, meta = await pipe.execute()
		if status not in ("waiting", "active"):
			raise ValueError("EVENT_NOT_JOINABLE")

		# 2. Check plan eligibility (before atomic join to avoid wasting a capacity slot)
		eligible_plans_json = meta.get("eligible_plans", "[]")
		eligible_plans = json.loads(eligible_plans_json)
		if eligible_plans and player_plan not in eligible_plans:
			raise ValueError("PLAN_NOT_ELIGIBLE")

		# 3. Atomic join: status check + uniqueness + capacity + SADD + EXPIRE in one Lua call
		capacity = int(meta.get("capacity", "0"))
		script = await self._get_join_script()
		position = await script(
			keys=[
				lc_joined_key(event_id),
				lc_submitted_key(event_id),
				lc_count_key(event_id),
				lc_status_key(event_id),
			],
			args=[player_id, capacity, LC_KEY_TTL],
		)
		if position == -4:
			raise ValueError("EVENT_NOT_JOINABLE")
		if position == -2 or position == -3:
			raise ValueError("ALREADY_JOINED")
		if position == -1:
			raise ValueError("CAPACITY_FULL")

		# Record per-player join timestamp for reconciliation (pipelined)
		join_time = _now_naive().strftime("%Y-%m-%d %H:%M:%S")
		join_pipe = self.redis.pipeline()
		join_pipe.hset(lc_join_times_key(event_id), player_id, join_time)
		join_pipe.expire(lc_join_times_key(event_id), LC_KEY_TTL)
		await join_pipe.execute()

		# 4. Calculate countdown_remaining
		countdown_remaining = 0
		if status == "waiting":
			exam_start_ts_str = meta.get("exam_start_ts", "")
			if exam_start_ts_str:
				try:
					exam_start = datetime.fromisoformat(exam_start_ts_str)
					remaining = (exam_start - _now_naive()).total_seconds()
					countdown_remaining = max(0, int(remaining))
				except ValueError:
					pass

		waiting_room_duration = int(meta.get("waiting_room_duration", "0"))

		logger.info(
			"lc_player_joined",
			event_id=event_id,
			player_id=player_id,
			position=position,
			status=status,
		)

		return {
			"position": position,
			"countdown_remaining": countdown_remaining,
			"waiting_room_duration": waiting_room_duration,
		}

	# -------------------------------------------------------------------------
	# Reconciliation (post-event Frappe persistence)
	# -------------------------------------------------------------------------

	async def _reconcile_event(self, event_id: str) -> None:
		"""Persist join + submission data to Frappe after event ends (idempotent, lock-guarded).

		Reads the lc:{id}:joined SET and lc:{id}:results HASH (sole sources of truth)
		and creates Participation docs in Frappe with score data pre-populated.
		Only deletes Redis keys on full success.
		"""
		# Step 0 — Already reconciled?
		if await self.redis.exists(lc_reconciled_key(event_id)):
			logger.info("lc_reconciliation_already_done", event_id=event_id)
			return

		# Step 1 — Acquire lock (3600s TTL)
		acquired = await self.redis.set(lc_reconcile_lock_key(event_id), "1", nx=True, ex=3600)
		if not acquired:
			logger.info("lc_reconciliation_locked", event_id=event_id)
			return

		try:
			# Step 2 — Read snapshot (joined players, join times, submission results)
			player_ids = await self.redis.smembers(lc_joined_key(event_id))
			count = int(await self.redis.get(lc_count_key(event_id)) or "0")
			meta = await self.redis.hgetall(lc_meta_key(event_id))
			join_times = await self.redis.hgetall(lc_join_times_key(event_id))
			results_raw = await self.redis.hgetall(lc_results_key(event_id))
			default_joined_at = meta.get("exam_start_ts", _now_naive().strftime("%Y-%m-%d %H:%M:%S"))

			# Parse submission results: player_id -> {score, correct_count, submitted_at, answers_json}
			results: dict[str, dict] = {}
			for pid, payload in results_raw.items():
				try:
					results[pid] = json.loads(payload)
				except (json.JSONDecodeError, TypeError):
					logger.warning("lc_reconcile_bad_result_payload", event_id=event_id, player=pid)

			# Step 3 — Build participation docs with score data pre-populated
			docs = []
			for pid in player_ids:
				doc: dict[str, Any] = {
					"doctype": "Memora Live Challenge Participation",
					"event": event_id,
					"player": pid,
					"joined_at": join_times.get(pid, default_joined_at),
				}
				r = results.get(pid)
				if r:
					doc["score"] = r.get("score")
					doc["submitted_at"] = r.get("submitted_at")
					doc["answers_json"] = r.get("answers_json")
				docs.append(doc)

			failed_players: list[str] = []
			# Chunk insert_many into batches (10k docs in one call exceeds payload limits)
			for i in range(0, len(docs), _RECONCILE_BATCH_SIZE):
				batch = docs[i : i + _RECONCILE_BATCH_SIZE]
				try:
					await self.frappe.call("frappe.client.insert_many", {"docs": json.dumps(batch)})
				except Exception:
					# Fallback: sequential idempotent inserts for this batch
					for doc in batch:
						try:
							await self.frappe.call("frappe.client.insert", {"doc": json.dumps(doc)})
						except Exception as e:
							err_str = str(e).lower()
							if "duplicate" in err_str or "already exists" in err_str:
								# Doc already exists — update score via filters
								# (participation docs have hash names, not player_id)
								r = results.get(doc["player"])
								if r:
									try:
										# Find the actual doc name first
										existing = await self.frappe.call(
											"frappe.client.get_list",
											{
												"doctype": "Memora Live Challenge Participation",
												"filters": json.dumps([
													["event", "=", event_id],
													["player", "=", doc["player"]],
												]),
												"fields": json.dumps(["name"]),
												"limit_page_length": "1",
											},
										)
										if existing:
											await self.frappe.call(
												"frappe.client.set_value",
												{
													"doctype": "Memora Live Challenge Participation",
													"name": existing[0]["name"],
													"fieldname": json.dumps(
														{
															"score": r.get("score"),
															"submitted_at": r.get("submitted_at"),
															"answers_json": r.get("answers_json"),
														}
													),
												},
											)
									except Exception:
										logger.warning(
											"lc_reconcile_score_update_failed",
											event_id=event_id,
											player=doc["player"],
										)
								continue
							failed_players.append(doc["player"])
							logger.warning(
								"lc_participation_insert_failed",
								event_id=event_id,
								player=doc["player"],
								error=str(e),
							)

			# Sync counters to Frappe event
			count_synced = True
			submitted_count = len(results)
			try:
				await self.frappe.call(
					"frappe.client.set_value",
					{
						"doctype": "Memora Live Challenge Event",
						"name": event_id,
						"fieldname": json.dumps(
							{
								"participant_count": count,
								"submitted_count": submitted_count,
							}
						),
					},
				)
			except Exception:
				count_synced = False
				logger.warning("lc_participant_count_sync_failed", event_id=event_id)

			# Step 4 — Conditional cleanup (full success only)
			all_succeeded = not failed_players and count_synced
			if all_succeeded:
				await self.redis.set(lc_reconciled_key(event_id), "1", ex=LC_KEY_TTL)
				pipe = self.redis.pipeline()
				# Keep status key alive ("ended") — it is the single routing
				# signal for _resolve_event_source(). Refresh TTL so it
				# survives for a full 24h after reconciliation.
				pipe.expire(lc_status_key(event_id), LC_KEY_TTL)
				# Delete ephemeral keys — DB is now source of truth.
				pipe.delete(lc_count_key(event_id))
				pipe.delete(lc_meta_key(event_id))
				pipe.delete(lc_questions_key(event_id))
				pipe.delete(lc_joined_key(event_id))
				pipe.delete(lc_submitted_key(event_id))
				pipe.delete(lc_join_times_key(event_id))
				pipe.delete(lc_results_key(event_id))
				pipe.delete(lc_reconcile_lock_key(event_id))
				await pipe.execute()
				logger.info(
					"lc_reconciliation_complete",
					event_id=event_id,
					participant_count=count,
					submitted_count=submitted_count,
				)
			else:
				# Partial failure — release lock only, leave keys for retry
				await self.redis.delete(lc_reconcile_lock_key(event_id))
				logger.error(
					"lc_reconciliation_partial_failure",
					event_id=event_id,
					failed_players=failed_players,
					count_synced=count_synced,
				)
		except Exception:
			await self.redis.delete(lc_reconcile_lock_key(event_id))
			logger.exception("lc_reconciliation_error", event_id=event_id)

	# -------------------------------------------------------------------------
	# Grade (T022)
	# -------------------------------------------------------------------------

	async def grade(
		self,
		event_id: str,
		player_id: str,
		answers: list[dict],
	) -> dict[str, Any]:
		"""Grade submitted answers (pure Redis — DB persistence deferred to reconciliation).

		Returns dict with score, correct_count, total_questions, submitted_at, corrections.
		Raises ValueError with error code on failure.
		"""
		# 1. Check event is active + mark submitted atomically (2 RT → 1 pipeline)
		pipe = self.redis.pipeline()
		pipe.get(lc_status_key(event_id))
		pipe.sadd(lc_submitted_key(event_id), player_id)
		status, added = await pipe.execute()
		if status != "active":
			# Rollback SADD if we added to a non-active event
			if added:
				await self.redis.srem(lc_submitted_key(event_id), player_id)
			raise ValueError("EVENT_NOT_ACTIVE")

		# 2. Atomic mark-as-submitted: SADD returns 1 if newly added, 0 if already present
		if not added:
			raise ValueError("ALREADY_SUBMITTED")
		await self.redis.expire(lc_submitted_key(event_id), LC_KEY_TTL)

		# 3. Check is participant (Redis joined set — fail fast, no DB fallback)
		is_participant = await self.redis.sismember(lc_joined_key(event_id), player_id)
		if not is_participant:
			# Rollback: remove from submitted set
			await self.redis.srem(lc_submitted_key(event_id), player_id)
			raise ValueError("NOT_A_PARTICIPANT")

		# 4. Load questions + meta in one round trip
		q_pipe = self.redis.pipeline()
		q_pipe.get(lc_questions_key(event_id))
		q_pipe.hgetall(lc_meta_key(event_id))
		questions_json, meta = await q_pipe.execute()
		if not questions_json:
			# Rollback submitted mark
			await self.redis.srem(lc_submitted_key(event_id), player_id)
			raise ValueError("EVENT_NOT_ACTIVE")
		questions = json.loads(questions_json)

		# 5. Get show_correct_answers setting
		show_correct = bool(int(meta.get("show_correct_answers", "0")))

		# 6. Grade
		result = grade_answers(questions, answers, show_correct_answers=show_correct)
		submitted_at = _now_naive().strftime("%Y-%m-%d %H:%M:%S")
		result["submitted_at"] = submitted_at

		# 7. Build answers with correctness for persistence
		answers_record = []
		correct_map = {q["idx"]: q["correct_answer"] for q in questions}
		for a in answers:
			answers_record.append(
				{
					"question_idx": a["question_idx"],
					"selected": a["selected"],
					"correct": a["selected"] == correct_map.get(a["question_idx"]),
				}
			)

		# 8. Store result in Redis (pure Redis — zero Frappe calls).
		#    Frappe persistence is deferred to _reconcile_event() after event ends.
		result_payload = json.dumps(
			{
				"score": result["score"],
				"correct_count": result["correct_count"],
				"submitted_at": submitted_at,
				"answers_json": json.dumps({"answers": answers_record}),
			}
		)
		pipe = self.redis.pipeline()
		pipe.hset(lc_results_key(event_id), player_id, result_payload)
		pipe.expire(lc_results_key(event_id), LC_KEY_TTL)
		await pipe.execute()

		logger.info(
			"lc_submission_graded",
			event_id=event_id,
			player_id=player_id,
			score=result["score"],
			correct_count=result["correct_count"],
		)

		return result

	# -------------------------------------------------------------------------
	# Event Detail (T024) — explicit source selection
	# -------------------------------------------------------------------------

	async def _resolve_event_source(self, event_id: str) -> str | None:
		"""Determine the authoritative data source for an event.

		Single decision criterion: Redis status key value.

		Returns:
			"redis" — event is active/waiting/draft, Redis has the data
			"db"    — event has ended, Frappe DB is the source of truth
			None    — event does not exist
		"""
		status = await self.redis.get(lc_status_key(event_id))

		# Cold start: status key absent — hydrate once, then re-read.
		# SETNX guard prevents stampede: first request hydrates,
		# concurrent requests get None (client retries naturally).
		# 30s TTL = natural backoff if Frappe is down.
		if status is None:
			guard = await self.redis.set(
				f"memora:lc:{event_id}:hydrate_guard", "1", nx=True, ex=30,
			)
			if not guard:
				return None
			if not await self._hydrate_event_to_redis(event_id):
				return None
			status = await self.redis.get(lc_status_key(event_id))

		if status == "ended":
			return "db"
		if status is not None:
			return "redis"
		return None

	async def get_event_detail(self, event_id: str, player_id: str) -> dict[str, Any] | None:
		"""Get public event details with player-specific flags.

		Explicit source selection: ended events read from DB,
		active/waiting/draft events read from Redis. No implicit fallback.
		"""
		source = await self._resolve_event_source(event_id)
		if source is None:
			return None
		if source == "db":
			return await self._get_event_detail_from_db(event_id, player_id)
		return await self._get_event_detail_from_redis(event_id, player_id)

	async def _get_event_detail_from_redis(self, event_id: str, player_id: str) -> dict[str, Any] | None:
		"""Read event detail exclusively from Redis. MUST NOT fall back to DB.

		Single pipeline read — zero Frappe calls. Returns None if Redis data
		is missing (indicates a hydration or data integrity problem).
		"""
		pipe = self.redis.pipeline()
		pipe.get(lc_status_key(event_id))
		pipe.hgetall(lc_meta_key(event_id))
		pipe.get(lc_count_key(event_id))
		pipe.sismember(lc_joined_key(event_id), player_id)
		pipe.sismember(lc_submitted_key(event_id), player_id)
		pipe.get(lc_questions_key(event_id))
		redis_status, meta, count_raw, has_joined, has_submitted, questions_json = await pipe.execute()

		if not meta:
			logger.error(
				"lc_redis_meta_missing",
				event_id=event_id,
				redis_status=redis_status,
			)
			return None

		status = redis_status.capitalize() if redis_status else "Draft"
		eligible_plans = json.loads(meta.get("eligible_plans", "[]"))
		question_count = 0
		if questions_json:
			try:
				question_count = len(json.loads(questions_json))
			except (json.JSONDecodeError, TypeError):
				pass

		return {
			"event_id": event_id,
			"event_name": meta.get("event_name", ""),
			"description": meta.get("description") or None,
			"status": status,
			"scheduled_start": meta.get("scheduled_start", ""),
			"exam_start_ts": meta.get("exam_start_ts", "") or None,
			"exam_end_ts": meta.get("exam_end_ts", "") or None,
			"waiting_room_duration": int(meta.get("waiting_room_duration", "180")),
			"exam_duration": int(meta.get("exam_duration", "10")),
			"enable_question_timer": bool(int(meta.get("enable_question_timer", "0"))),
			"question_time_limit": int(meta.get("question_time_limit", "30")),
			"capacity": int(meta.get("capacity", "100")),
			"current_count": int(count_raw or "0"),
			"is_paid": bool(int(meta.get("is_paid", "0"))),
			"show_correct_answers": bool(int(meta.get("show_correct_answers", "0"))),
			"show_student_rank": bool(int(meta.get("show_student_rank", "0"))),
			"participation_xp": int(meta.get("participation_xp", "0")),
			"first_place_xp": int(meta.get("first_place_xp", "0")),
			"second_place_xp": int(meta.get("second_place_xp", "0")),
			"third_place_xp": int(meta.get("third_place_xp", "0")),
			"default_xp": int(meta.get("default_xp", "0")),
			"question_count": question_count,
			"eligible_plans": eligible_plans,
			"has_joined": bool(has_joined),
			"has_submitted": bool(has_submitted),
		}

	async def _get_event_detail_from_db(self, event_id: str, player_id: str) -> dict[str, Any] | None:
		"""Read event detail exclusively from Frappe DB. Used ONLY for ended events."""
		try:
			event = await self.frappe.call(
				"frappe.client.get",
				{"doctype": "Memora Live Challenge Event", "name": event_id},
			)
		except Exception:
			logger.exception("lc_db_event_fetch_failed", event_id=event_id)
			return None
		if not event:
			return None

		joined = False
		submitted = False
		try:
			participation = await self.frappe.call(
				"frappe.client.get_list",
				{
					"doctype": "Memora Live Challenge Participation",
					"filters": json.dumps([
						["event", "=", event_id],
						["player", "=", player_id],
					]),
					"fields": json.dumps(["name", "submitted_at"]),
					"limit_page_length": "1",
				},
			)
			if participation:
				joined = True
				submitted = bool(participation[0].get("submitted_at"))
		except Exception:
			logger.warning(
				"lc_participation_lookup_failed",
				event_id=event_id,
				player_id=player_id,
			)

		# Extract top 3 from leaderboard_json
		top_players = None
		lb_json = event.get("leaderboard_json")
		if lb_json:
			try:
				lb = json.loads(lb_json) if isinstance(lb_json, str) else lb_json
				top_players = lb[:3] if lb else None
			except (json.JSONDecodeError, TypeError):
				pass

		return {
			"event_id": event_id,
			"event_name": event.get("event_name", ""),
			"description": event.get("description") or None,
			"status": "Ended",
			"scheduled_start": str(event.get("scheduled_start", "")),
			"exam_start_ts": str(event.get("exam_start_ts", "")) or None,
			"exam_end_ts": str(event.get("exam_end_ts", "")) or None,
			"waiting_room_duration": int(event.get("waiting_room_duration", 180)),
			"exam_duration": int(event.get("exam_duration", 10)),
			"enable_question_timer": bool(event.get("enable_question_timer")),
			"question_time_limit": int(event.get("question_time_limit", 30)),
			"capacity": int(event.get("capacity", 100)),
			"current_count": int(event.get("participant_count", 0)),
			"is_paid": bool(event.get("is_paid")),
			"show_correct_answers": bool(event.get("show_correct_answers")),
			"show_student_rank": bool(event.get("show_student_rank")),
			"participation_xp": int(event.get("participation_xp", 0)),
			"first_place_xp": int(event.get("first_place_xp", 0)),
			"second_place_xp": int(event.get("second_place_xp", 0)),
			"third_place_xp": int(event.get("third_place_xp", 0)),
			"default_xp": int(event.get("default_xp", 0)),
			"question_count": len(event.get("questions", [])),
			"eligible_plans": [ep.get("plan") for ep in event.get("eligible_plans", [])],
			"has_joined": joined,
			"has_submitted": submitted,
			"top_players": top_players,
		}

	# -------------------------------------------------------------------------
	# Questions REST fallback
	# -------------------------------------------------------------------------

	async def get_questions(self, event_id: str, player_id: str) -> dict[str, Any]:
		"""Get exam questions via REST (fallback when WebSocket is unavailable).

		Raises ValueError with error code on failure:
		- EVENT_NOT_ACTIVE: status is not "active" or questions missing
		- NOT_A_PARTICIPANT: player not in joined set
		- ALREADY_SUBMITTED: player already submitted answers
		"""
		status = await self.redis.get(lc_status_key(event_id))
		if status != "active":
			raise ValueError("EVENT_NOT_ACTIVE")

		is_joined = await self.redis.sismember(lc_joined_key(event_id), player_id)
		if not is_joined:
			raise ValueError("NOT_A_PARTICIPANT")

		has_submitted = await self.redis.sismember(lc_submitted_key(event_id), player_id)
		if has_submitted:
			raise ValueError("ALREADY_SUBMITTED")

		questions_json = await self.redis.get(lc_questions_key(event_id))
		if not questions_json:
			raise ValueError("EVENT_NOT_ACTIVE")

		questions = json.loads(questions_json)
		safe_questions = _strip_correct_answers(questions)
		meta = await self.redis.hgetall(lc_meta_key(event_id))

		return {
			"event_id": event_id,
			"exam_end_ts": meta.get("exam_end_ts", ""),
			"total_questions": len(safe_questions),
			"enable_question_timer": bool(int(meta.get("enable_question_timer", "0"))),
			"question_time_limit": int(meta.get("question_time_limit", "30")),
			"questions": safe_questions,
		}

	# -------------------------------------------------------------------------
	# Result & Leaderboard (T035/T036)
	# -------------------------------------------------------------------------

	async def get_result(self, event_id: str, player_id: str) -> dict[str, Any] | None:
		"""Get student's own result for an event.

		Returns None if no participation found.
		"""
		try:
			parts = await self.frappe.call(
				"frappe.client.get_list",
				{
					"doctype": "Memora Live Challenge Participation",
					"filters": json.dumps([["event", "=", event_id], ["player", "=", player_id]]),
					"fields": json.dumps([
						"name", "score", "rank", "xp_awarded",
						"submitted_at", "answers_json",
					]),
					"limit_page_length": "1",
				},
			)
			if not parts:
				return None
			part = parts[0]
		except Exception:
			logger.error("lc_get_result_failed", event_id=event_id, player_id=player_id)
			return None

		# Get event info for context
		try:
			event = await self.frappe.call(
				"frappe.client.get",
				{
					"doctype": "Memora Live Challenge Event",
					"name": event_id,
				},
			)
		except Exception:
			event = {}

		event_name = event.get("event_name", "")
		show_correct = bool(int(event.get("show_correct_answers", 0)))

		# Count total participants
		total_participants = 0
		try:
			total_participants = await self.frappe.call(
				"frappe.client.get_count",
				{
					"doctype": "Memora Live Challenge Participation",
					"filters": json.dumps({"event": event_id}),
				},
			)
		except Exception:
			pass

		# Parse answers_json for correct_count / total_questions
		correct_count = 0
		total_questions = 0
		answers_json_raw = part.get("answers_json")
		answers_list = []
		if answers_json_raw:
			try:
				answers_data = json.loads(answers_json_raw) if isinstance(answers_json_raw, str) else answers_json_raw
				answers_list = answers_data.get("answers", [])
				total_questions = len(answers_list)
				correct_count = sum(1 for a in answers_list if a.get("correct", False))
			except (json.JSONDecodeError, KeyError, TypeError):
				pass

		# Build corrections if show_correct_answers is enabled.
		# answers_json stores {correct: bool} but not the actual correct_answer,
		# so we need to look up correct answers from the event's questions.
		corrections = None
		if show_correct and answers_list:
			# Build correct_answer map from event questions
			questions = event.get("questions", [])
			correct_map: dict[int, str] = {}
			if isinstance(questions, list):
				for q in questions:
					if isinstance(q, dict):
						# Frappe child table idx is 1-based, our question_idx is 0-based
						idx = int(q.get("idx", 0)) - 1
						correct_map[idx] = q.get("correct_answer", "")

			corrections = [
				{
					"question_idx": a["question_idx"],
					"selected": a.get("selected"),
					"correct_answer": correct_map.get(a["question_idx"], ""),
				}
				for a in answers_list
				if not a.get("correct", False)
			]

		rank = part.get("rank")
		xp_awarded = part.get("xp_awarded")

		return {
			"event_id": event_id,
			"event_name": event_name,
			"score": float(part.get("score") or 0),
			"correct_count": correct_count,
			"total_questions": total_questions,
			"rank": int(rank) if rank else None,
			"total_participants": int(total_participants or 0),
			"xp_awarded": int(xp_awarded) if xp_awarded else None,
			"submitted_at": str(part.get("submitted_at", "")) if part.get("submitted_at") else None,
			"corrections": corrections,
		}

	async def get_leaderboard(self, event_id: str, player_id: str) -> dict[str, Any] | None:
		"""Get top 20 leaderboard for an ended event.

		Returns None if event is not in Ended status.
		"""
		try:
			event = await self.frappe.call(
				"frappe.client.get",
				{
					"doctype": "Memora Live Challenge Event",
					"name": event_id,
				},
			)
		except Exception:
			return None

		if not event or event.get("status") != "Ended":
			return None

		# Parse leaderboard_json
		leaderboard = []
		lb_json = event.get("leaderboard_json")
		if lb_json:
			try:
				leaderboard = json.loads(lb_json) if isinstance(lb_json, str) else lb_json
			except (json.JSONDecodeError, TypeError):
				pass

		# Get player's own rank/score if show_student_rank enabled
		my_rank = None
		my_score = None
		show_student_rank = bool(int(event.get("show_student_rank", 0)))
		if show_student_rank:
			try:
				parts = await self.frappe.call(
					"frappe.client.get_list",
					{
						"doctype": "Memora Live Challenge Participation",
						"filters": json.dumps([["event", "=", event_id], ["player", "=", player_id]]),
						"fields": json.dumps(["rank", "score"]),
						"limit_page_length": "1",
					},
				)
				if parts:
					my_rank = int(parts[0]["rank"]) if parts[0].get("rank") else None
					my_score = float(parts[0]["score"]) if parts[0].get("score") is not None else None
			except Exception:
				pass

		total_participants = int(event.get("participant_count", 0))

		return {
			"event_id": event_id,
			"event_name": event.get("event_name", ""),
			"status": event.get("status", ""),
			"leaderboard": leaderboard,
			"my_rank": my_rank,
			"my_score": my_score,
			"total_participants": total_participants,
		}

	# -------------------------------------------------------------------------
	# Waiting Room Reactions (T005)
	# -------------------------------------------------------------------------

	async def handle_reaction_tap(self, event_id: str, player_id: str, msg: dict) -> None:
		"""Handle a reaction tap from a player in the waiting room.

		Validates room status is 'waiting' before delegating to the engine.
		Silently drops taps in any other state. Errors are isolated — no
		reaction failure can propagate to the WS connection or countdown logic.
		"""
		try:
			status = await self.redis.get(lc_status_key(event_id))
			if status != "waiting":
				logger.debug("reaction_tap_dropped_status", event_id=event_id, status=status)
				return
			reaction = msg.get("reaction", "")
			logger.debug("reaction_tap_delegating", event_id=event_id, player_id=player_id, reaction=reaction)
			accepted = await self._reaction_engine.accept_tap(event_id, player_id, reaction)
			logger.debug("reaction_tap_result", event_id=event_id, accepted=accepted, reaction=reaction)
		except Exception:
			logger.warning("reaction_tap_handler_error", event_id=event_id, player_id=player_id, exc_info=True)

	# -------------------------------------------------------------------------
	# WebSocket Connection Tracking (T029)
	# -------------------------------------------------------------------------

	def register_connection(self, event_id: str, ws: WebSocket) -> None:
		"""Register a WebSocket connection for an event."""
		is_first = event_id not in self._ws_connections
		if is_first:
			self._ws_connections[event_id] = set()
		self._ws_connections[event_id].add(ws)
		if is_first:
			asyncio.create_task(self._reaction_engine.subscribe_event(event_id))

	def remove_connection(self, event_id: str, ws: WebSocket) -> None:
		"""Remove a WebSocket connection for an event."""
		conns = self._ws_connections.get(event_id)
		if conns:
			conns.discard(ws)
			if not conns:
				del self._ws_connections[event_id]
				asyncio.create_task(self._reaction_engine.unsubscribe_event(event_id))

	def get_connected_count(self, event_id: str) -> int:
		"""Return number of active WebSocket connections for an event."""
		return len(self._ws_connections.get(event_id, set()))

	async def _broadcast_json(self, event_id: str, message: dict) -> int:
		"""Send JSON message to all connected WebSockets for an event.

		Sends concurrently (up to _BROADCAST_CONCURRENCY at a time) with a
		per-connection timeout so one slow client cannot block the broadcast.
		Returns count of successful sends. Removes dead connections.
		"""
		conns = list(self._ws_connections.get(event_id, set()))
		if not conns:
			return 0

		# Pre-serialize once for all connections
		payload = json.dumps(message)
		sem = asyncio.Semaphore(_BROADCAST_CONCURRENCY)
		dead: list[WebSocket] = []
		sent = 0

		async def _send(ws: WebSocket) -> bool:
			async with sem:
				try:
					await asyncio.wait_for(ws.send_text(payload), timeout=2.0)
					return True
				except Exception:
					dead.append(ws)
					return False

		# Process in chunks to limit coroutine creation pressure at 10k+ scale
		chunk_size = _BROADCAST_CONCURRENCY * 2
		for i in range(0, len(conns), chunk_size):
			chunk = conns[i : i + chunk_size]
			results = await asyncio.gather(*[_send(ws) for ws in chunk])
			sent += sum(1 for r in results if r)

		for ws in dead:
			self.remove_connection(event_id, ws)

		return sent

	# -------------------------------------------------------------------------
	# Countdown + Transition Broadcast (T029 + T031)
	# -------------------------------------------------------------------------

	def start_countdown_loop(self, event_id: str) -> None:
		"""Start the periodic countdown broadcast loop for an event.

		Called when the first WebSocket connects during Waiting status.
		The loop sends countdown updates every 1s, triggers exam_start
		when exam_start_ts is reached, and sends event_ended when
		exam_end_ts is reached.
		"""
		if event_id in self._countdown_tasks:
			return  # Already running
		task = asyncio.create_task(self._countdown_loop(event_id))
		self._countdown_tasks[event_id] = task

	def stop_countdown_loop(self, event_id: str) -> None:
		"""Cancel the countdown loop for an event."""
		task = self._countdown_tasks.pop(event_id, None)
		if task and not task.done():
			task.cancel()

	async def _countdown_loop(self, event_id: str) -> None:
		"""Background loop: broadcast countdown, trigger exam_start and event_ended."""
		try:
			meta = await self.redis.hgetall(lc_meta_key(event_id))
			exam_start_ts_str = meta.get("exam_start_ts", "")
			exam_end_ts_str = meta.get("exam_end_ts", "")
			enable_timer = bool(int(meta.get("enable_question_timer", "0")))
			q_time_limit = int(meta.get("question_time_limit", "30"))

			if not exam_start_ts_str or not exam_end_ts_str:
				logger.warning("lc_countdown_missing_timestamps", event_id=event_id)
				return

			exam_start_ts = datetime.strptime(exam_start_ts_str, "%Y-%m-%d %H:%M:%S")
			exam_end_ts = datetime.strptime(exam_end_ts_str, "%Y-%m-%d %H:%M:%S")

			# Check if already active (avoid duplicate exam_start broadcast — Finding 6)
			initial_status = await self.redis.get(lc_status_key(event_id))
			exam_started = initial_status == "active"

			while True:
				if self._shutting_down:
					return

				now = _now_naive()
				status = await self.redis.get(lc_status_key(event_id))

				# Event ended (by scheduled task or another worker)
				if status == "ended":
					self._reaction_engine.stop_room(event_id)
					await self._reconcile_event(event_id)
					await self._broadcast_event_ended(event_id)
					return

				# Check if we should transition to Active
				if not exam_started and now >= exam_start_ts:
					# Authoritative start: set Redis status to active
					await self.redis.set(lc_status_key(event_id), "active", ex=LC_KEY_TTL)
					self._reaction_engine.stop_room(event_id)
					await self._broadcast_exam_start(event_id, meta)
					exam_started = True
					logger.info("lc_exam_started_by_ws", event_id=event_id)

				# Check if exam has ended
				if exam_started and now >= exam_end_ts:
					await self.redis.set(lc_status_key(event_id), "ended", ex=LC_KEY_TTL)  # freeze joins/submits
					self._reaction_engine.stop_room(event_id)
					await self._reconcile_event(event_id)
					await self._broadcast_event_ended(event_id)
					logger.info("lc_exam_ended_by_ws", event_id=event_id)
					return

				# Still waiting — send countdown
				if not exam_started:
					remaining = max(0, int((exam_start_ts - now).total_seconds()))
					participant_count = int(await self.redis.get(lc_count_key(event_id)) or "0")
					await self._broadcast_json(
						event_id,
						{
							"type": "countdown",
							"remaining": remaining,
							"participant_count": participant_count,
						},
					)

				# No connected clients — stop the loop
				if self.get_connected_count(event_id) == 0:
					return

				await asyncio.sleep(1)

		except asyncio.CancelledError:
			return
		except Exception:
			logger.exception("lc_countdown_loop_error", event_id=event_id)
		finally:
			self._countdown_tasks.pop(event_id, None)

	async def _broadcast_exam_start(self, event_id: str, meta: dict) -> None:
		"""Broadcast exam_start with questions (sans correct_answer) to all clients."""
		questions_json = await self.redis.get(lc_questions_key(event_id))
		if not questions_json:
			return

		questions = json.loads(questions_json)
		safe_questions = _strip_correct_answers(questions)
		msg = _build_exam_start_msg(safe_questions, meta)

		sent = await self._broadcast_json(event_id, msg)
		logger.info("lc_exam_start_broadcast", event_id=event_id, sent=sent)

	async def _broadcast_event_ended(self, event_id: str) -> None:
		"""Broadcast event_ended to all connected clients."""
		sent = await self._broadcast_json(event_id, {"type": "event_ended"})
		logger.info("lc_event_ended_broadcast", event_id=event_id, sent=sent)

	async def send_exam_start_to_client(self, event_id: str, ws: WebSocket) -> None:
		"""Send exam_start to a single client (late join / reconnect during Active)."""
		meta = await self.redis.hgetall(lc_meta_key(event_id))
		questions_json = await self.redis.get(lc_questions_key(event_id))
		if not questions_json:
			return

		questions = json.loads(questions_json)
		safe_questions = _strip_correct_answers(questions)
		msg = _build_exam_start_msg(safe_questions, meta)

		try:
			await ws.send_text(json.dumps(msg))
		except Exception:
			logger.debug("lc_send_exam_start_failed", event_id=event_id)
