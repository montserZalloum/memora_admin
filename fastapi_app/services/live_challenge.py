"""Live Challenge service — join, grade, submission queue, WebSocket management."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis
import structlog
from fastapi import WebSocket

from fastapi_app.core.redis_keys import (
	LC_KEY_TTL,
	lc_count_key,
	lc_joined_key,
	lc_meta_key,
	lc_questions_key,
	lc_status_key,
	lc_submitted_key,
)

if TYPE_CHECKING:
	from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()

# Lua script for atomic join: uniqueness + capacity + SADD in one call.
# KEYS: [1] joined_set, [2] submitted_set, [3] count_key
# ARGV: [1] player_id, [2] capacity
# Returns: position (>0) on success, -1 capacity full, -2 already joined, -3 already submitted
_ATOMIC_JOIN_LUA = """
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
return pos
"""

# Queue consumer settings
_FLUSH_BATCH_SIZE = 50
_FLUSH_INTERVAL_SECONDS = 30


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
		self._submission_queue: asyncio.Queue[dict] = asyncio.Queue()
		self._queue_task: asyncio.Task | None = None
		self._shutting_down = False
		self._join_script: Any = None
		# WebSocket connection tracking: event_id -> set[WebSocket]
		self._ws_connections: dict[str, set[WebSocket]] = {}
		# Per-event countdown loop tasks
		self._countdown_tasks: dict[str, asyncio.Task] = {}

	async def _get_join_script(self):
		"""Get or register the Lua atomic join script."""
		if self._join_script is None:
			self._join_script = self.redis.register_script(_ATOMIC_JOIN_LUA)
		return self._join_script

	# -------------------------------------------------------------------------
	# Queue Consumer (T023)
	# -------------------------------------------------------------------------

	async def start_queue_consumer(self):
		"""Start background task that flushes submissions to MariaDB."""
		self._queue_task = asyncio.create_task(self._queue_consumer_loop())
		logger.info("lc_queue_consumer_started")

	async def drain_queue(self):
		"""Flush all remaining submissions. Called on shutdown or event end."""
		batch: list[dict] = []
		while not self._submission_queue.empty():
			try:
				batch.append(self._submission_queue.get_nowait())
			except asyncio.QueueEmpty:
				break
		if batch:
			await self._flush_batch(batch)
			logger.info("lc_queue_drained", count=len(batch))

	async def stop_queue_consumer(self):
		"""Stop the queue consumer and drain remaining items."""
		self._shutting_down = True
		if self._queue_task:
			self._queue_task.cancel()
			try:
				await self._queue_task
			except asyncio.CancelledError:
				pass
		# Final drain after task cancellation
		await self.drain_queue()

	async def _queue_consumer_loop(self):
		"""Background loop: flush batch every 50 items or 30 seconds."""
		batch: list[dict] = []
		last_flush = time.monotonic()

		try:
			while not self._shutting_down:
				try:
					item = await asyncio.wait_for(self._submission_queue.get(), timeout=1.0)
					batch.append(item)
				except asyncio.TimeoutError:
					pass

				should_flush = len(batch) >= _FLUSH_BATCH_SIZE or (
					batch and (time.monotonic() - last_flush) >= _FLUSH_INTERVAL_SECONDS
				)

				if should_flush and batch:
					await self._flush_batch(batch)
					batch = []
					last_flush = time.monotonic()
		except asyncio.CancelledError:
			# Drain on cancellation
			while not self._submission_queue.empty():
				try:
					batch.append(self._submission_queue.get_nowait())
				except asyncio.QueueEmpty:
					break
			if batch:
				await self._flush_batch(batch)
			raise

	async def _flush_batch(self, batch: list[dict]):
		"""Persist a batch of submissions to MariaDB via FrappeClient."""
		event_counts: dict[str, int] = {}

		for item in batch:
			event_id = item["event_id"]
			event_counts[event_id] = event_counts.get(event_id, 0) + 1
			try:
				await self.frappe.call(
					"frappe.client.set_value",
					{
						"doctype": "Memora Live Challenge Participation",
						"name": item["participation_name"],
						"fieldname": json.dumps(
							{
								"score": item["score"],
								"submitted_at": item["submitted_at"],
								"answers_json": item["answers_json"],
							}
						),
					},
				)
			except Exception:
				logger.error(
					"lc_submission_persist_failed",
					event_id=event_id,
					player_id=item["player_id"],
				)

		# Sync submitted_count for each event from Redis (idempotent)
		for event_id, _count in event_counts.items():
			try:
				submitted_count = await self.redis.scard(lc_submitted_key(event_id))
				await self.frappe.call(
					"frappe.client.set_value",
					{
						"doctype": "Memora Live Challenge Event",
						"name": event_id,
						"fieldname": json.dumps({"submitted_count": submitted_count}),
					},
				)
			except Exception:
				logger.error("lc_submitted_count_sync_failed", event_id=event_id)

		logger.info("lc_batch_flushed", count=len(batch), events=list(event_counts.keys()))

	async def queue_submission(self, submission_data: dict):
		"""Add submission to in-memory queue for batch persistence."""
		await self._submission_queue.put(submission_data)

	# -------------------------------------------------------------------------
	# Join (T021)
	# -------------------------------------------------------------------------

	async def join(
		self,
		event_id: str,
		player_id: str,
	) -> dict[str, Any]:
		"""Join a live challenge event.

		Returns dict with position, countdown_remaining, waiting_room_duration.
		Raises ValueError with error code on failure.
		"""
		# 1. Check event status from Redis
		status = await self.redis.get(lc_status_key(event_id))
		if status not in ("waiting", "active"):
			raise ValueError("EVENT_NOT_JOINABLE")

		# 2. Check plan eligibility (before atomic join to avoid wasting a capacity slot)
		meta = await self.redis.hgetall(lc_meta_key(event_id))
		eligible_plans_json = meta.get("eligible_plans", "[]")
		eligible_plans = json.loads(eligible_plans_json)
		if eligible_plans:
			# Look up player's current plan from FrappeClient (authoritative, not JWT which may be stale)
			player_plan = None
			try:
				profile = await self.frappe.call(
					"frappe.client.get_value",
					{
						"doctype": "Memora Player Profile",
						"filters": json.dumps({"name": player_id}),
						"fieldname": "plan",
					},
				)
				if profile:
					player_plan = profile.get("plan")
			except Exception:
				logger.warning("lc_plan_lookup_failed", player_id=player_id, event_id=event_id)
			if player_plan not in eligible_plans:
				raise ValueError("PLAN_NOT_ELIGIBLE")

		# 3. Atomic join: uniqueness + capacity + SADD in one Lua call
		capacity = int(meta.get("capacity", "0"))
		script = await self._get_join_script()
		position = await script(
			keys=[lc_joined_key(event_id), lc_submitted_key(event_id), lc_count_key(event_id)],
			args=[player_id, capacity],
		)
		if position == -2 or position == -3:
			raise ValueError("ALREADY_JOINED")
		if position == -1:
			raise ValueError("CAPACITY_FULL")

		# Set TTL on joined set
		await self.redis.expire(lc_joined_key(event_id), LC_KEY_TTL)

		# 6. Create Participation record via FrappeClient
		joined_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
		try:
			await self.frappe.call(
				"frappe.client.insert",
				{
					"doc": json.dumps(
						{
							"doctype": "Memora Live Challenge Participation",
							"event": event_id,
							"player": player_id,
							"joined_at": joined_at,
						}
					),
				},
			)
		except Exception:
			# Rollback: decrement count and remove from joined set
			await self.redis.decr(lc_count_key(event_id))
			await self.redis.srem(lc_joined_key(event_id), player_id)
			logger.error("lc_participation_create_failed", event_id=event_id, player_id=player_id)
			raise

		# Sync participant_count to MariaDB
		try:
			await self.frappe.call(
				"frappe.client.set_value",
				{
					"doctype": "Memora Live Challenge Event",
					"name": event_id,
					"fieldname": json.dumps({"participant_count": position}),
				},
			)
		except Exception:
			logger.warning("lc_participant_count_sync_failed", event_id=event_id)

		# 7. Calculate countdown_remaining
		countdown_remaining = 0
		if status == "waiting":
			exam_start_ts_str = meta.get("exam_start_ts", "")
			if exam_start_ts_str:
				try:
					exam_start = datetime.strptime(exam_start_ts_str, "%Y-%m-%d %H:%M:%S")
					remaining = (exam_start - datetime.now()).total_seconds()
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
	# Grade (T022)
	# -------------------------------------------------------------------------

	async def grade(
		self,
		event_id: str,
		player_id: str,
		answers: list[dict],
	) -> dict[str, Any]:
		"""Grade submitted answers and queue for persistence.

		Returns dict with score, correct_count, total_questions, submitted_at, corrections.
		Raises ValueError with error code on failure.
		"""
		# 1. Check event is active
		status = await self.redis.get(lc_status_key(event_id))
		if status != "active":
			raise ValueError("EVENT_NOT_ACTIVE")

		# 2. Atomic mark-as-submitted: SADD returns 1 if newly added, 0 if already present
		added = await self.redis.sadd(lc_submitted_key(event_id), player_id)
		if not added:
			raise ValueError("ALREADY_SUBMITTED")
		await self.redis.expire(lc_submitted_key(event_id), LC_KEY_TTL)

		# 3. Check is participant (Redis joined set)
		is_participant = await self.redis.sismember(lc_joined_key(event_id), player_id)
		if not is_participant:
			# Rollback: remove from submitted set
			await self.redis.srem(lc_submitted_key(event_id), player_id)
			# Fall back to FrappeClient check (joined set may have expired)
			try:
				result = await self.frappe.call(
					"frappe.client.get_count",
					{
						"doctype": "Memora Live Challenge Participation",
						"filters": json.dumps({"event": event_id, "player": player_id}),
					},
				)
				if not result:
					raise ValueError("NOT_A_PARTICIPANT")
				# Re-add to submitted set
				await self.redis.sadd(lc_submitted_key(event_id), player_id)
				await self.redis.expire(lc_submitted_key(event_id), LC_KEY_TTL)
			except ValueError:
				raise

		# 4. Load questions from Redis
		questions_json = await self.redis.get(lc_questions_key(event_id))
		if not questions_json:
			# Rollback submitted mark
			await self.redis.srem(lc_submitted_key(event_id), player_id)
			raise ValueError("EVENT_NOT_ACTIVE")
		questions = json.loads(questions_json)

		# 5. Get show_correct_answers setting
		meta = await self.redis.hgetall(lc_meta_key(event_id))
		show_correct = bool(int(meta.get("show_correct_answers", "0")))

		# 6. Grade
		result = grade_answers(questions, answers, show_correct_answers=show_correct)
		submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
		result["submitted_at"] = submitted_at

		# 7. Find participation record name for queue
		participation_name = None
		try:
			parts = await self.frappe.call(
				"frappe.client.get_list",
				{
					"doctype": "Memora Live Challenge Participation",
					"filters": json.dumps([["event", "=", event_id], ["player", "=", player_id]]),
					"fields": json.dumps(["name"]),
					"limit_page_length": "1",
				},
			)
			if parts:
				participation_name = parts[0]["name"]
		except Exception:
			logger.warning(
				"lc_participation_lookup_failed",
				event_id=event_id,
				player_id=player_id,
			)

		# 8. Build answers with correctness for persistence
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

		# 9. Queue for batch persistence
		if participation_name:
			await self.queue_submission(
				{
					"event_id": event_id,
					"player_id": player_id,
					"participation_name": participation_name,
					"score": result["score"],
					"correct_count": result["correct_count"],
					"submitted_at": submitted_at,
					"answers_json": json.dumps({"answers": answers_record}),
				}
			)
		else:
			# Rollback submitted mark so player can retry
			await self.redis.srem(lc_submitted_key(event_id), player_id)
			logger.error(
				"lc_submission_not_queued_no_participation",
				event_id=event_id,
				player_id=player_id,
			)
			raise ValueError("SUBMISSION_FAILED")

		logger.info(
			"lc_submission_graded",
			event_id=event_id,
			player_id=player_id,
			score=result["score"],
			correct_count=result["correct_count"],
		)

		return result

	# -------------------------------------------------------------------------
	# Event Detail (T024)
	# -------------------------------------------------------------------------

	async def get_event_detail(self, event_id: str, player_id: str) -> dict[str, Any] | None:
		"""Get public event details with player-specific flags.

		Returns None if event not found.
		"""
		# Fetch event from FrappeClient
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

		# Get live data from Redis (may not exist if event hasn't started yet)
		redis_status = await self.redis.get(lc_status_key(event_id))
		current_count = int(await self.redis.get(lc_count_key(event_id)) or "0")

		# Player-specific flags from Redis
		has_joined = bool(await self.redis.sismember(lc_joined_key(event_id), player_id))
		has_submitted = bool(await self.redis.sismember(lc_submitted_key(event_id), player_id))

		# Use Redis status if available, otherwise MariaDB status
		status = redis_status.capitalize() if redis_status else event.get("status", "Draft")

		# Count questions
		questions = event.get("questions", [])
		question_count = len(questions) if isinstance(questions, list) else 0

		# Eligible plans
		eligible_plans_raw = event.get("eligible_plans", [])
		eligible_plans = []
		if isinstance(eligible_plans_raw, list):
			eligible_plans = [ep.get("plan", "") for ep in eligible_plans_raw if isinstance(ep, dict)]

		return {
			"event_id": event_id,
			"event_name": event.get("event_name", ""),
			"description": event.get("description"),
			"status": status,
			"scheduled_start": str(event.get("scheduled_start", "")),
			"exam_start_ts": str(event.get("exam_start_ts", "")) if event.get("exam_start_ts") else None,
			"exam_end_ts": str(event.get("exam_end_ts", "")) if event.get("exam_end_ts") else None,
			"waiting_room_duration": int(event.get("waiting_room_duration", 180)),
			"exam_duration": int(event.get("exam_duration", 10)),
			"enable_question_timer": bool(event.get("enable_question_timer", 0)),
			"question_time_limit": int(event.get("question_time_limit", 30)),
			"capacity": int(event.get("capacity", 100)),
			"current_count": current_count,
			"is_paid": bool(event.get("is_paid", 0)),
			"show_correct_answers": bool(event.get("show_correct_answers", 0)),
			"show_student_rank": bool(event.get("show_student_rank", 0)),
			"participation_xp": int(event.get("participation_xp", 0)),
			"first_place_xp": int(event.get("first_place_xp", 0)),
			"second_place_xp": int(event.get("second_place_xp", 0)),
			"third_place_xp": int(event.get("third_place_xp", 0)),
			"default_xp": int(event.get("default_xp", 0)),
			"question_count": question_count,
			"eligible_plans": eligible_plans,
			"has_joined": has_joined,
			"has_submitted": has_submitted,
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
	# WebSocket Connection Tracking (T029)
	# -------------------------------------------------------------------------

	def register_connection(self, event_id: str, ws: WebSocket) -> None:
		"""Register a WebSocket connection for an event."""
		if event_id not in self._ws_connections:
			self._ws_connections[event_id] = set()
		self._ws_connections[event_id].add(ws)

	def remove_connection(self, event_id: str, ws: WebSocket) -> None:
		"""Remove a WebSocket connection for an event."""
		conns = self._ws_connections.get(event_id)
		if conns:
			conns.discard(ws)
			if not conns:
				del self._ws_connections[event_id]

	def get_connected_count(self, event_id: str) -> int:
		"""Return number of active WebSocket connections for an event."""
		return len(self._ws_connections.get(event_id, set()))

	async def _broadcast_json(self, event_id: str, message: dict) -> int:
		"""Send JSON message to all connected WebSockets for an event.

		Returns count of successful sends. Removes dead connections.
		"""
		conns = list(self._ws_connections.get(event_id, set()))
		if not conns:
			return 0

		payload = json.dumps(message)
		dead: list[WebSocket] = []
		sent = 0

		for ws in conns:
			try:
				await ws.send_text(payload)
				sent += 1
			except Exception:
				dead.append(ws)

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

				now = datetime.now()
				status = await self.redis.get(lc_status_key(event_id))

				# Event ended (by scheduled task or another worker)
				if status == "ended":
					await self._broadcast_event_ended(event_id)
					return

				# Check if we should transition to Active
				if not exam_started and now >= exam_start_ts:
					# Authoritative start: set Redis status to active
					await self.redis.set(lc_status_key(event_id), "active", ex=LC_KEY_TTL)
					await self._broadcast_exam_start(event_id, meta)
					exam_started = True
					logger.info("lc_exam_started_by_ws", event_id=event_id)

				# Check if exam has ended
				if exam_started and now >= exam_end_ts:
					await self.drain_queue()  # Flush pending submissions before ending
					await self.redis.set(lc_status_key(event_id), "ended", ex=LC_KEY_TTL)
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
		# Strip correct_answer from questions
		safe_questions = [
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

		enable_timer = bool(int(meta.get("enable_question_timer", "0")))
		q_time_limit = int(meta.get("question_time_limit", "30"))

		msg = {
			"type": "exam_start",
			"exam_end_ts": meta.get("exam_end_ts", ""),
			"total_questions": len(safe_questions),
			"enable_question_timer": enable_timer,
			"question_time_limit": q_time_limit,
			"questions": safe_questions,
		}

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
		safe_questions = [
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

		enable_timer = bool(int(meta.get("enable_question_timer", "0")))
		q_time_limit = int(meta.get("question_time_limit", "30"))

		msg = {
			"type": "exam_start",
			"exam_end_ts": meta.get("exam_end_ts", ""),
			"total_questions": len(safe_questions),
			"enable_question_timer": enable_timer,
			"question_time_limit": q_time_limit,
			"questions": safe_questions,
		}

		try:
			await ws.send_text(json.dumps(msg))
		except Exception:
			logger.debug("lc_send_exam_start_failed", event_id=event_id)
