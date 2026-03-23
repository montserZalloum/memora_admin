"""Last Stand round engine — server-driven round-based elimination gameplay.

One instance per active Last Stand event.  Manages the full lifecycle:
answer window → evaluate → result window → next round → … → event end.

All game state lives in Redis.  No MariaDB writes during Active gameplay (FR-022).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import redis.asyncio as aioredis
import structlog

from fastapi_app.core.redis_keys import (
	LC_KEY_TTL,
	lc_alive_key,
	lc_answered_counts_key,
	lc_correct_counts_key,
	lc_eliminated_at_key,
	lc_eliminated_key,
	lc_hearts_key,
	lc_round_answers_key,
	lc_round_key,
	lc_round_signal_key,
	lc_response_times_key,
	lc_status_key,
)

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Lua: atomic answer submission (R-007)
# ---------------------------------------------------------------------------
# KEYS: [1] status_key  [2] alive_key  [3] round_key  [4] round_answers_key
# ARGV: [1] player_id   [2] round_id   [3] selected   [4] timestamp
#
# Returns:
#    1 = accepted
#   -1 = event not active
#   -2 = player not alive
#   -3 = round_id mismatch
#   -4 = answer window closed
#   -5 = already answered this round
# Lua: atomic JSON array append for response times (B-2 fix)
# KEYS: [1] response_times_hash_key
# ARGV: [1] player_id  [2] response_time_ms  [3] ttl
# Atomically decodes the existing JSON array, appends, and re-encodes.
_ATOMIC_RT_APPEND_LUA = """
local existing = redis.call('HGET', KEYS[1], ARGV[1])
local arr
if existing then
    arr = cjson.decode(existing)
else
    arr = {}
end
table.insert(arr, tonumber(ARGV[2]))
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(arr))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""

_ATOMIC_ANSWER_LUA = """
local status = redis.call('GET', KEYS[1])
if status ~= 'active' then return -1 end

if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 0 then return -2 end

local rd = {}
local raw = redis.call('HGETALL', KEYS[3])
for i = 1, #raw, 2 do rd[raw[i]] = raw[i+1] end

if rd['round_id'] ~= ARGV[2] then return -3 end
if rd['phase'] ~= 'answer' then return -4 end

local now = tonumber(ARGV[4])
local deadline = tonumber(rd['phase_end_ts'] or '0')
if now > deadline then return -4 end

if redis.call('HEXISTS', KEYS[4], ARGV[1]) == 1 then return -5 end

redis.call('HSET', KEYS[4], ARGV[1], cjson.encode({selected=ARGV[3], ts=ARGV[4]}))
return 1
"""

# Map Lua return codes → REST error codes
ANSWER_ERROR_CODES: dict[int, str] = {
	-1: "EVENT_NOT_ACTIVE",
	-2: "NOT_ALIVE",
	-3: "ROUND_MISMATCH",
	-4: "WINDOW_CLOSED",
	-5: "ALREADY_ANSWERED",
}


class LastStandEngine:
	"""Server-driven round loop for a single Last Stand event.

	Created when Waiting → Active for last_stand events.
	Runs as an asyncio Task managed by LiveChallengeService.
	"""

	def __init__(
		self,
		redis_client: aioredis.Redis,
		event_id: str,
		questions: list[dict],
		meta: dict,
		broadcast_json: Callable[[str, dict], Awaitable[int]],
		broadcast_personalized: Callable[[str, dict, dict[str, dict]], Awaitable[int]],
		on_event_ended: Callable[[str, str, int, int], Awaitable[None]],
		resume_from_idx: int = 0,
	):
		self.redis = redis_client
		self.event_id = event_id
		self.questions = questions
		self.meta = meta
		self._broadcast_json = broadcast_json
		self._broadcast_personalized = broadcast_personalized
		self._on_event_ended = on_event_ended

		self.time_limit = int(meta.get("question_time_limit", "30"))
		self.result_window = int(meta.get("result_window_duration", "3"))
		self.starting_hearts = int(meta.get("starting_hearts", "3"))
		self.total_rounds = len(questions)
		self._resume_from_idx = resume_from_idx

		self._answer_script: Any = None
		self._rt_append_script: Any = None
		self._stopped = False

	# ------------------------------------------------------------------
	# Lua answer script
	# ------------------------------------------------------------------

	async def _ensure_answer_script(self):
		if self._answer_script is None:
			self._answer_script = self.redis.register_script(_ATOMIC_ANSWER_LUA)
		return self._answer_script

	async def _ensure_rt_append_script(self):
		if self._rt_append_script is None:
			self._rt_append_script = self.redis.register_script(_ATOMIC_RT_APPEND_LUA)
		return self._rt_append_script

	async def submit_answer(
		self, player_id: str, round_id: str, selected: str,
	) -> int:
		"""Execute atomic answer submission.  Returns 1 on success, <0 on error."""
		script = await self._ensure_answer_script()
		result = await script(
			keys=[
				lc_status_key(self.event_id),
				lc_alive_key(self.event_id),
				lc_round_key(self.event_id),
				lc_round_answers_key(self.event_id, round_id),
			],
			args=[player_id, round_id, selected, str(time.time())],
		)
		return int(result)

	# ------------------------------------------------------------------
	# Main round loop
	# ------------------------------------------------------------------

	async def run(self) -> None:
		"""Execute the full round loop until all questions done or all eliminated."""
		last_idx = self._resume_from_idx
		try:
			for idx, question in enumerate(self.questions):
				# Skip already-completed rounds on resume
				if idx < self._resume_from_idx:
					continue
				if self._stopped:
					break
				last_idx = idx

				alive_count = await self.redis.scard(lc_alive_key(self.event_id))
				if alive_count == 0:
					await self._end_event("all_eliminated", idx)
					return

				round_id = f"{self.event_id}-R{idx}"
				answer_start = time.time()
				phase_end_ts = answer_start + self.time_limit

				# -- Set round state (answer phase) --
				pipe = self.redis.pipeline()
				pipe.hset(
					lc_round_key(self.event_id),
					mapping={
						"round_id": round_id,
						"question_idx": str(idx),
						"phase": "answer",
						"phase_end_ts": str(phase_end_ts),
						"alive_count": str(alive_count),
					},
				)
				pipe.expire(lc_round_key(self.event_id), LC_KEY_TTL)
				await pipe.execute()

				# -- Broadcast round_start (personalized is_alive) --
				await self._broadcast_round_start(idx, round_id, question, alive_count)

				# -- Answer window --
				await self._wait_for_answers(round_id, phase_end_ts)

				# -- Transition to result phase --
				result_end_ts = time.time() + self.result_window
				await self.redis.hset(
					lc_round_key(self.event_id),
					mapping={"phase": "result", "phase_end_ts": str(result_end_ts)},
				)

				# -- Evaluate round --
				await self._evaluate_round(round_id, idx, question, answer_start)

				# -- Broadcast alive_count_update (admin dashboards, spectator UIs) --
				alive_after = await self.redis.scard(lc_alive_key(self.event_id))
				eliminated_after = await self.redis.scard(lc_eliminated_key(self.event_id))
				await self._broadcast_json(self.event_id, {
					"type": "alive_count_update",
					"alive_count": alive_after,
					"eliminated_count": eliminated_after,
					"current_round": idx,
				})

				# -- Result window --
				remaining = result_end_ts - time.time()
				if remaining > 0:
					await asyncio.sleep(remaining)

			# All questions finished
			if not self._stopped:
				await self._end_event("all_finished", self.total_rounds)

		except asyncio.CancelledError:
			logger.info("last_stand_engine_cancelled", event_id=self.event_id)
		except Exception:
			logger.exception("last_stand_engine_error", event_id=self.event_id)
			try:
				await self._end_event("time_ceiling", last_idx)
			except Exception:
				pass

	# ------------------------------------------------------------------
	# Round phases
	# ------------------------------------------------------------------

	async def _broadcast_round_start(
		self, idx: int, round_id: str, question: dict, alive_count: int,
	) -> None:
		safe_q = {
			"idx": question["idx"],
			"question_text": question["question_text"],
			"option_a": question["option_a"],
			"option_b": question["option_b"],
			"option_c": question["option_c"],
			"option_d": question["option_d"],
		}
		base_msg: dict[str, Any] = {
			"type": "round_start",
			"round_id": round_id,
			"question_idx": idx,
			"question": safe_q,
			"time_limit": self.time_limit,
			"alive_count": alive_count,
			"total_rounds": self.total_rounds,
			"is_alive": False,  # default for spectators / eliminated
		}
		alive_members = await self.redis.smembers(lc_alive_key(self.event_id))
		player_states = {pid: {"is_alive": True} for pid in alive_members}
		await self._broadcast_personalized(self.event_id, base_msg, player_states)

	async def _wait_for_answers(self, round_id: str, phase_end_ts: float) -> None:
		"""Wait until answer window expires or all alive players answered."""
		pubsub = self.redis.pubsub()
		channel = lc_round_signal_key(self.event_id)
		await pubsub.subscribe(channel)

		try:
			while True:
				remaining = phase_end_ts - time.time()
				if remaining <= 0 or self._stopped:
					break

				# Check early close
				pipe = self.redis.pipeline()
				pipe.hlen(lc_round_answers_key(self.event_id, round_id))
				pipe.scard(lc_alive_key(self.event_id))
				answer_count, current_alive = await pipe.execute()
				if current_alive > 0 and answer_count >= current_alive:
					logger.info(
						"last_stand_early_close",
						event_id=self.event_id,
						round_id=round_id,
					)
					break

				# Poll pub/sub with 100ms timeout
				try:
					msg = await asyncio.wait_for(
						pubsub.get_message(
							ignore_subscribe_messages=True, timeout=0.1,
						),
						timeout=min(0.15, remaining),
					)
					if msg and msg.get("type") == "message":
						data = msg.get("data", b"")
						if isinstance(data, bytes):
							data = data.decode()
						if data == "all_answered":
							break
				except asyncio.TimeoutError:
					pass
		finally:
			await pubsub.unsubscribe(channel)
			await pubsub.close()

	async def _evaluate_round(
		self,
		round_id: str,
		question_idx: int,
		question: dict,
		answer_start: float,
	) -> None:
		"""Evaluate answers, deduct hearts, eliminate players, broadcast results."""
		correct_answer = question["correct_answer"]
		round_answers_key = lc_round_answers_key(self.event_id, round_id)
		answers_raw = await self.redis.hgetall(round_answers_key)
		# Set TTL on per-round answer key so it doesn't persist indefinitely (S-4)
		await self.redis.expire(round_answers_key, LC_KEY_TTL)
		alive_members = await self.redis.smembers(lc_alive_key(self.event_id))

		# -- Pass 1: Determine correctness + response times --
		player_results: dict[str, dict] = {}
		for pid in alive_members:
			answer_data = answers_raw.get(pid)
			if answer_data:
				try:
					parsed = json.loads(answer_data)
					selected = parsed.get("selected")
					ts = float(parsed.get("ts", 0))
					is_correct = selected == correct_answer
					response_time_ms = max(0, int((ts - answer_start) * 1000))
				except (json.JSONDecodeError, TypeError, ValueError):
					is_correct = False
					response_time_ms = None
				player_results[pid] = {
					"answered": True,
					"is_correct": is_correct,
					"response_time_ms": response_time_ms,
				}
			else:
				player_results[pid] = {
					"answered": False,
					"is_correct": False,
					"response_time_ms": None,
				}

		# Classify players
		answered_pids = [p for p, r in player_results.items() if r["answered"]]
		correct_pids = [p for p, r in player_results.items() if r["is_correct"]]
		incorrect_pids = [p for p, r in player_results.items() if not r["is_correct"]]

		# -- Pass 2: Update stats (batched pipeline) --
		stats_pipe = self.redis.pipeline()
		for pid in answered_pids:
			stats_pipe.hincrby(lc_answered_counts_key(self.event_id), pid, 1)
		if answered_pids:
			stats_pipe.expire(lc_answered_counts_key(self.event_id), LC_KEY_TTL)
		for pid in correct_pids:
			stats_pipe.hincrby(lc_correct_counts_key(self.event_id), pid, 1)
		if correct_pids:
			stats_pipe.expire(lc_correct_counts_key(self.event_id), LC_KEY_TTL)
		await stats_pipe.execute()

		# -- Pass 2b: Update response times (atomic Lua append per player) --
		rt_pids = [
			p for p in answered_pids
			if player_results[p]["response_time_ms"] is not None
		]
		if rt_pids:
			rt_script = await self._ensure_rt_append_script()
			rt_key = lc_response_times_key(self.event_id)
			for pid in rt_pids:
				await rt_script(
					keys=[rt_key],
					args=[pid, str(player_results[pid]["response_time_ms"]), str(LC_KEY_TTL)],
				)

		# -- Pass 3: Deduct hearts for incorrect players (HINCRBY returns new value) --
		new_hearts_map: dict[str, int] = {}
		if incorrect_pids:
			heart_pipe = self.redis.pipeline()
			for pid in incorrect_pids:
				heart_pipe.hincrby(lc_hearts_key(self.event_id), pid, -1)
			new_hearts_list = await heart_pipe.execute()
			for pid, nh in zip(incorrect_pids, new_hearts_list):
				new_hearts_map[pid] = int(nh)

			# Mark hearts as deducted for this round (crash recovery guard — S-1)
			await self.redis.hset(
				lc_round_key(self.event_id), "hearts_deducted", "1",
			)

		# -- Pass 4: Eliminate players with hearts <= 0 --
		to_eliminate = [pid for pid, nh in new_hearts_map.items() if nh <= 0]
		eliminated_this_round = len(to_eliminate)
		if to_eliminate:
			elim_pipe = self.redis.pipeline()
			for pid in to_eliminate:
				elim_pipe.smove(
					lc_alive_key(self.event_id),
					lc_eliminated_key(self.event_id),
					pid,
				)
				elim_pipe.hset(
					lc_eliminated_at_key(self.event_id), pid, str(question_idx),
				)
			elim_pipe.expire(lc_eliminated_key(self.event_id), LC_KEY_TTL)
			elim_pipe.expire(lc_eliminated_at_key(self.event_id), LC_KEY_TTL)
			await elim_pipe.execute()

		# -- Read current hearts for correct players (unchanged this round) --
		correct_hearts: dict[str, int] = {}
		if correct_pids:
			ch_pipe = self.redis.pipeline()
			for pid in correct_pids:
				ch_pipe.hget(lc_hearts_key(self.event_id), pid)
			hearts_list = await ch_pipe.execute()
			for pid, h in zip(correct_pids, hearts_list):
				correct_hearts[pid] = int(h or "0")

		# -- Build player states for personalized broadcast --
		new_alive = await self.redis.scard(lc_alive_key(self.event_id))
		player_states: dict[str, dict] = {}

		for pid in alive_members:
			r = player_results[pid]
			if not r["is_correct"]:
				nh = new_hearts_map.get(pid, 0)
				is_elim = pid in to_eliminate
				player_states[pid] = {
					"hearts_remaining": max(0, nh),
					"heart_lost": True,
					"is_correct": False if r["answered"] else None,
					"is_eliminated": is_elim,
					"is_alive": not is_elim,
				}
			else:
				player_states[pid] = {
					"hearts_remaining": correct_hearts.get(pid, 0),
					"heart_lost": False,
					"is_correct": True,
					"is_eliminated": False,
					"is_alive": True,
				}

		base_msg: dict[str, Any] = {
			"type": "round_result",
			"round_id": round_id,
			"question_idx": question_idx,
			"alive_count": new_alive,
			"eliminated_this_round": eliminated_this_round,
			"result_duration": self.result_window,
			# Defaults for spectators (not in player_states)
			"hearts_remaining": 0,
			"heart_lost": False,
			"is_correct": None,
			"is_eliminated": False,
			"is_alive": False,
		}
		await self._broadcast_personalized(self.event_id, base_msg, player_states)

	# ------------------------------------------------------------------
	# End event
	# ------------------------------------------------------------------

	async def _end_event(self, reason: str, rounds_played: int) -> None:
		"""Store end info in Redis and delegate to service callback."""
		self._stopped = True
		alive_count = await self.redis.scard(lc_alive_key(self.event_id))

		# Persist end metadata in round HASH (used by reconciliation / status reads)
		await self.redis.hset(
			lc_round_key(self.event_id),
			mapping={
				"phase": "ended",
				"end_reason": reason,
				"final_alive_count": str(alive_count),
				"total_rounds_played": str(rounds_played),
			},
		)

		# Set status to ended
		await self.redis.set(lc_status_key(self.event_id), "ended", ex=LC_KEY_TTL)

		# Notify service (triggers broadcast + reconciliation)
		await self._on_event_ended(self.event_id, reason, alive_count, rounds_played)

		logger.info(
			"last_stand_event_ended",
			event_id=self.event_id,
			reason=reason,
			alive_count=alive_count,
			rounds_played=rounds_played,
		)

	def stop(self) -> None:
		"""Signal the engine to stop after the current phase completes."""
		self._stopped = True
