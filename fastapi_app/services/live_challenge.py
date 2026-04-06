"""Live Challenge service — join, grade, WebSocket management, post-event reconciliation."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog
from fastapi import WebSocket

from fastapi_app.core.config import get_settings
from fastapi_app.core.redis_keys import (
	LC_KEY_TTL,
	lc_alive_key,
	lc_answered_counts_key,
	lc_correct_counts_key,
	lc_count_key,
	lc_eliminated_at_key,
	lc_eliminated_key,
	lc_engine_lock_key,
	lc_hearts_key,
	lc_join_times_key,
	lc_joined_key,
	lc_meta_key,
	lc_mode_key,
	lc_questions_key,
	lc_reconcile_lock_key,
	lc_reconciled_key,
	lc_response_times_key,
	lc_results_key,
	lc_round_broadcast_channel,
	lc_round_key,
	lc_status_key,
	lc_submitted_key,
)
from fastapi_app.models.live_challenge import _fmt_score
from fastapi_app.services.waiting_room_reactions import ReactionEngine

if TYPE_CHECKING:
	from fastapi_app.services.frappe_client import FrappeClient
	from fastapi_app.services.last_stand_engine import LastStandEngine

logger = structlog.get_logger()

_SYSTEM_TZ: ZoneInfo | None = None


def _now_naive() -> datetime:
	"""Return current time in the Frappe system timezone as a naive datetime.

	Frappe stores Datetime fields in the system timezone (e.g. Asia/Amman).
	The server OS clock may differ (e.g. UTC), so we explicitly convert to
	match what the admin entered and what is stored in the DB / Redis.
	"""
	global _SYSTEM_TZ
	if _SYSTEM_TZ is None:
		_SYSTEM_TZ = ZoneInfo(get_settings().system_timezone)
	return datetime.now(_SYSTEM_TZ).replace(tzinfo=None)


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
local cap = tonumber(ARGV[2])
if cap > 0 then
    local current = tonumber(redis.call('GET', KEYS[3]) or '0')
    if current >= cap then
        return -1
    end
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
) -> dict[str, Any]:
	"""Grade submitted answers against correct answers (pure function).

	Args:
		questions: List of question dicts with 'idx' and 'correct_answer' fields.
		answers: List of answer dicts with 'question_idx' and 'selected' fields.

	Returns:
		Dict with score, correct_count, and total_questions.
	"""
	total = len(questions)
	correct_count = 0

	# Build lookup: question_idx -> selected
	answer_map = {a["question_idx"]: a["selected"] for a in answers}

	for q in questions:
		selected = answer_map.get(q["idx"])
		if selected == q["correct_answer"]:
			correct_count += 1

	score = round((correct_count / total) * 100, 1) if total > 0 else 0

	return {
		"score": score,
		"correct_count": correct_count,
		"total_questions": total,
	}


class LiveChallengeService:
	def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient):
		self.redis = redis_client
		self.frappe = frappe_client
		self._shutting_down = False
		self._join_script: Any = None
		self._cas_script: Any = None
		self._answer_script: Any = None
		# WebSocket connection tracking: event_id -> set[WebSocket]
		self._ws_connections: dict[str, set[WebSocket]] = {}
		# WebSocket → player_id mapping for personalized broadcasts
		self._ws_player_map: dict[str, dict[WebSocket, str]] = {}
		# Per-event countdown loop tasks
		self._countdown_tasks: dict[str, asyncio.Task] = {}
		# Last Stand engine instances and tasks
		self._engines: dict[str, LastStandEngine] = {}
		self._engine_tasks: dict[str, asyncio.Task] = {}
		# Waiting room reaction engine
		self._reaction_engine = ReactionEngine(
			settings=get_settings(),
			broadcast=self._broadcast_json,
			redis=self.redis,
		)
		# Cross-worker Last Stand round broadcast subscriber
		self._round_subscriber_task: asyncio.Task | None = None
		self._round_pubsub: Any = None
		self._round_subscribed_channels: set[str] = set()

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
			questions.append(
				{
					"idx": (int(raw_idx) - 1) if raw_idx is not None else 0,
					"question_text": q.get("question_text", ""),
					"option_a": q.get("option_a", ""),
					"option_b": q.get("option_b", ""),
					"option_c": q.get("option_c", ""),
					"option_d": q.get("option_d", ""),
					"correct_answer": q.get("correct_answer", ""),
				}
			)
		pipe.set(lc_questions_key(event_id), json.dumps(questions), ex=LC_KEY_TTL)

		# Meta hash — includes ALL fields needed by get_event_detail (Redis-only reads)
		eligible_plans = [
			ep.get("plan", "") for ep in (event.get("eligible_plans") or []) if isinstance(ep, dict)
		]
		mode = event.get("mode") or "exam"
		meta = {
			"scheduled_start": str(event.get("scheduled_start", "")),
			"exam_start_ts": str(event.get("exam_start_ts", "")),
			"exam_end_ts": str(event.get("exam_end_ts", "")),
			"capacity": str(event.get("capacity", 0)),
			"enable_question_timer": str(int(event.get("enable_question_timer", 0))),
			"question_time_limit": str(event.get("question_time_limit", 30)),
			"waiting_room_duration": str(event.get("waiting_room_duration", 180)),
			"eligible_plans": json.dumps(eligible_plans),
			"event_name": event.get("event_name", ""),
			"description": event.get("description") or "",
			"exam_duration": str(event.get("exam_duration", 10)),
			"is_paid": str(int(event.get("is_paid", 0))),
			"rewards_json": json.dumps(
				[
					{
						"rank": r.get("rank", 0),
						"reward_type": r.get("reward_type", "XP"),
						"xp_amount": r.get("xp_amount") or 0,
						"prize_description": r.get("prize_description") or "",
					}
					for r in (event.get("rewards") or [])
				]
			),
			"mode": mode,
			"starting_hearts": str(event.get("starting_hearts", 3)),
			"result_window_duration": str(event.get("result_window_duration", 3)),
		}
		meta_key = lc_meta_key(event_id)
		# hset with all fields is an atomic overwrite — no delete needed
		pipe.hset(meta_key, mapping=meta)
		pipe.expire(meta_key, LC_KEY_TTL)

		# Mode key (fast lookup without HGET on meta)
		pipe.set(lc_mode_key(event_id), mode, ex=LC_KEY_TTL)

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
			logger.warning(
				"lc_status_bad_timestamps",
				event_id=event_id,
				exam_start_ts=exam_start_raw,
				exam_end_ts=exam_end_raw,
			)
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
			resolved_status = current_status
		else:
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

		resp: dict[str, Any] = {"status": resolved_status, "participant_count": participant_count}

		# Enrich with Last Stand stats when applicable
		mode = meta.get("mode", "exam")
		resp["mode"] = mode
		if mode == "last_stand":
			if resolved_status == "active":
				ls_pipe = self.redis.pipeline()
				ls_pipe.scard(lc_alive_key(event_id))
				ls_pipe.scard(lc_eliminated_key(event_id))
				ls_pipe.hgetall(lc_round_key(event_id))
				ls_pipe.get(lc_questions_key(event_id))
				alive, eliminated, round_data, qs_raw = await ls_pipe.execute()
				resp["alive_count"] = alive
				resp["eliminated_count"] = eliminated
				resp["current_round"] = int(round_data.get("question_idx", "0")) if round_data else 0
				try:
					resp["total_rounds"] = len(json.loads(qs_raw)) if qs_raw else 0
				except (json.JSONDecodeError, TypeError):
					resp["total_rounds"] = 0

		return resp

	# -------------------------------------------------------------------------
	# Lifecycle
	# -------------------------------------------------------------------------

	async def start_reaction_subscriber(self) -> None:
		"""Start the cross-worker reaction burst pub/sub subscriber."""
		await self._reaction_engine.start_subscriber()

	# -------------------------------------------------------------------------
	# Cross-worker Last Stand round broadcast (pub/sub)
	# -------------------------------------------------------------------------

	async def start_round_subscriber(self) -> None:
		"""Start the cross-worker round broadcast pub/sub subscriber.

		Creates a pubsub object with no initial subscriptions.  Events are
		subscribed dynamically when the first WS connects (register_connection).
		Mirrors ReactionEngine.start_subscriber.
		"""
		if self._round_subscriber_task is not None:
			return
		self._round_subscriber_task = asyncio.create_task(self._round_subscriber_loop())

	async def _round_subscriber_loop(self) -> None:
		"""Poll for round broadcast messages on Redis pub/sub and relay locally.

		Each worker runs this loop.  The engine worker publishes round messages
		to the channel; every worker (including the engine worker) receives them
		and calls the local _broadcast_json / _broadcast_personalized.
		"""
		pool = self.redis.connection_pool
		client = redis.Redis(connection_pool=pool)
		try:
			self._round_pubsub = client.pubsub()
			logger.info("round_subscriber_started")

			while True:
				try:
					message = await self._round_pubsub.get_message(
						ignore_subscribe_messages=True,
						timeout=0.1,
					)
				except asyncio.CancelledError:
					raise
				except Exception:
					await asyncio.sleep(0.1)
					continue

				if message is None:
					await asyncio.sleep(0.01)
					continue

				try:
					data = message["data"]
					if isinstance(data, bytes):
						data = data.decode("utf-8")
					envelope = json.loads(data)
					event_id = envelope.get("event_id", "")
					if not event_id:
						continue

					btype = envelope.get("broadcast_type")
					if btype == "json":
						await self._broadcast_json(event_id, envelope["message"])
					elif btype == "personalized":
						await self._broadcast_personalized(
							event_id,
							envelope["base_msg"],
							envelope["player_states"],
						)
				except Exception:
					logger.warning("round_subscriber_message_error", exc_info=True)
					continue
		except asyncio.CancelledError:
			logger.info("round_subscriber_cancelled")
			raise
		except Exception:
			logger.error("round_subscriber_error", exc_info=True)
			raise
		finally:
			try:
				if self._round_pubsub is not None:
					for ch in list(self._round_subscribed_channels):
						await self._round_pubsub.unsubscribe(ch)
					self._round_subscribed_channels.clear()
				await client.aclose()
			except Exception:
				logger.debug("round_subscriber_cleanup_error", exc_info=True)

	async def stop_round_subscriber(self) -> None:
		"""Cancel the round subscriber loop and clean up."""
		if self._round_subscriber_task is not None:
			self._round_subscriber_task.cancel()
			try:
				await self._round_subscriber_task
			except asyncio.CancelledError:
				pass
			self._round_subscriber_task = None

	async def _subscribe_round_event(self, event_id: str) -> None:
		"""Subscribe this worker to round broadcasts for an event."""
		if self._round_pubsub is None:
			return
		channel = lc_round_broadcast_channel(event_id)
		if channel not in self._round_subscribed_channels:
			try:
				await self._round_pubsub.subscribe(channel)
				self._round_subscribed_channels.add(channel)
			except Exception:
				logger.warning("round_subscribe_error", event_id=event_id)

	async def _unsubscribe_round_event(self, event_id: str) -> None:
		"""Unsubscribe this worker from round broadcasts for an event."""
		if self._round_pubsub is None:
			return
		channel = lc_round_broadcast_channel(event_id)
		if channel in self._round_subscribed_channels:
			try:
				await self._round_pubsub.unsubscribe(channel)
				self._round_subscribed_channels.discard(channel)
			except Exception:
				logger.warning("round_unsubscribe_error", event_id=event_id)

	async def _publish_round_broadcast(self, event_id: str, envelope: dict) -> bool:
		"""Publish a round broadcast envelope to Redis pub/sub.

		Returns True if published, False on failure (caller falls back to local).
		"""
		try:
			channel = lc_round_broadcast_channel(event_id)
			payload = json.dumps(envelope)
			await self.redis.publish(channel, payload)
			return True
		except Exception:
			logger.warning("round_broadcast_publish_error", event_id=event_id)
			return False

	async def _engine_broadcast_json(self, event_id: str, message: dict) -> int:
		"""Pub/sub wrapper for _broadcast_json — used by LastStandEngine.

		Publishes to Redis so ALL workers relay to their local connections.
		Falls back to direct local broadcast if publish fails.
		"""
		envelope = {
			"event_id": event_id,
			"broadcast_type": "json",
			"message": message,
		}
		if await self._publish_round_broadcast(event_id, envelope):
			return 0
		return await self._broadcast_json(event_id, message)

	async def _engine_broadcast_personalized(
		self,
		event_id: str,
		base_msg: dict,
		player_states: dict[str, dict],
	) -> int:
		"""Pub/sub wrapper for _broadcast_personalized — used by LastStandEngine.

		Publishes to Redis so ALL workers relay to their local connections.
		Falls back to direct local broadcast if publish fails.
		"""
		envelope = {
			"event_id": event_id,
			"broadcast_type": "personalized",
			"base_msg": base_msg,
			"player_states": player_states,
		}
		if await self._publish_round_broadcast(event_id, envelope):
			return 0
		return await self._broadcast_personalized(event_id, base_msg, player_states)

	async def resume_active_last_stand_events(self) -> None:
		"""Startup scan: resume LastStandEngine for any Active Last Stand events.

		Called once during FastAPI startup.  Queries Frappe for Active events
		with mode=last_stand, checks Redis round state, and resumes engines
		from stored state (fast-forwards missed rounds if phase_end_ts < now).
		"""
		try:
			active_events = await self.frappe.call(
				"frappe.client.get_list",
				{
					"doctype": "Memora Live Challenge Event",
					"filters": {"status": "Active", "mode": "last_stand"},
					"fields": ["name"],
					"limit_page_length": 0,
				},
			)
		except Exception:
			logger.exception("last_stand_startup_scan_frappe_failed")
			return

		if not active_events:
			return

		for ev in active_events:
			event_id = ev["name"]
			try:
				# Check if engine already running (shouldn't happen on fresh startup)
				if event_id in self._engines:
					continue

				# Check Redis state exists
				redis_status = await self.redis.get(lc_status_key(event_id))
				if redis_status != "active":
					logger.info(
						"last_stand_startup_skip_not_active",
						event_id=event_id,
						redis_status=redis_status,
					)
					continue

				round_data = await self.redis.hgetall(lc_round_key(event_id))
				if not round_data:
					# No round state — Redis was lost. End the event via reconciliation.
					logger.warning(
						"last_stand_startup_no_round_state",
						event_id=event_id,
					)
					await self.redis.set(lc_status_key(event_id), "ended", ex=LC_KEY_TTL)
					asyncio.create_task(self._reconcile_event(event_id))
					continue

				phase = round_data.get("phase", "")
				if phase == "ended":
					# Engine already finished — just ensure reconciliation
					logger.info("last_stand_startup_already_ended", event_id=event_id)
					asyncio.create_task(self._reconcile_event(event_id))
					continue

				# Resume engine from stored state
				await self._start_last_stand_engine(event_id)
				logger.info(
					"last_stand_startup_resumed",
					event_id=event_id,
					round_phase=phase,
					round_question_idx=round_data.get("question_idx", "?"),
				)

			except Exception:
				logger.exception("last_stand_startup_resume_failed", event_id=event_id)

	async def shutdown(self):
		"""Signal all background loops to stop and cancel countdown tasks."""
		self._shutting_down = True
		await self._reaction_engine.stop_subscriber()
		await self.stop_round_subscriber()
		for event_id in list(self._countdown_tasks):
			self.stop_countdown_loop(event_id)
		# Stop all Last Stand engines and release their locks
		for event_id, engine in self._engines.items():
			engine.stop()
			await self.redis.delete(lc_engine_lock_key(event_id))
		for task in self._engine_tasks.values():
			if not task.done():
				task.cancel()
		self._engines.clear()
		self._engine_tasks.clear()

	# -------------------------------------------------------------------------
	# Last Stand Engine Lifecycle
	# -------------------------------------------------------------------------

	async def _start_last_stand_engine(self, event_id: str) -> None:
		"""Create and start the LastStandEngine for an event.

		Supports both fresh starts and crash-recovery resumes.  On resume,
		reads the round HASH to determine which question index to resume
		from.  For rounds missed during downtime (phase_end_ts < now),
		alive players who didn't answer lose a heart.

		Multi-worker safety: uses a Redis SETNX lock so only one worker
		runs the engine per event.  Other workers skip silently.
		"""
		# Guard: only one worker runs the engine (multi-worker safety)
		acquired = await self.redis.set(
			lc_engine_lock_key(event_id),
			"1",
			nx=True,
			ex=LC_KEY_TTL,
		)
		if not acquired:
			logger.info(
				"last_stand_engine_lock_held",
				event_id=event_id,
			)
			return

		from fastapi_app.services.last_stand_engine import LastStandEngine

		try:
			questions_json = await self.redis.get(lc_questions_key(event_id))
			if not questions_json:
				logger.error("last_stand_no_questions", event_id=event_id)
				return
			questions = json.loads(questions_json)
			meta = await self.redis.hgetall(lc_meta_key(event_id))

			# Determine resume point from Redis round state
			resume_from_idx = 0
			round_data = await self.redis.hgetall(lc_round_key(event_id))
			if round_data and round_data.get("question_idx"):
				stored_idx = int(round_data["question_idx"])
				phase = round_data.get("phase", "")
				phase_end_ts = float(round_data.get("phase_end_ts", "0"))
				now = time.time()

				if phase_end_ts < now:
					# Phase expired during downtime — fast-forward past this round
					# Deduct hearts for alive players who didn't answer the missed round
					await self._fast_forward_missed_round(event_id, round_data, questions)
					resume_from_idx = stored_idx + 1
				elif phase == "result":
					# In result window but not expired — resume from NEXT round
					resume_from_idx = stored_idx + 1
				else:
					# Still in answer window — resume from THIS round
					resume_from_idx = stored_idx

				if resume_from_idx > 0:
					logger.info(
						"last_stand_engine_resuming",
						event_id=event_id,
						resume_from_idx=resume_from_idx,
						stored_idx=stored_idx,
						phase=phase,
					)

			engine = LastStandEngine(
				redis_client=self.redis,
				event_id=event_id,
				questions=questions,
				meta=meta,
				broadcast_json=self._engine_broadcast_json,
				broadcast_personalized=self._engine_broadcast_personalized,
				on_event_ended=self._on_last_stand_ended,
				resume_from_idx=resume_from_idx,
			)
			self._engines[event_id] = engine
			task = asyncio.create_task(engine.run())
			self._engine_tasks[event_id] = task
			logger.info(
				"last_stand_engine_started",
				event_id=event_id,
				total_rounds=len(questions),
				resume_from_idx=resume_from_idx,
			)
		except Exception:
			# Release lock on any failure so another worker can retry
			await self.redis.delete(lc_engine_lock_key(event_id))
			raise

	async def _fast_forward_missed_round(
		self,
		event_id: str,
		round_data: dict,
		questions: list[dict],
	) -> None:
		"""Fast-forward a round that expired during downtime (crash recovery).

		For alive players who didn't answer, deduct a heart and eliminate if <= 0.
		"""
		from fastapi_app.core.redis_keys import lc_round_answers_key

		round_id = round_data.get("round_id", "")
		question_idx = int(round_data.get("question_idx", "0"))

		# Guard: if hearts were already deducted for this round before the crash,
		# skip deduction to avoid double-penalizing players (S-1).
		if round_data.get("hearts_deducted") == "1":
			logger.info(
				"last_stand_fast_forward_skip_hearts_already_deducted",
				event_id=event_id,
				question_idx=question_idx,
			)
			return

		# Get alive players and who already answered
		alive_members = await self.redis.smembers(lc_alive_key(event_id))
		if not alive_members:
			return

		answered = set()
		if round_id:
			answered_raw = await self.redis.hkeys(lc_round_answers_key(event_id, round_id))
			answered = set(answered_raw)

		# Unanswered alive players lose a heart
		unanswered = [pid for pid in alive_members if pid not in answered]
		if not unanswered:
			return

		pipe = self.redis.pipeline()
		for pid in unanswered:
			pipe.hincrby(lc_hearts_key(event_id), pid, -1)
		new_hearts_list = await pipe.execute()

		# Eliminate players with hearts <= 0
		to_eliminate = [pid for pid, nh in zip(unanswered, new_hearts_list) if int(nh) <= 0]
		if to_eliminate:
			elim_pipe = self.redis.pipeline()
			for pid in to_eliminate:
				elim_pipe.smove(
					lc_alive_key(event_id),
					lc_eliminated_key(event_id),
					pid,
				)
				elim_pipe.hset(
					lc_eliminated_at_key(event_id),
					pid,
					str(question_idx),
				)
			elim_pipe.expire(lc_eliminated_key(event_id), LC_KEY_TTL)
			elim_pipe.expire(lc_eliminated_at_key(event_id), LC_KEY_TTL)
			await elim_pipe.execute()

		logger.info(
			"last_stand_fast_forward",
			event_id=event_id,
			question_idx=question_idx,
			unanswered_count=len(unanswered),
			eliminated_count=len(to_eliminate),
		)

	async def _on_last_stand_ended(
		self,
		event_id: str,
		reason: str,
		alive_count: int,
		rounds_played: int,
	) -> None:
		"""Callback from LastStandEngine when the event ends."""
		# Release engine lock so another worker can start on crash-recovery
		await self.redis.delete(lc_engine_lock_key(event_id))
		# Broadcast event_ended with Last Stand fields (via pub/sub for cross-worker)
		await self._engine_broadcast_json(
			event_id,
			{
				"type": "event_ended",
				"reason": reason,
				"final_alive_count": alive_count,
				"total_rounds_played": rounds_played,
			},
		)
		# Trigger reconciliation
		await self._reconcile_event(event_id)
		# Defer engine cleanup so the safety ceiling can still find and await
		# the engine task (which is the caller of this callback) — S-5.
		asyncio.get_event_loop().call_soon(self._cleanup_engine, event_id)

	def _cleanup_engine(self, event_id: str) -> None:
		"""Remove engine and task references (deferred from _on_last_stand_ended)."""
		self._engines.pop(event_id, None)
		self._engine_tasks.pop(event_id, None)

	async def submit_last_stand_answer(
		self,
		event_id: str,
		player_id: str,
		round_id: str,
		selected: str,
	) -> int:
		"""Submit an answer for a Last Stand round.  Returns Lua result code.

		Uses the atomic Lua script directly against Redis so it works
		regardless of which uvicorn worker handles the HTTP request
		(the engine runs in only one worker).
		"""
		from fastapi_app.core.redis_keys import lc_round_answers_key, lc_round_signal_key
		from fastapi_app.services.last_stand_engine import _ATOMIC_ANSWER_LUA

		if self._answer_script is None:
			self._answer_script = self.redis.register_script(_ATOMIC_ANSWER_LUA)

		result = await self._answer_script(
			keys=[
				lc_status_key(event_id),
				lc_alive_key(event_id),
				lc_round_key(event_id),
				lc_round_answers_key(event_id, round_id),
			],
			args=[player_id, round_id, selected, str(time.time())],
		)
		result = int(result)

		# On success, check for early close signal (R-004)
		if result == 1:
			pipe = self.redis.pipeline()
			pipe.hlen(lc_round_answers_key(event_id, round_id))
			pipe.scard(lc_alive_key(event_id))
			answer_count, alive_count = await pipe.execute()
			if alive_count > 0 and answer_count >= alive_count:
				await self.redis.publish(lc_round_signal_key(event_id), "all_answered")

		return result

	# -------------------------------------------------------------------------
	# Join (T021)
	# -------------------------------------------------------------------------

	async def join(
		self,
		event_id: str,
		player_id: str,
		player_plan: str | None = None,
	) -> dict[str, Any]:
		"""Join a live challenge event.

		For free events: Redis-only. For paid events: checks premium bypass
		or event ticket access before allowing join (FR-015 source-of-truth gate).

		Returns dict with position, countdown_remaining, waiting_room_duration.
		Raises ValueError with error code on failure.
		"""
		# 1. Fast-path status + meta + mode in one round trip
		pipe = self.redis.pipeline()
		pipe.get(lc_status_key(event_id))
		pipe.hgetall(lc_meta_key(event_id))
		pipe.get(lc_mode_key(event_id))
		status, meta, mode = await pipe.execute()
		mode = mode or "exam"
		if status not in ("waiting", "active"):
			raise ValueError("EVENT_NOT_JOINABLE")

		# Last Stand: reject late join during Active phase (FR-007)
		if mode == "last_stand" and status == "active":
			raise ValueError("NO_LATE_JOIN")

		# 2. Check plan eligibility (before atomic join to avoid wasting a capacity slot)
		eligible_plans_json = meta.get("eligible_plans", "[]")
		eligible_plans = json.loads(eligible_plans_json)
		if eligible_plans and player_plan not in eligible_plans:
			raise ValueError("PLAN_NOT_ELIGIBLE")

		# 2.5 Paid-event access gate (R-010, FR-015)
		is_paid = bool(int(meta.get("is_paid", "0")))
		if is_paid:
			await self._check_paid_event_access(player_id, event_id)

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
		# Last Stand: initialize hearts and alive set on join
		if mode == "last_stand":
			starting_hearts = int(meta.get("starting_hearts", "3"))
			join_pipe.hset(lc_hearts_key(event_id), player_id, starting_hearts)
			join_pipe.expire(lc_hearts_key(event_id), LC_KEY_TTL)
			join_pipe.sadd(lc_alive_key(event_id), player_id)
			join_pipe.expire(lc_alive_key(event_id), LC_KEY_TTL)
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

		result = {
			"position": position,
			"countdown_remaining": countdown_remaining,
			"waiting_room_duration": waiting_room_duration,
			"mode": mode,
		}
		if mode == "last_stand":
			result["starting_hearts"] = int(meta.get("starting_hearts", "3"))
		return result

	async def _check_paid_event_access(
		self,
		player_id: str,
		event_id: str,
	) -> None:
		"""Gate 1.5 for paid events (R-010): event ticket required.

		Raises ValueError("NO_EVENT_ACCESS") if player has no active ticket.
		"""
		from fastapi_app.services.event_access import EventAccessService

		event_svc = EventAccessService(self.redis, self.frappe)
		access_state = await event_svc.has_active_access(player_id, event_id)
		if access_state.has_access:
			return

		raise ValueError("NO_EVENT_ACCESS")

	# -------------------------------------------------------------------------
	# Reconciliation (post-event Frappe persistence)
	# -------------------------------------------------------------------------

	async def _reconcile_event(self, event_id: str) -> None:
		"""Persist join + submission data to Frappe after event ends (idempotent, lock-guarded).

		Handles both exam and last_stand modes:
		- Exam: reads lc_results_key (score/answers from /submit)
		- Last Stand: reads hearts, eliminated_at, correct_counts, answered_counts,
		  response_times from Redis and computes score/stats.

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
			# Step 2 — Read snapshot (joined players, join times, meta)
			player_ids = await self.redis.smembers(lc_joined_key(event_id))
			count = int(await self.redis.get(lc_count_key(event_id)) or "0")
			meta = await self.redis.hgetall(lc_meta_key(event_id))
			join_times = await self.redis.hgetall(lc_join_times_key(event_id))
			default_joined_at = meta.get("exam_start_ts", _now_naive().strftime("%Y-%m-%d %H:%M:%S"))
			mode = meta.get("mode", "exam")

			if mode == "last_stand":
				docs = await self._build_last_stand_docs(
					event_id,
					player_ids,
					join_times,
					default_joined_at,
				)
				submitted_count = len(player_ids)
			else:
				# Exam mode — read submission results
				results_raw = await self.redis.hgetall(lc_results_key(event_id))
				results: dict[str, dict] = {}
				for pid, payload in results_raw.items():
					try:
						results[pid] = json.loads(payload)
					except (json.JSONDecodeError, TypeError):
						logger.warning("lc_reconcile_bad_result_payload", event_id=event_id, player=pid)

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
				submitted_count = len(results)

			# Step 3 — Insert docs via Frappe
			failed_players: list[str] = []
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
								# Doc already exists — update fields
								update_fields = self._reconcile_update_fields(doc, mode)
								if update_fields:
									try:
										existing = await self.frappe.call(
											"frappe.client.get_list",
											{
												"doctype": "Memora Live Challenge Participation",
												"filters": json.dumps(
													[
														["event", "=", event_id],
														["player", "=", doc["player"]],
													]
												),
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
													"fieldname": json.dumps(update_fields),
												},
											)
									except Exception:
										logger.warning(
											"lc_reconcile_update_failed",
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

			# Sync status + counters to Frappe event (bypass DocType validate)
			count_synced = True
			try:
				await self.frappe.call(
					"memora_admin.memora_admin.api.live_challenge.reconcile_event_status",
					{
						"event_id": event_id,
						"status": "Ended",
						"participant_count": count,
						"submitted_count": submitted_count,
					},
				)
			except Exception:
				count_synced = False
				logger.warning("lc_event_sync_failed", event_id=event_id)

			# Step 4 — Conditional cleanup (full success only)
			all_succeeded = not failed_players and count_synced
			if all_succeeded:
				await self.redis.set(lc_reconciled_key(event_id), "1", ex=LC_KEY_TTL)
				await self._cleanup_redis_keys(event_id)
				logger.info(
					"lc_reconciliation_complete",
					event_id=event_id,
					mode=mode,
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

	async def _build_last_stand_docs(
		self,
		event_id: str,
		player_ids: set,
		join_times: dict,
		default_joined_at: str,
	) -> list[dict[str, Any]]:
		"""Build Participation docs for a Last Stand event from Redis state."""
		hearts_raw = await self.redis.hgetall(lc_hearts_key(event_id))
		eliminated_at_raw = await self.redis.hgetall(lc_eliminated_at_key(event_id))
		correct_counts_raw = await self.redis.hgetall(lc_correct_counts_key(event_id))
		answered_counts_raw = await self.redis.hgetall(lc_answered_counts_key(event_id))
		response_times_raw = await self.redis.hgetall(lc_response_times_key(event_id))
		questions_raw = await self.redis.get(lc_questions_key(event_id))

		# Parse total questions
		total_questions = 0
		if questions_raw:
			try:
				total_questions = len(json.loads(questions_raw))
			except (json.JSONDecodeError, TypeError):
				pass
		if total_questions == 0:
			round_data = await self.redis.hgetall(lc_round_key(event_id))
			total_questions = int(round_data.get("total_rounds_played", "0") or "0") or 1

		now_str = _now_naive().strftime("%Y-%m-%d %H:%M:%S")
		docs = []
		for pid in player_ids:
			correct = int(correct_counts_raw.get(pid, "0") or "0")
			player_hearts = max(0, int(hearts_raw.get(pid, "0") or "0"))
			elim_at = eliminated_at_raw.get(pid)
			is_eliminated = 1 if elim_at is not None else 0
			elim_at_q = int(elim_at) if elim_at is not None else 0
			score = round((correct / total_questions * 100), 1) if total_questions > 0 else 0

			# Avg response time from answered questions
			rt_raw = response_times_raw.get(pid)
			if rt_raw:
				try:
					rts = json.loads(rt_raw)
					avg_rt_ms = int(sum(rts) / len(rts)) if rts else 0
				except (json.JSONDecodeError, TypeError):
					avg_rt_ms = 0
			else:
				avg_rt_ms = 0

			docs.append(
				{
					"doctype": "Memora Live Challenge Participation",
					"event": event_id,
					"player": pid,
					"joined_at": join_times.get(pid, default_joined_at),
					"score": score,
					"submitted_at": now_str,
					"final_hearts": player_hearts,
					"is_eliminated": is_eliminated,
					"eliminated_at_question": elim_at_q,
					"avg_response_time_ms": avg_rt_ms,
				}
			)
		return docs

	@staticmethod
	def _reconcile_update_fields(doc: dict, mode: str) -> dict | None:
		"""Extract the fields to update on duplicate for a given mode."""
		if mode == "last_stand":
			return {
				"score": doc.get("score"),
				"submitted_at": doc.get("submitted_at"),
				"final_hearts": doc.get("final_hearts"),
				"is_eliminated": doc.get("is_eliminated"),
				"eliminated_at_question": doc.get("eliminated_at_question"),
				"avg_response_time_ms": doc.get("avg_response_time_ms"),
			}
		# Exam mode
		if doc.get("score") is not None:
			return {
				"score": doc.get("score"),
				"submitted_at": doc.get("submitted_at"),
				"answers_json": doc.get("answers_json"),
			}
		return None

	async def _cleanup_redis_keys(self, event_id: str) -> None:
		"""Delete all ephemeral Redis keys for an event after reconciliation.

		Cleans up both exam and Last Stand keys (no-op DELETEs for missing keys).
		"""
		pipe = self.redis.pipeline()
		pipe.expire(lc_status_key(event_id), LC_KEY_TTL)
		pipe.delete(lc_count_key(event_id))
		pipe.delete(lc_meta_key(event_id))
		pipe.delete(lc_questions_key(event_id))
		pipe.delete(lc_joined_key(event_id))
		pipe.delete(lc_submitted_key(event_id))
		pipe.delete(lc_join_times_key(event_id))
		pipe.delete(lc_results_key(event_id))
		pipe.delete(lc_reconcile_lock_key(event_id))
		# Last Stand keys (no-op if exam mode)
		pipe.delete(lc_engine_lock_key(event_id))
		pipe.delete(lc_mode_key(event_id))
		pipe.delete(lc_round_key(event_id))
		pipe.delete(lc_hearts_key(event_id))
		pipe.delete(lc_alive_key(event_id))
		pipe.delete(lc_eliminated_key(event_id))
		pipe.delete(lc_eliminated_at_key(event_id))
		pipe.delete(lc_response_times_key(event_id))
		pipe.delete(lc_correct_counts_key(event_id))
		pipe.delete(lc_answered_counts_key(event_id))
		await pipe.execute()

		# Clean up per-round answer keys (pattern-matched, outside pipeline)
		round_answers_pattern = f"memora:lc:{event_id}:round_answers:*"
		cursor = 0
		while True:
			cursor, keys = await self.redis.scan(cursor, match=round_answers_pattern, count=100)
			if keys:
				await self.redis.delete(*keys)
			if cursor == 0:
				break

	# -------------------------------------------------------------------------
	# Grade (T022)
	# -------------------------------------------------------------------------

	async def grade(
		self,
		event_id: str,
		player_id: str,
		answers: list[dict],
		player_plan: str | None = None,
	) -> dict[str, Any]:
		"""Grade submitted answers (pure Redis — DB persistence deferred to reconciliation).

		Returns dict with score, correct_count, total_questions, submitted_at, corrections.
		Raises ValueError with error code on failure.
		"""
		# 0. Reject Last Stand events — they use POST /answer, not POST /submit
		mode = await self.redis.get(lc_mode_key(event_id))
		if mode == "last_stand":
			raise ValueError("MODE_NOT_SUPPORTED")

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

		# 4.5 Check plan eligibility (zero extra Redis RT — meta already loaded)
		eligible_plans_json = meta.get("eligible_plans", "[]")
		eligible_plans = json.loads(eligible_plans_json)
		if eligible_plans and player_plan not in eligible_plans:
			await self.redis.srem(lc_submitted_key(event_id), player_id)
			raise ValueError("PLAN_NOT_ELIGIBLE")

		# 5. Grade
		result = grade_answers(questions, answers)
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
				f"memora:lc:{event_id}:hydrate_guard",
				"1",
				nx=True,
				ex=30,
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

	async def get_event_detail(
		self, event_id: str, player_id: str, player_plan: str | None = None
	) -> dict[str, Any] | None:
		"""Get public event details with player-specific flags.

		Explicit source selection: ended events read from DB,
		active/waiting/draft events read from Redis. No implicit fallback.
		"""
		source = await self._resolve_event_source(event_id)
		if source is None:
			return None
		if source == "db":
			return await self._get_event_detail_from_db(event_id, player_id, player_plan)
		return await self._get_event_detail_from_redis(event_id, player_id, player_plan)

	async def _get_event_detail_from_redis(
		self, event_id: str, player_id: str, player_plan: str | None = None
	) -> dict[str, Any] | None:
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

		detail: dict[str, Any] = {
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
			"capacity": int(meta.get("capacity", "0")),
			"current_count": int(count_raw or "0"),
			"is_paid": bool(int(meta.get("is_paid", "0"))),
			"rewards": json.loads(meta.get("rewards_json", "[]")) if meta.get("rewards_json") else [],
			"question_count": question_count,
			"eligible_plans": eligible_plans,
			"is_plan_eligible": (not eligible_plans) or (player_plan in eligible_plans),
			"has_joined": bool(has_joined),
			"has_submitted": bool(has_submitted),
		}

		# Enrich with Last Stand fields so client doesn't need a separate /status call
		mode = meta.get("mode", "exam")
		detail["mode"] = mode
		if mode == "last_stand" and status.lower() == "active":
			ls_pipe = self.redis.pipeline()
			ls_pipe.scard(lc_alive_key(event_id))
			ls_pipe.scard(lc_eliminated_key(event_id))
			ls_pipe.hgetall(lc_round_key(event_id))
			alive, eliminated, round_data = await ls_pipe.execute()
			detail["alive_count"] = alive
			detail["eliminated_count"] = eliminated
			detail["current_round"] = int(round_data.get("question_idx", "0")) if round_data else 0
			detail["total_rounds"] = question_count

		return detail

	async def _get_event_detail_from_db(
		self, event_id: str, player_id: str, player_plan: str | None = None
	) -> dict[str, Any] | None:
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
					"filters": json.dumps(
						[
							["event", "=", event_id],
							["player", "=", player_id],
						]
					),
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
			"capacity": int(event.get("capacity", 0)),
			"current_count": int(event.get("participant_count", 0)),
			"is_paid": bool(event.get("is_paid")),
			"rewards": [
				{
					"rank": int(r.get("rank", 0)),
					"reward_type": r.get("reward_type", "XP"),
					"xp_amount": int(r.get("xp_amount") or 0),
					"prize_description": r.get("prize_description") or "",
				}
				for r in (event.get("rewards") or [])
			],
			"question_count": len(event.get("questions", [])),
			"eligible_plans": [ep.get("plan") for ep in event.get("eligible_plans", [])],
			"is_plan_eligible": (not event.get("eligible_plans"))
			or (player_plan in [ep.get("plan") for ep in event.get("eligible_plans", [])]),
			"has_joined": joined,
			"has_submitted": submitted,
			"top_players": top_players,
			"mode": event.get("mode", "exam"),
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
		Includes Last Stand fields (final_hearts, is_eliminated, etc.) when applicable.
		"""
		try:
			parts = await self.frappe.call(
				"frappe.client.get_list",
				{
					"doctype": "Memora Live Challenge Participation",
					"filters": json.dumps([["event", "=", event_id], ["player", "=", player_id]]),
					"fields": json.dumps(
						[
							"name",
							"score",
							"rank",
							"xp_awarded",
							"submitted_at",
							"answers_json",
							"final_hearts",
							"is_eliminated",
							"eliminated_at_question",
							"avg_response_time_ms",
						]
					),
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
		mode = event.get("mode", "exam")

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

		# Parse answers_json for correct_count / total_questions (exam mode)
		correct_count = 0
		total_questions = 0
		if mode == "last_stand":
			# For Last Stand, derive from score + event question count
			total_questions = len(event.get("questions", []))
			score_val = float(part.get("score") or 0)
			if total_questions > 0:
				correct_count = round(score_val * total_questions / 100)
		else:
			answers_json_raw = part.get("answers_json")
			if answers_json_raw:
				try:
					answers_data = (
						json.loads(answers_json_raw)
						if isinstance(answers_json_raw, str)
						else answers_json_raw
					)
					answers_list = answers_data.get("answers", [])
					total_questions = len(answers_list)
					correct_count = sum(1 for a in answers_list if a.get("correct", False))
				except (json.JSONDecodeError, KeyError, TypeError):
					pass

		rank = part.get("rank")
		xp_awarded = part.get("xp_awarded")

		return {
			"event_id": event_id,
			"event_name": event_name,
			"score": _fmt_score(float(part.get("score") or 0)),
			"correct_count": correct_count,
			"total_questions": total_questions,
			"rank": int(rank) if rank else None,
			"total_participants": int(total_participants or 0),
			"xp_awarded": int(xp_awarded) if xp_awarded else None,
			"submitted_at": str(part.get("submitted_at", "")) if part.get("submitted_at") else None,
			"final_hearts": int(part.get("final_hearts") or 0),
			"is_eliminated": bool(part.get("is_eliminated")),
			"eliminated_at_question": int(part.get("eliminated_at_question") or 0),
			"avg_response_time_ms": int(part.get("avg_response_time_ms") or 0),
		}

	async def get_leaderboard(self, event_id: str, player_id: str) -> dict[str, Any] | None:
		"""Get leaderboard for an ended event, or exam_end_ts for an active one.

		Returns None if event is not found or not in Active/Ended status.
		"""
		# Check Redis status first (source of truth for live transitions)
		redis_status = await self.redis.get(lc_status_key(event_id))

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

		if not event:
			return None

		# Prefer Redis status (real-time), fall back to Frappe doc status
		effective_status = redis_status or (event.get("status") or "").lower()

		if effective_status not in ("ended", "active"):
			return None

		# Active event — return exam_end_ts so client knows when to check back
		if effective_status == "active":
			return {
				"event_id": event_id,
				"event_name": event.get("event_name", ""),
				"status": "Active",
				"leaderboard": [],
				"my_rank": None,
				"my_score": None,
				"total_participants": int(event.get("participant_count", 0)),
				"exam_end_ts": str(event.get("exam_end_ts", "")) or None,
			}

		# Parse leaderboard_json
		leaderboard = []
		lb_json = event.get("leaderboard_json")
		if lb_json:
			try:
				leaderboard = json.loads(lb_json) if isinstance(lb_json, str) else lb_json
			except (json.JSONDecodeError, TypeError):
				pass

		total_participants = int(event.get("participant_count", 0))

		# Get player's own rank/score
		my_rank = None
		my_score = None
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
				my_score = _fmt_score(float(parts[0]["score"])) if parts[0].get("score") is not None else None
		except Exception:
			pass

		return {
			"event_id": event_id,
			"event_name": event.get("event_name", ""),
			"status": event.get("status", ""),
			"leaderboard": leaderboard,
			"my_rank": my_rank,
			"my_score": my_score,
			"total_participants": total_participants,
			"exam_end_ts": str(event.get("exam_end_ts", "")) or None,
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
				logger.info("reaction_tap_dropped_status", event_id=event_id, status=status)
				return
			reaction = msg.get("reaction", "")
			accepted = await self._reaction_engine.accept_tap(event_id, player_id, reaction)
			logger.debug(
				"reaction_tap_result",
				event_id=event_id,
				player_id=player_id,
				accepted=accepted,
				reaction=reaction,
			)
		except Exception:
			logger.warning(
				"reaction_tap_handler_error", event_id=event_id, player_id=player_id, exc_info=True
			)

	# -------------------------------------------------------------------------
	# WebSocket Connection Tracking (T029)
	# -------------------------------------------------------------------------

	def register_connection(
		self,
		event_id: str,
		ws: WebSocket,
		player_id: str | None = None,
	) -> None:
		"""Register a WebSocket connection for an event.

		When *player_id* is provided the mapping is stored so that
		``_broadcast_personalized`` can send per-player messages.
		"""
		is_first = event_id not in self._ws_connections
		if is_first:
			self._ws_connections[event_id] = set()
		self._ws_connections[event_id].add(ws)
		if player_id is not None:
			if event_id not in self._ws_player_map:
				self._ws_player_map[event_id] = {}
			self._ws_player_map[event_id][ws] = player_id
		if is_first:
			asyncio.create_task(self._reaction_engine.subscribe_event(event_id))
			asyncio.create_task(self._subscribe_round_event(event_id))

	def remove_connection(self, event_id: str, ws: WebSocket) -> None:
		"""Remove a WebSocket connection for an event."""
		conns = self._ws_connections.get(event_id)
		if conns:
			conns.discard(ws)
			if not conns:
				del self._ws_connections[event_id]
				asyncio.create_task(self._reaction_engine.unsubscribe_event(event_id))
				asyncio.create_task(self._unsubscribe_round_event(event_id))
		# Clean up player mapping
		pm = self._ws_player_map.get(event_id)
		if pm:
			pm.pop(ws, None)
			if not pm:
				del self._ws_player_map[event_id]

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

	async def _broadcast_personalized(
		self,
		event_id: str,
		base_msg: dict,
		player_states: dict[str, dict],
	) -> int:
		"""Send a per-player JSON message to every connected WebSocket.

		For each connection whose player_id appears in *player_states* the
		message is ``base_msg | player_states[player_id]``.  Spectators (not
		in *player_states*) receive *base_msg* only.
		"""
		conns = list(self._ws_connections.get(event_id, set()))
		if not conns:
			return 0

		ws_map = self._ws_player_map.get(event_id, {})

		# Pre-serialise: one payload per connection
		base_json = json.dumps(base_msg)
		messages: dict[WebSocket, str] = {}
		for ws in conns:
			pid = ws_map.get(ws)
			if pid and pid in player_states:
				messages[ws] = json.dumps({**base_msg, **player_states[pid]})
			else:
				messages[ws] = base_json

		sem = asyncio.Semaphore(_BROADCAST_CONCURRENCY)
		dead: list[WebSocket] = []
		sent = 0

		async def _send(ws: WebSocket) -> bool:
			async with sem:
				try:
					await asyncio.wait_for(ws.send_text(messages[ws]), timeout=2.0)
					return True
				except Exception:
					dead.append(ws)
					return False

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

			exam_start_ts = datetime.fromisoformat(exam_start_ts_str)
			exam_end_ts = datetime.fromisoformat(exam_end_ts_str)

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

					mode = await self.redis.get(lc_mode_key(event_id))
					if mode == "last_stand":
						await self._start_last_stand_engine(event_id)
					else:
						await self._broadcast_exam_start(event_id, meta)

					exam_started = True
					logger.info("lc_exam_started_by_ws", event_id=event_id, mode=mode or "exam")

				# Check if exam has ended (safety ceiling for Last Stand)
				if exam_started and now >= exam_end_ts:
					# Stop Last Stand engine if still running
					engine = self._engines.get(event_id)
					if engine:
						engine.stop()
						# Wait for engine task to finish current phase before reconciling
						engine_task = self._engine_tasks.get(event_id)
						if engine_task and not engine_task.done():
							try:
								await asyncio.wait_for(engine_task, timeout=5.0)
							except (asyncio.TimeoutError, asyncio.CancelledError):
								logger.warning(
									"last_stand_engine_stop_timeout",
									event_id=event_id,
								)
						# Release engine lock (may already be released by _on_last_stand_ended)
						await self.redis.delete(lc_engine_lock_key(event_id))

					mode = await self.redis.get(lc_mode_key(event_id))
					if mode == "last_stand":
						# Store safety-ceiling end info if engine didn't already
						round_state = await self.redis.hgetall(lc_round_key(event_id))
						if round_state.get("phase") != "ended":
							alive = await self.redis.scard(lc_alive_key(event_id))
							rounds_played = int(round_state.get("question_idx", "0"))
							await self.redis.hset(
								lc_round_key(event_id),
								mapping={
									"phase": "ended",
									"end_reason": "time_ceiling",
									"final_alive_count": str(alive),
									"total_rounds_played": str(rounds_played),
								},
							)

					await self.redis.set(lc_status_key(event_id), "ended", ex=LC_KEY_TTL)
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
		"""Broadcast event_ended to all connected clients.

		For Last Stand events the message includes reason, final_alive_count,
		and total_rounds_played read from the round state HASH.
		"""
		msg: dict[str, Any] = {"type": "event_ended"}
		mode = await self.redis.get(lc_mode_key(event_id))
		if mode == "last_stand":
			round_state = await self.redis.hgetall(lc_round_key(event_id))
			msg["reason"] = round_state.get("end_reason", "time_ceiling")
			msg["final_alive_count"] = int(round_state.get("final_alive_count", "0"))
			msg["total_rounds_played"] = int(round_state.get("total_rounds_played", "0"))
		sent = await self._broadcast_json(event_id, msg)
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

	async def send_player_state_to_client(
		self,
		event_id: str,
		ws: WebSocket,
		player_id: str,
	) -> None:
		"""Send player_state to a single client on WS reconnect during Active Last Stand.

		Reads alive/eliminated SET membership, hearts HASH, round state HASH,
		and (if alive + answer phase) the current question from Redis.
		Allows the client to rebuild its UI state immediately on reconnect.
		"""
		pipe = self.redis.pipeline()
		pipe.sismember(lc_alive_key(event_id), player_id)
		pipe.sismember(lc_eliminated_key(event_id), player_id)
		pipe.hget(lc_hearts_key(event_id), player_id)
		pipe.hgetall(lc_round_key(event_id))
		pipe.hget(lc_eliminated_at_key(event_id), player_id)
		pipe.scard(lc_alive_key(event_id))
		is_alive, is_eliminated, hearts_raw, round_state, elim_at_raw, alive_count = await pipe.execute()

		# Determine player liveness
		player_alive = bool(is_alive)
		hearts_remaining = int(hearts_raw) if hearts_raw else 0

		# Round state
		current_round_id = round_state.get("round_id")
		question_idx = int(round_state.get("question_idx", "0"))
		phase = round_state.get("phase", "answer")
		phase_end_ts = float(round_state.get("phase_end_ts", "0"))
		phase_remaining_ms = max(0, int((phase_end_ts - time.time()) * 1000))

		# Build base message
		msg: dict[str, Any] = {
			"type": "player_state",
			"hearts_remaining": hearts_remaining,
			"is_alive": player_alive,
			"current_round_id": current_round_id,
			"question_idx": question_idx,
			"phase": phase,
			"phase_remaining_ms": phase_remaining_ms,
			"question": None,
			"alive_count": alive_count,
			"eliminated_at_question": None,
		}

		# Eliminated players get their elimination question index
		if is_eliminated and elim_at_raw is not None:
			msg["eliminated_at_question"] = int(elim_at_raw)

		# Alive player in answer phase: include current question (sans correct_answer)
		if player_alive and phase == "answer" and phase_remaining_ms > 0:
			questions_json = await self.redis.get(lc_questions_key(event_id))
			if questions_json:
				questions = json.loads(questions_json)
				if 0 <= question_idx < len(questions):
					q = questions[question_idx]
					msg["question"] = {
						"idx": q["idx"],
						"question_text": q["question_text"],
						"option_a": q["option_a"],
						"option_b": q["option_b"],
						"option_c": q["option_c"],
						"option_d": q["option_d"],
					}

		try:
			await ws.send_text(json.dumps(msg))
			logger.debug(
				"lc_player_state_sent",
				event_id=event_id,
				player_id=player_id,
				is_alive=player_alive,
				phase=phase,
			)
		except Exception:
			logger.debug("lc_send_player_state_failed", event_id=event_id, player_id=player_id)
