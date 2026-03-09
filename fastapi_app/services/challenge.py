"""Challenge Hub service — progress tracking, grading, XP, and hierarchy state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import (
	CH_ATTEMPT_LOCK_TTL,
	CH_PROGRESS_KEY_TTL,
	CH_QUESTION_LOOKUP_KEY_TTL,
	CH_SETTINGS_KEY_TTL,
	ch_attempt_buffer_key,
	ch_attempt_lock_key,
	ch_leaderboard_key,
	ch_leaderboard_subject_key,
	ch_question_lookup_key,
	ch_progress_key,
	ch_settings_key,
	dirty_ch_progress_key,
	interaction_buffer_key,
)
from fastapi_app.models.challenge import (
	AttemptRequest,
	AttemptResponse,
	ChallengeHierarchyResponse,
	ChallengeSubjectSummary,
	NextTopicInfo,
	TopicState,
	TrackState,
	UnitState,
)
from fastapi_app.services.hydration import guarded_hydrate

if TYPE_CHECKING:
	from fastapi_app.services.access import AccessService
	from fastapi_app.services.frappe_client import FrappeClient
	from fastapi_app.services.hierarchy import HierarchyService
	from fastapi_app.services.plan import PlanService
	from fastapi_app.services.stats import StatsService

logger = structlog.get_logger()

# Low-memory dense-rank helper:
# Counts distinct score tiers strictly above a player's XP and returns
# the nearest higher tier. Uses single-member ZRANGEBYSCORE stepping
# inside Redis to avoid fetching all members into Python memory.
_RANK_TIERS_ABOVE_LUA = """
local key = KEYS[1]
local xp = tonumber(ARGV[1])
local count = 0
local min_above = -1
local current_min = xp + 1

while true do
    local entries = redis.call('ZRANGEBYSCORE', key, current_min, '+inf', 'WITHSCORES', 'LIMIT', 0, 1)
    if #entries == 0 then break end
    local score = math.floor(tonumber(entries[2]))
    count = count + 1
    if min_above == -1 then min_above = score end
    current_min = score + 1
end

return {count, min_above}
"""


class ChallengeService:
	"""Manages Challenge Hub progress, grading, XP delta, and hierarchy state.

	Redis keys (all from redis_keys.py):
	- ch_progress_key(player, subject) → HASH of topic progress
	- ch_leaderboard_key(season, plan) → ZSET of Challenge XP
	- ch_leaderboard_subject_key(season, plan, subject) → ZSET per-subject
	- ch_idem_key(player, attempt_key) → STRING for idempotency
	- dirty_ch_progress_key() → SET of dirty player:subject:season triples
	- ch_attempt_buffer_key() → LIST of serialized attempt payloads

	Self-heals: On cache miss, ensure_hydrated() restores from Memora Challenge Progress
	records in MariaDB via FrappeClient.
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient | None = None,
		settings: dict | None = None,
		hierarchy_service: HierarchyService | None = None,
		access_service: AccessService | None = None,
		stats_service: StatsService | None = None,
		plan_service: PlanService | None = None,
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self.settings = settings or {}
		self.hierarchy_svc = hierarchy_service
		self.access_svc = access_service
		self.stats_svc = stats_service
		self.plan_svc = plan_service
		self._rank_tiers_script = self.redis.register_script(_RANK_TIERS_ABOVE_LUA)

	async def ensure_hydrated(self, player_id: str, subject_id: str) -> None:
		"""Ensure challenge progress cache exists, hydrating from MariaDB if missing.

		Uses distributed lock + semaphore to prevent thundering herd.
		Only one request per player+subject hydrates at a time; others wait.
		"""
		key = ch_progress_key(player_id, subject_id)

		if await self.redis.exists(key):
			return

		if not self.frappe:
			logger.warning(
				"ch_hydration_skipped",
				player_id=player_id,
				subject_id=subject_id,
				reason="no_frappe_client",
			)
			return

		async def _do_hydrate() -> bool:
			try:
				records = await self.frappe.call(
					"memora_admin.api.challenge.get_player_challenge_progress",
					{"player_id": player_id, "subject_id": subject_id},
				)
				if not records:
					return False

				pipe = self.redis.pipeline()
				for rec in records:
					topic_id = rec["topic"]
					progress_data = json.dumps(
						{
							"stamped": rec.get("stamped", 0),
							"best_correct": rec.get("best_correct", 0),
							"best_score_pct": rec.get("best_score_pct", 0),
							"best_passing_pct": rec.get("best_passing_pct", 0),
							"total_xp": rec.get("total_xp_earned", 0),
							"attempt_count": rec.get("attempt_count", 0),
						}
					)
					pipe.hset(key, topic_id, progress_data)
				pipe.expire(key, CH_PROGRESS_KEY_TTL)
				await pipe.execute()

				logger.info(
					"ch_hydrated_from_mariadb",
					player_id=player_id,
					subject_id=subject_id,
					topic_count=len(records),
				)
				return True
			except Exception as e:
				logger.error(
					"ch_hydration_failed",
					player_id=player_id,
					subject_id=subject_id,
					error=str(e),
				)
				return False

		await guarded_hydrate(self.redis, key, _do_hydrate)

	async def _get_progress_map(self, player_id: str, subject_id: str) -> dict[str, dict]:
		"""Get challenge progress for all topics in a subject.

		Returns dict of topic_id → {stamped, best_correct, best_score_pct, ...}.
		"""
		await self.ensure_hydrated(player_id, subject_id)
		key = ch_progress_key(player_id, subject_id)
		raw = await self.redis.hgetall(key)

		result = {}
		for topic_id, data_str in raw.items():
			try:
				result[topic_id] = json.loads(data_str)
			except (json.JSONDecodeError, TypeError):
				continue

		if not raw:
			logger.debug("ch_cache_miss", player_id=player_id, subject_id=subject_id)

		return result

	async def get_challenge_subjects(
		self,
		player_id: str,
		plan_id: str | None,
	) -> list[ChallengeSubjectSummary]:
		"""Load player's plan subjects with challenge summary stats.

		For each subject: count total non-empty topics, stamped topics, total challenge XP.
		"""
		if not plan_id or not self.plan_svc:
			return []

		manifest = await self.plan_svc.get_manifest(plan_id)
		if not manifest:
			return []

		summaries = []
		for plan_subject in manifest.subjects:
			subject_id = plan_subject.id
			hierarchy = await self.hierarchy_svc.get_hierarchy(subject_id) if self.hierarchy_svc else None
			if not hierarchy:
				continue

			# Load challenge progress for this subject
			progress_map = await self._get_progress_map(player_id, subject_id)

			# Walk hierarchy, count non-empty topics (mcq_count > 0)
			total_topics = 0
			stamped_topics = 0
			total_xp = 0

			for track in hierarchy.tracks:
				for unit in track.units:
					prev_stamped = True  # First topic has no predecessor constraint
					for topic in unit.topics:
						if topic.mcq_count == 0:
							# Empty topics auto-stamp if predecessor is stamped
							if prev_stamped:
								prev_stamped = True  # Chain continues
							continue

						total_topics += 1
						tp = progress_map.get(topic.topic_id, {})
						is_stamped = bool(tp.get("stamped", 0))
						total_xp += int(tp.get("total_xp", 0))

						if is_stamped:
							stamped_topics += 1
							prev_stamped = True
						else:
							prev_stamped = False

			summaries.append(
				ChallengeSubjectSummary(
					subject_id=subject_id,
					subject_name=plan_subject.title,
					total_topics=total_topics,
					stamped_topics=stamped_topics,
					total_challenge_xp=total_xp,
				)
			)

		logger.info(
			"ch_hierarchy_subjects",
			player_id=player_id,
			subject_count=len(summaries),
		)
		return summaries

	async def get_challenge_hierarchy(
		self,
		player_id: str,
		plan_id: str | None,
		subject_id: str,
	) -> ChallengeHierarchyResponse | None:
		"""Build challenge hierarchy for a subject with topic states.

		For each topic, evaluates 3 unlock conditions:
		1. Content access (via AccessService.check_access_with_plan)
		2. Normal path complete (all lessons done via stats cache)
		3. Previous topic stamped in Challenge Hub (sequential gate)

		Empty topics (mcq_count == 0) are auto-stamped when predecessor is stamped
		and hidden from the response (FR-009).

		Returns None if subject is not in the player's plan.
		"""
		if not self.hierarchy_svc:
			return None

		# Challenge Hub requires plan membership — reject if no plan
		if not plan_id or not self.plan_svc:
			return None

		manifest = await self.plan_svc.get_manifest(plan_id)
		if not manifest:
			return None

		plan_subject_ids = {ps.id for ps in manifest.subjects}
		if subject_id not in plan_subject_ids:
			return None

		hierarchy = await self.hierarchy_svc.get_hierarchy(subject_id)
		if not hierarchy:
			return None

		# Load challenge progress and stats in parallel-ish fashion
		progress_map = await self._get_progress_map(player_id, subject_id)

		# Get stats for topic completion check
		stats = None
		if self.stats_svc:
			stats = await self.stats_svc.get_stats(player_id, subject_id, hierarchy.version)

		# Access check is subject-level for this endpoint; avoid per-topic repetition.
		subject_has_access = True
		if self.access_svc:
			subject_has_access = await self.access_svc.check_access_with_plan(
				player_id, f"SUB-{subject_id}", plan_id
			)

		# Build free-content lookup sets from hierarchy (sourced from Plan Subject meta_data)
		free_units_set = set(hierarchy.free_units)
		free_topics_set = set(hierarchy.free_topics)
		has_free_content = bool(free_units_set or free_topics_set)

		tracks_response = []
		for track in hierarchy.tracks:
			track_has_access = subject_has_access

			if not track_has_access and not has_free_content:
				tracks_response.append(
					TrackState(
						track_id=track.track_id,
						track_name=track.track_title or track.track_id,
						has_access=False,
						units=[],
					)
				)
				continue

			units_response = []
			for unit in track.units:
				topics_response = []
				unit_is_free = unit.unit_id in free_units_set

				# Track predecessor stamp state for sequential unlock within unit
				prev_stamped = True  # First topic has no predecessor constraint

				for topic in unit.topics:
					# Determine if this topic is auto-stamped (empty + predecessor stamped)
					is_empty = topic.mcq_count == 0
					tp = progress_map.get(topic.topic_id, {})
					is_explicitly_stamped = bool(tp.get("stamped", 0))

					if is_empty:
						# Auto-stamp chain: empty topic inherits predecessor's stamped state
						if prev_stamped:
							prev_stamped = True  # Chain propagates
						# Empty topics are hidden from response
						continue

					# Check 3 unlock conditions for non-empty topics
					# Condition 1: Content access (subject-level grant OR free topic/unit)
					topic_is_free = unit_is_free or topic.topic_id in free_topics_set
					has_access = subject_has_access or topic_is_free

					# Condition 2: Normal path complete (all lessons in topic done)
					normal_path_complete = False
					if stats:
						topic_completed = int(stats.get(f"{topic.topic_id}:completed", 0))
						topic_total = int(stats.get(f"{topic.topic_id}:total", 0))
						normal_path_complete = topic_total > 0 and topic_completed >= topic_total

					# Condition 3: Previous topic stamped
					predecessor_stamped = prev_stamped

					# Determine state
					if is_explicitly_stamped:
						state = "stamped"
					elif has_access and normal_path_complete and predecessor_stamped:
						state = "open"
					else:
						state = "locked"

					# Determine lock reason
					lock_reason = None
					if state == "locked":
						if not has_access:
							lock_reason = "NO_ACCESS"
						elif not normal_path_complete:
							lock_reason = "NORMAL_PATH_INCOMPLETE"
						elif not predecessor_stamped:
							lock_reason = "PREVIOUS_NOT_STAMPED"

					topics_response.append(
						TopicState(
							topic_id=topic.topic_id,
							topic_name=topic.topic_title or topic.topic_id,
							state=state,
							mcq_count=topic.mcq_count,
							best_score_pct=float(tp["best_score_pct"]) if tp.get("best_score_pct") else None,
							best_passing_pct=float(tp["best_passing_pct"]) if tp.get("best_passing_pct") else None,
							total_xp=int(tp.get("total_xp", 0)),
							attempt_count=int(tp.get("attempt_count", 0)),
							normal_path_complete=normal_path_complete,
							has_access=has_access,
							lock_reason=lock_reason,
						)
					)

					# Update predecessor state for next topic
					prev_stamped = is_explicitly_stamped

				units_response.append(
					UnitState(
						unit_id=unit.unit_id,
						unit_name=unit.unit_title or unit.unit_id,
						topics=topics_response,
					)
				)

			tracks_response.append(
				TrackState(
					track_id=track.track_id,
					track_name=track.track_title or track.track_id,
					has_access=track_has_access or has_free_content,
					units=units_response,
				)
			)

		topic_count = sum(len(u.topics) for t in tracks_response for u in t.units)
		logger.info(
			"ch_hierarchy_detail",
			player_id=player_id,
			subject_id=subject_id,
			track_count=len(tracks_response),
			topic_count=topic_count,
		)
		return ChallengeHierarchyResponse(
			subject_id=subject_id,
			tracks=tracks_response,
		)

	# =========================================================================
	# Phase 4: Core Gameplay (T015-T019)
	# =========================================================================

	def _grade_attempt(
		self,
		questions: list,
		pass_threshold: int,
		question_lookup: dict[str, dict] | None,
	) -> tuple[int, float, bool]:
		"""Grade an attempt server-side using canonical correct answers.

		Verifies each question's chosen_answer against correct_choice from
		the Review Item. The client-supplied `correct` flag is IGNORED —
		correctness is determined entirely server-side.

		Validates:
		- No duplicate item_ids (prevents inflation by repeating correct answers)
		- Submitted count matches expected question count for the topic
		- Every item_id exists in the question_lookup with a valid correct_choice

		If question_lookup is None (FrappeClient unavailable or call failed),
		falls back to client-reported correctness with a warning log.

		Returns (correct_count, score_pct, passed).
		Raises ValueError on validation failure.
		"""
		total = len(questions)

		# Check for duplicate item_ids — prevents XP inflation via repeated correct answers
		seen_ids = set()
		for q in questions:
			if q.item_id in seen_ids:
				raise ValueError(f"DUPLICATE_ITEM:{q.item_id}")
			seen_ids.add(q.item_id)

		if question_lookup is not None:
			# Validate submitted count matches expected questions for the topic
			if total != len(question_lookup):
				raise ValueError(
					f"QUESTION_COUNT_MISMATCH:submitted={total},expected={len(question_lookup)}"
				)

			# Server-side grading: verify answers against canonical correct_choice
			correct_count = 0
			for q in questions:
				answer_key = question_lookup.get(q.item_id)
				if not answer_key:
					raise ValueError(f"UNKNOWN_ITEM:{q.item_id}")
				canonical = answer_key.get("correct_choice")
				if canonical is None:
					# Review Item exists but has no correct_choice set — reject
					raise ValueError(f"NO_ANSWER_KEY:{q.item_id}")
				if q.chosen_answer == canonical:
					correct_count += 1
		else:
			# Fallback: no FrappeClient available — trust client (degraded mode)
			logger.warning("ch_grade_fallback_client_trusted", reason="no_question_lookup")
			correct_count = sum(1 for q in questions if q.correct)

		score_pct = round(correct_count / total * 100, 2)
		passed = score_pct >= pass_threshold
		return correct_count, score_pct, passed

	def _update_best_scores(
		self,
		current_correct: int,
		current_score_pct: float,
		passed: bool,
		prev_best_correct: int,
		prev_best_score_pct: float,
		prev_best_passing_pct: float,
	) -> tuple[int, float, float, bool]:
		"""Update best scores if current attempt improves on previous.

		Returns (best_correct, best_score_pct, best_passing_pct, is_new_best).
		"""
		is_new_best = current_correct > prev_best_correct
		best_correct = max(current_correct, prev_best_correct)
		best_score_pct = max(current_score_pct, prev_best_score_pct)

		best_passing_pct = prev_best_passing_pct
		if passed and current_score_pct > prev_best_passing_pct:
			best_passing_pct = current_score_pct

		return best_correct, best_score_pct, best_passing_pct, is_new_best

	def _calculate_xp_delta(
		self,
		current_correct: int,
		previous_best_correct: int,
		xp_per_question: int,
	) -> int:
		"""Calculate XP delta — only improvement earns XP.

		Returns max(0, current_correct - previous_best_correct) * xp_per_question.
		"""
		return max(0, current_correct - previous_best_correct) * xp_per_question

	async def _count_distinct_tiers_above(self, key: str, xp: int) -> tuple[int, int]:
		"""Return (distinct_tiers_above, nearest_higher_tier_xp or -1).

		Uses Lua for low-memory dense-rank math; falls back to iterative
		single-entry probes if script execution fails.
		"""
		try:
			raw = await self._rank_tiers_script(keys=[key], args=[str(int(xp))])
			if isinstance(raw, (list, tuple)) and len(raw) == 2:
				return int(raw[0]), int(raw[1])
		except Exception as e:
			logger.warning("ch_rank_script_failed", key=key, error=str(e))

		# Fallback: still low-memory (LIMIT 1 per tier), avoids O(N) result transfer.
		distinct_above = 0
		min_above = -1
		current_min = int(xp) + 1

		while True:
			next_tier = await self.redis.zrangebyscore(
				key,
				current_min,
				"+inf",
				withscores=True,
				start=0,
				num=1,
			)
			if not next_tier:
				break

			tier_xp = int(next_tier[0][1])
			distinct_above += 1
			if min_above == -1:
				min_above = tier_xp
			current_min = tier_xp + 1

		return distinct_above, min_above

	async def _push_fsrs_interactions(
		self,
		player_id: str,
		questions: list,
		question_lookup: dict[str, dict] | None,
		graded_correctness: dict[str, bool],
		pipe: redis.client.Pipeline,
	) -> None:
		"""Push per-question FSRS interactions to the interaction buffer via pipeline.

		question_lookup maps item_id → {lesson, stage_id} from the Review Item data.
		graded_correctness maps item_id → bool (server-graded result).
		Skips items not found in the lookup (graceful degradation).
		If question_lookup is None, all items are skipped.
		"""
		if question_lookup is None:
			logger.warning("ch_fsrs_push_skipped", reason="no_question_lookup")
			return

		now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
		buf_key = interaction_buffer_key()

		pushed = 0
		for q in questions:
			item_meta = question_lookup.get(q.item_id)
			if not item_meta:
				logger.warning("ch_fsrs_item_not_found", item_id=q.item_id)
				continue

			is_correct = graded_correctness.get(q.item_id, False)
			interaction = json.dumps(
				{
					"player": player_id,
					"lesson": item_meta["lesson"],
					"stage_id": item_meta["stage_id"],
					"item_id": q.item_id,
					"event_type": "Completed",
					"errors_count": 0 if is_correct else 1,
					"time_spent": q.time_spent,
					"timestamp": now_iso,
					"metadata": {"source": "challenge_hub"},
				}
			)
			pipe.rpush(buf_key, interaction)
			pushed += 1

		logger.info("ch_fsrs_pushed", player_id=player_id, pushed=pushed, total=len(questions))

	async def _get_challenge_settings(self) -> dict:
		"""Get challenge settings (xp_per_question, pass_threshold, etc.).

		Uses Redis cache first, then Frappe on miss.
		Falls back to defaults if unavailable.
		"""
		defaults = {
			"xp_per_question": 5,
			"pass_threshold": 50,
			"lb_top_count": 20,
			"lb_refresh_interval": 300,
		}

		try:
			cached = await self.redis.get(ch_settings_key())
			if cached:
				payload = json.loads(cached)
				return {
					"xp_per_question": payload.get("xp_per_question", 5),
					"pass_threshold": payload.get("pass_threshold", 50),
					"lb_top_count": payload.get("lb_top_count", 20),
					"lb_refresh_interval": payload.get("lb_refresh_interval", 300),
				}
		except Exception as e:
			logger.warning("ch_settings_cache_read_failed", error=str(e))

		if not self.frappe:
			return defaults

		try:
			result = await self.frappe.call("memora_admin.api.challenge.get_challenge_settings")
			if not result:
				return defaults

			settings = {
				"xp_per_question": result.get("xp_per_question", 5),
				"pass_threshold": result.get("pass_threshold", 50),
				"lb_top_count": result.get("lb_top_count", 20),
				"lb_refresh_interval": result.get("lb_refresh_interval", 300),
			}
			await self.redis.set(ch_settings_key(), json.dumps(settings), ex=CH_SETTINGS_KEY_TTL)
			return settings
		except Exception as e:
			logger.warning("ch_settings_load_failed", error=str(e))

		return defaults

	async def _get_question_lookup(self, topic_id: str) -> dict[str, dict] | None:
		"""Get item_id → {lesson, stage_id, correct_choice} mapping for a topic's MCQ Review Items.

		Queries Frappe for Review Item records. Returns dict mapping item_id
		to {lesson, stage_id, correct_choice} needed for grading and FSRS interaction push.

		Returns None if FrappeClient is unavailable or the call fails (triggers
		degraded fallback in _grade_attempt). Returns {} if topic has no questions.
		"""
		cache_key = ch_question_lookup_key(topic_id)

		try:
			cached = await self.redis.get(cache_key)
			if cached:
				data = json.loads(cached)
				return data if isinstance(data, dict) else {}
		except Exception as e:
			logger.warning("ch_question_lookup_cache_read_failed", topic_id=topic_id, error=str(e))

		if not self.frappe:
			return None

		try:
			records = await self.frappe.call(
				"memora_admin.api.challenge.get_topic_question_items",
				{"topic_id": topic_id},
			)
			if not records:
				# Do NOT cache empty results — transient Frappe issues (stale workers,
				# permission errors) can return [] even when items exist in DB.
				# Next request will retry the Frappe call.
				logger.warning("ch_question_lookup_empty", topic_id=topic_id)
				return {}

			lookup = {
				r["item_id"]: {
					"lesson": r["lesson"],
					"stage_id": r["stage_id"],
					"correct_choice": int(r["correct_choice"]) if r.get("correct_choice") else None,
				}
				for r in records
			}
			await self.redis.set(cache_key, json.dumps(lookup), ex=CH_QUESTION_LOOKUP_KEY_TTL)
			return lookup
		except Exception as e:
			logger.warning("ch_question_lookup_failed", topic_id=topic_id, error=str(e))
			return None

	async def _evaluate_next_topic(
		self,
		player_id: str,
		plan_id: str | None,
		subject_id: str,
		current_topic_id: str,
		now_stamped: bool,
	) -> NextTopicInfo | None:
		"""If this stamp unlocked the next topic, return its info."""
		if not now_stamped or not self.hierarchy_svc:
			return None

		hierarchy = await self.hierarchy_svc.get_hierarchy(subject_id)
		if not hierarchy:
			return None

		progress_map = await self._get_progress_map(player_id, subject_id)
		stats = None
		if self.stats_svc:
			stats = await self.stats_svc.get_stats(player_id, subject_id, hierarchy.version)
		subject_has_access = True
		if self.access_svc:
			subject_has_access = await self.access_svc.check_access_with_plan(
				player_id, f"SUB-{subject_id}", plan_id
			)

		# Walk hierarchy to find next non-empty topic after current_topic_id
		found_current = False
		for track in hierarchy.tracks:
			for unit in track.units:
				for topic in unit.topics:
					if topic.topic_id == current_topic_id:
						found_current = True
						continue
					if not found_current:
						continue

					# Skip empty topics (auto-stamped)
					if topic.mcq_count == 0:
						continue

					# Check if this topic is now unlocked
					tp = progress_map.get(topic.topic_id, {})
					if tp.get("stamped"):
						# Already stamped, skip
						continue

					# Check access
					if not subject_has_access:
						return NextTopicInfo(topic_id=topic.topic_id, state="locked")

					# Check normal path complete
					normal_complete = False
					if stats:
						tc = int(stats.get(f"{topic.topic_id}:completed", 0))
						tt = int(stats.get(f"{topic.topic_id}:total", 0))
						normal_complete = tt > 0 and tc >= tt
					if not normal_complete:
						return NextTopicInfo(topic_id=topic.topic_id, state="locked")

					# Predecessor is now stamped (we just stamped the current topic)
					return NextTopicInfo(topic_id=topic.topic_id, state="open")

		return None

	async def submit_attempt(
		self,
		player_id: str,
		plan_id: str | None,
		season_id: str | None,
		subject_id: str,
		request: AttemptRequest,
	) -> AttemptResponse:
		"""Orchestrate a challenge attempt submission.

		1. Validate topic is open (3 unlock conditions)
		2. Grade attempt
		3. Update best scores
		4. Calculate XP delta
		5. Update Redis progress HASH + dirty set + attempt buffer
		6. Push FSRS interactions
		7. Compute next_topic
		8. Return AttemptResponse
		"""
		topic_id = request.topic_id
		if not subject_id:
			raise ValueError("subject_id is required")

		# Load challenge settings
		ch_settings = await self._get_challenge_settings()
		xp_per_question = ch_settings["xp_per_question"]
		pass_threshold = ch_settings["pass_threshold"]

		# Load question lookup BEFORE lock — may call Frappe and can be slow.
		question_lookup = await self._get_question_lookup(topic_id)

		# Grade attempt before lock (pure computation on request payload + lookup).
		correct_count, score_pct, passed = self._grade_attempt(
			request.questions,
			pass_threshold,
			question_lookup,
		)

		# Build per-question server-graded correctness for attempt buffer and FSRS.
		graded_correctness: dict[str, bool] = {}
		graded_details = []
		for q in request.questions:
			server_correct = False
			if question_lookup is not None:
				answer_key = question_lookup.get(q.item_id)
				if answer_key and answer_key.get("correct_choice") is not None:
					server_correct = q.chosen_answer == answer_key["correct_choice"]
			else:
				server_correct = q.correct  # degraded fallback
			graded_correctness[q.item_id] = server_correct
			graded_details.append(
				{
					"item_id": q.item_id,
					"correct": server_correct,
					"time_spent": q.time_spent,
					"chosen_answer": q.chosen_answer,
				}
			)

		attempt_lock = self.redis.lock(
			ch_attempt_lock_key(player_id, subject_id, topic_id),
			timeout=CH_ATTEMPT_LOCK_TTL,
			blocking_timeout=2,
			sleep=0.05,
		)
		acquired = await attempt_lock.acquire()
		if not acquired:
			raise ValueError("ATTEMPT_IN_PROGRESS")

		try:
			# Ensure hydrated and load progress under lock (prevents double-award races).
			progress_map = await self._get_progress_map(player_id, subject_id)
			tp = progress_map.get(topic_id, {})

			prev_best_correct = int(tp.get("best_correct", 0))
			prev_best_score_pct = float(tp.get("best_score_pct", 0))
			prev_best_passing_pct = float(tp.get("best_passing_pct", 0))
			prev_total_xp = int(tp.get("total_xp", 0))
			prev_attempt_count = int(tp.get("attempt_count", 0))
			was_stamped = bool(tp.get("stamped", 0))

			# Validate topic existence/open state.
			is_open, topic_found = await self._validate_topic_open(player_id, plan_id, subject_id, topic_id)
			if not topic_found:
				raise ValueError("TOPIC_NOT_FOUND")
			# Retries are allowed after stamp, so lock gate only applies to unstamped topics.
			if not was_stamped and not is_open:
				raise ValueError("TOPIC_LOCKED")

			# T016: Update best scores
			best_correct, best_score_pct, best_passing_pct, is_new_best = self._update_best_scores(
				correct_count,
				score_pct,
				passed,
				prev_best_correct,
				prev_best_score_pct,
				prev_best_passing_pct,
			)

			# T017: Calculate XP delta
			xp_delta = self._calculate_xp_delta(correct_count, prev_best_correct, xp_per_question)

			# Determine stamp status
			stamped = was_stamped or passed
			attempt_number = prev_attempt_count + 1
			total_topic_xp = prev_total_xp + xp_delta

			# Build updated progress data
			progress_data = json.dumps(
				{
					"stamped": 1 if stamped else 0,
					"best_correct": best_correct,
					"best_score_pct": best_score_pct,
					"best_passing_pct": best_passing_pct,
					"total_xp": total_topic_xp,
					"attempt_count": attempt_number,
				}
			)

			# Build attempt payload for buffer
			now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
			attempt_payload = json.dumps(
				{
					"player": player_id,
					"topic": topic_id,
					"subject": subject_id,
					"season": season_id,
					"attempt_number": attempt_number,
					"total_questions": len(request.questions),
					"correct_count": correct_count,
					"score_pct": score_pct,
					"passed": passed,
					"time_spent": request.time_spent,
					"xp_earned": xp_delta,
					"submitted_at": now_str,
					"details": graded_details,
				}
			)

			# Atomic Redis pipeline: progress update + dirty set + attempt buffer + FSRS
			key = ch_progress_key(player_id, subject_id)
			pipe = self.redis.pipeline()
			pipe.hset(key, topic_id, progress_data)
			pipe.expire(key, CH_PROGRESS_KEY_TTL)
			# Include season in dirty member so sync uses the season active at earn-time,
			# not the player's current season (which may have changed before flush).
			pipe.sadd(dirty_ch_progress_key(), f"{player_id}:{subject_id}:{season_id}")
			pipe.rpush(ch_attempt_buffer_key(), attempt_payload)

			# T018: Push FSRS interactions (uses server-graded correctness, not client flags)
			await self._push_fsrs_interactions(
				player_id, request.questions, question_lookup, graded_correctness, pipe
			)

			# T023: Update challenge leaderboard ZSETs (only when XP improved)
			if xp_delta > 0 and season_id and plan_id:
				lb_key = ch_leaderboard_key(season_id, plan_id)
				lb_subj_key = ch_leaderboard_subject_key(season_id, plan_id, subject_id)
				pipe.zincrby(lb_key, xp_delta, player_id)
				pipe.zincrby(lb_subj_key, xp_delta, player_id)

			await pipe.execute()
		finally:
			try:
				await attempt_lock.release()
			except Exception as e:
				logger.warning("ch_attempt_lock_release_failed", error=str(e), player_id=player_id, topic_id=topic_id)

		logger.info(
			"ch_attempt_submitted",
			player_id=player_id,
			topic_id=topic_id,
			score_pct=score_pct,
			passed=passed,
			xp_delta=xp_delta,
			attempt_number=attempt_number,
			stamped=stamped,
			is_new_best=is_new_best,
		)

		if stamped and not was_stamped:
			logger.info(
				"ch_topic_stamped",
				player_id=player_id,
				topic_id=topic_id,
				subject_id=subject_id,
				score_pct=score_pct,
			)

		# Compute next topic (if this stamp unlocked it)
		newly_stamped = stamped and not was_stamped
		next_topic = await self._evaluate_next_topic(player_id, plan_id, subject_id, topic_id, newly_stamped)

		return AttemptResponse(
			attempt_number=attempt_number,
			score_pct=score_pct,
			passed=passed,
			stamped=stamped,
			xp_earned=xp_delta,
			total_topic_xp=total_topic_xp,
			best_score_pct=best_score_pct,
			best_passing_pct=best_passing_pct if best_passing_pct > 0 else None,
			is_new_best=is_new_best,
			next_topic=next_topic,
		)

	async def _validate_topic_open(
		self,
		player_id: str,
		plan_id: str | None,
		subject_id: str,
		topic_id: str,
	) -> tuple[bool, bool]:
		"""Check the 3 unlock conditions for a topic.

		1. Content access (AccessService)
		2. Normal path complete (StatsService)
		3. Previous topic stamped in Challenge Hub

		Returns:
			(is_open, topic_found)
		"""
		if not self.hierarchy_svc:
			return False, False

		hierarchy = await self.hierarchy_svc.get_hierarchy(subject_id)
		if not hierarchy:
			return False, False

		# Resolve topic location and predecessor gate first, so callers can
		# distinguish "not found" from "found but locked".
		progress_map = await self._get_progress_map(player_id, subject_id)
		topic_found = False
		predecessor_stamped = False
		for track in hierarchy.tracks:
			for unit in track.units:
				prev_stamped = True  # First topic has no predecessor constraint
				for topic in unit.topics:
					if topic.mcq_count == 0:
						# Empty topic auto-stamps if predecessor is stamped.
						if prev_stamped:
							prev_stamped = True
						continue
					if topic.topic_id == topic_id:
						topic_found = True
						predecessor_stamped = prev_stamped
						break
					tp = progress_map.get(topic.topic_id, {})
					prev_stamped = bool(tp.get("stamped", 0))
				if topic_found:
					break
			if topic_found:
				break
		if not topic_found:
			return False, False

		# Check access — free topics/units bypass the subject-level grant requirement
		if self.access_svc:
			has_access = await self.access_svc.check_access_with_plan(player_id, f"SUB-{subject_id}", plan_id)
			if not has_access:
				free_units_set = set(hierarchy.free_units)
				free_topics_set = set(hierarchy.free_topics)
				# Find which unit contains this topic to check unit-level free
				topic_unit_id = None
				for t in hierarchy.tracks:
					for u in t.units:
						if any(tp.topic_id == topic_id for tp in u.topics):
							topic_unit_id = u.unit_id
							break
					if topic_unit_id:
						break
				topic_is_free = topic_id in free_topics_set or (topic_unit_id and topic_unit_id in free_units_set)
				if not topic_is_free:
					return False, True

		# Check normal path complete
		stats = None
		if self.stats_svc:
			stats = await self.stats_svc.get_stats(player_id, subject_id, hierarchy.version)

		if stats:
			topic_completed = int(stats.get(f"{topic_id}:completed", 0))
			topic_total = int(stats.get(f"{topic_id}:total", 0))
			if topic_total == 0 or topic_completed < topic_total:
				return False, True
		else:
			return False, True

		return predecessor_stamped, True

	# =========================================================================
	# Phase 5: Challenge Leaderboard (T024, T025)
	# =========================================================================

	async def get_leaderboard(
		self,
		season_id: str,
		plan_id: str,
		player_id: str,
		subject_id: str | None = None,
		limit: int = 20,
		offset: int = 0,
	) -> dict:
		"""Get top Challenge XP players from the leaderboard.

		Uses ZRANGE desc for O(log N + M). Dense ranking: tied players share rank.
		Returns dict with entries (list of {rank, player_id, xp}), total_players.
		"""
		if subject_id:
			key = ch_leaderboard_subject_key(season_id, plan_id, subject_id)
		else:
			key = ch_leaderboard_key(season_id, plan_id)

		# Fetch only the requested page window.
		pipe = self.redis.pipeline()
		pipe.zrange(key, offset, offset + limit - 1, desc=True, withscores=True)
		pipe.zcard(key)
		results, total_players = await pipe.execute()

		if not results:
			return {
				"entries": [],
				"total_players": total_players,
			}

		# Compute dense rank for first row without scanning all previous members.
		first_xp = int(results[0][1])
		distinct_above, _min_above = await self._count_distinct_tiers_above(key, first_xp)
		current_rank = distinct_above + 1
		prev_xp = None
		entries = []

		for member, score in results:
			xp = int(score)
			if prev_xp is not None and xp != prev_xp:
				current_rank += 1
			entries.append({"rank": current_rank, "player_id": member, "xp": xp})
			prev_xp = xp

		return {
			"entries": entries,
			"total_players": total_players,
		}

	async def get_my_rank(
		self,
		season_id: str,
		plan_id: str,
		player_id: str,
		subject_id: str | None = None,
		neighbor_count: int = 2,
	) -> dict:
		"""Get player's own Challenge XP rank with neighbors.

		Dense ranking: tied players share rank. Returns dict with
		rank, xp, xp_to_next, neighbors, total_players.
		Unranked case: rank=None, xp=0, neighbors=[].
		"""
		if subject_id:
			key = ch_leaderboard_subject_key(season_id, plan_id, subject_id)
		else:
			key = ch_leaderboard_key(season_id, plan_id)

		# Stage 1: position, total, score in one pipeline
		pipe = self.redis.pipeline()
		pipe.zrevrank(key, player_id)
		pipe.zcard(key)
		pipe.zscore(key, player_id)
		position, total, score = await pipe.execute()

		# Handle unranked player
		if position is None:
			return {
				"rank": None,
				"xp": 0,
				"xp_to_next": None,
				"neighbors": [],
				"total_players": total,
			}

		xp = int(score) if score is not None else 0

		# Stage 2: neighbors + dense rank computation
		start = max(0, position - neighbor_count)
		stop = position + neighbor_count

		neighbors_raw = await self.redis.zrange(key, start, stop, desc=True, withscores=True)

		# Dense rank in O(tiers) inside Redis, no unbounded member transfer.
		distinct_above, min_above = await self._count_distinct_tiers_above(key, xp)
		my_rank = distinct_above + 1

		# xp_to_next: XP of nearest higher score
		xp_to_next = None
		if min_above >= 0:
			xp_to_next = min_above - xp

		# Compute neighbor dense ranks relative to my_rank
		window_tiers = {int(s) for _, s in neighbors_raw}

		neighbors = []
		for neighbor_id, neighbor_score in neighbors_raw:
			neighbor_xp = int(neighbor_score)

			if neighbor_xp > xp:
				tiers_between = len({t for t in window_tiers if xp < t <= neighbor_xp})
				neighbor_rank = my_rank - tiers_between
			elif neighbor_xp < xp:
				tiers_between = len({t for t in window_tiers if neighbor_xp < t <= xp})
				neighbor_rank = my_rank + tiers_between
			else:
				neighbor_rank = my_rank

			neighbors.append({
				"rank": neighbor_rank,
				"player_id": neighbor_id,
				"xp": neighbor_xp,
				"is_me": neighbor_id == player_id,
			})

		return {
			"rank": my_rank,
			"xp": xp,
			"xp_to_next": xp_to_next,
			"neighbors": neighbors,
			"total_players": total,
		}
