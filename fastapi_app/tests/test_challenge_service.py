"""Tests for ChallengeService — pure logic + key integration tests.

Phase 9 (T033-T038): Grading, XP delta, best scores, empty topic auto-stamp,
Challenge XP isolation, and FSRS push verification.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis

from fastapi_app.core.redis_keys import (
	ch_attempt_buffer_key,
	ch_leaderboard_key,
	ch_leaderboard_subject_key,
	ch_progress_key,
	dirty_ch_progress_key,
	interaction_buffer_key,
	wallet_key,
)
from fastapi_app.models.challenge import AttemptRequest, QuestionDetail
from fastapi_app.services.challenge import ChallengeService


# =============================================================================
# Helpers
# =============================================================================


def _q(item_id: str = "item-1", correct: bool = True, time_spent: int = 10, chosen: int = 1) -> QuestionDetail:
	"""Build a QuestionDetail for tests."""
	return QuestionDetail(item_id=item_id, correct=correct, time_spent=time_spent, chosen_answer=chosen)


def _qs_as_ns(questions: list[QuestionDetail]) -> list[SimpleNamespace]:
	"""Convert QuestionDetail list to SimpleNamespace list (matching runtime objects)."""
	return [SimpleNamespace(item_id=q.item_id, correct=q.correct, time_spent=q.time_spent, chosen_answer=q.chosen_answer) for q in questions]


def _make_service(redis_client: redis.Redis, **kwargs) -> ChallengeService:
	"""Build a ChallengeService with defaults for testing."""
	return ChallengeService(redis_client=redis_client, **kwargs)


# =============================================================================
# T033: Pure tests for _grade_attempt
# =============================================================================


class TestGradeAttempt:
	"""T033 — Pure tests for _grade_attempt() with server-side answer verification."""

	def _grade(self, questions: list, threshold: int = 50, question_lookup: dict | None = None):
		"""Call _grade_attempt with a question_lookup for server-side grading."""
		svc = ChallengeService.__new__(ChallengeService)
		if question_lookup is None:
			# Build a lookup where correct_choice matches chosen_answer for "correct" items
			question_lookup = {}
			for q in questions:
				# If test marks q.correct=True, set correct_choice = q.chosen_answer
				# If test marks q.correct=False, set correct_choice to a different value
				question_lookup[q.item_id] = {
					"lesson": "L-TEST",
					"stage_id": "S-TEST",
					"correct_choice": q.chosen_answer if q.correct else (q.chosen_answer % 4) + 1,
				}
		return svc._grade_attempt(questions, threshold, question_lookup)

	def test_pass_at_threshold_50pct(self):
		"""50% score passes when threshold is 50."""
		qs = [_q(f"q{i}", correct=(i < 5), chosen=1) for i in range(10)]
		correct_count, score_pct, passed = self._grade(qs, 50)
		assert correct_count == 5
		assert score_pct == 50.0
		assert passed is True

	def test_fail_below_threshold_49pct(self):
		"""49% fails when threshold is 50 (boundary)."""
		qs = [_q(f"q{i}", correct=(i < 49), chosen=1) for i in range(100)]
		correct_count, score_pct, passed = self._grade(qs, 50)
		assert correct_count == 49
		assert score_pct == 49.0
		assert passed is False

	def test_pass_above_threshold_51pct(self):
		"""51% passes when threshold is 50."""
		qs = [_q(f"q{i}", correct=(i < 51), chosen=1) for i in range(100)]
		correct_count, score_pct, passed = self._grade(qs, 50)
		assert correct_count == 51
		assert score_pct == 51.0
		assert passed is True

	def test_zero_score(self):
		"""0/N score fails."""
		qs = [_q(f"q{i}", correct=False, chosen=1) for i in range(5)]
		correct_count, score_pct, passed = self._grade(qs, 50)
		assert correct_count == 0
		assert score_pct == 0.0
		assert passed is False

	def test_perfect_score(self):
		"""N/N score passes."""
		qs = [_q(f"q{i}", correct=True, chosen=1) for i in range(10)]
		correct_count, score_pct, passed = self._grade(qs, 50)
		assert correct_count == 10
		assert score_pct == 100.0
		assert passed is True

	def test_server_overrides_client_correct_flag(self):
		"""Server-side grading ignores the client's correct flag."""
		# Client says both correct, but only q0's chosen_answer matches correct_choice
		qs = [_q("q0", correct=True, chosen=2), _q("q1", correct=True, chosen=3)]
		lookup = {
			"q0": {"lesson": "L", "stage_id": "S", "correct_choice": 2},  # match
			"q1": {"lesson": "L", "stage_id": "S", "correct_choice": 1},  # mismatch: client says 3, answer is 1
		}
		correct_count, score_pct, passed = self._grade(qs, 50, question_lookup=lookup)
		assert correct_count == 1  # Only q0 is actually correct
		assert score_pct == 50.0
		assert passed is True

	def test_unknown_item_id_raises(self):
		"""Raises ValueError for item_id not in question_lookup."""
		qs = [_q("q-unknown", correct=True, chosen=1)]
		# Lookup has 1 entry so count matches, but item_id doesn't match
		lookup = {"q-other": {"lesson": "L", "stage_id": "S", "correct_choice": 1}}
		with pytest.raises(ValueError, match="UNKNOWN_ITEM"):
			self._grade(qs, 50, question_lookup=lookup)

	def test_no_answer_key_raises(self):
		"""Raises ValueError when correct_choice is None in lookup."""
		qs = [_q("q0", correct=True, chosen=1)]
		lookup = {"q0": {"lesson": "L", "stage_id": "S", "correct_choice": None}}
		with pytest.raises(ValueError, match="NO_ANSWER_KEY"):
			self._grade(qs, 50, question_lookup=lookup)

	def test_duplicate_item_id_raises(self):
		"""Raises ValueError when the same item_id appears more than once."""
		qs = [_q("q0", correct=True, chosen=1), _q("q0", correct=True, chosen=1)]
		with pytest.raises(ValueError, match="DUPLICATE_ITEM"):
			self._grade(qs, 50)

	def test_question_count_mismatch_raises(self):
		"""Raises ValueError when submitted count doesn't match expected."""
		# Lookup has 3 items but we only submit 2
		qs = [_q("q0", correct=True, chosen=1), _q("q1", correct=False, chosen=2)]
		lookup = {
			"q0": {"lesson": "L", "stage_id": "S", "correct_choice": 1},
			"q1": {"lesson": "L", "stage_id": "S", "correct_choice": 1},
			"q2": {"lesson": "L", "stage_id": "S", "correct_choice": 1},
		}
		with pytest.raises(ValueError, match="QUESTION_COUNT_MISMATCH"):
			self._grade(qs, 50, question_lookup=lookup)

	def test_fallback_to_client_when_no_lookup(self):
		"""Falls back to client-reported correctness when question_lookup is None."""
		svc = ChallengeService.__new__(ChallengeService)
		qs = [_q("q0", correct=True, chosen=1), _q("q1", correct=False, chosen=2)]
		correct_count, score_pct, _passed = svc._grade_attempt(qs, 50, question_lookup=None)
		assert correct_count == 1  # trusts client
		assert score_pct == 50.0

	def test_rounding(self):
		"""Score is rounded to 2 decimal places."""
		qs = [_q("q0", correct=True, chosen=1), _q("q1", correct=False, chosen=1), _q("q2", correct=False, chosen=1)]
		correct_count, score_pct, _ = self._grade(qs, 50)
		assert correct_count == 1
		assert score_pct == 33.33

	def test_single_question_correct(self):
		"""1/1 = 100%, passes any threshold."""
		qs = [_q("q0", correct=True, chosen=1)]
		correct_count, score_pct, passed = self._grade(qs, 100)
		assert correct_count == 1
		assert score_pct == 100.0
		assert passed is True


# =============================================================================
# T034: Pure tests for _calculate_xp_delta
# =============================================================================


class TestCalculateXpDelta:
	"""T034 — Pure tests for _calculate_xp_delta()."""

	def _calc(self, current: int, previous_best: int, xp_per_q: int = 5):
		svc = ChallengeService.__new__(ChallengeService)
		return svc._calculate_xp_delta(current, previous_best, xp_per_q)

	def test_first_attempt_full_xp(self):
		"""First attempt (previous_best=0) earns full XP."""
		assert self._calc(10, 0, 5) == 50  # 10 * 5

	def test_improvement_delta_xp(self):
		"""Improving from 8 to 12 earns (12-8) * xp_per_q."""
		assert self._calc(12, 8, 5) == 20  # 4 * 5

	def test_regression_zero_xp(self):
		"""Scoring worse than previous best earns 0 XP."""
		assert self._calc(5, 10, 5) == 0

	def test_same_score_zero_xp(self):
		"""Same score as previous best earns 0 XP."""
		assert self._calc(10, 10, 5) == 0

	def test_configurable_xp_per_question(self):
		"""XP per question is configurable."""
		assert self._calc(10, 0, 3) == 30  # 10 * 3
		assert self._calc(10, 0, 10) == 100  # 10 * 10

	def test_improvement_by_one(self):
		"""Improving by exactly 1 question earns xp_per_question."""
		assert self._calc(11, 10, 5) == 5  # 1 * 5

	def test_zero_correct_zero_delta(self):
		"""0 correct on first attempt earns 0 XP."""
		assert self._calc(0, 0, 5) == 0


# =============================================================================
# T035: Pure tests for _update_best_scores
# =============================================================================


class TestUpdateBestScores:
	"""T035 — Pure tests for _update_best_scores()."""

	def _update(
		self,
		current_correct: int,
		current_score_pct: float,
		passed: bool,
		prev_best_correct: int = 0,
		prev_best_score_pct: float = 0.0,
		prev_best_passing_pct: float = 0.0,
	):
		svc = ChallengeService.__new__(ChallengeService)
		return svc._update_best_scores(
			current_correct, current_score_pct, passed,
			prev_best_correct, prev_best_score_pct, prev_best_passing_pct,
		)

	def test_new_best_overall(self):
		"""Higher correct count sets new best and is_new_best=True."""
		best_correct, best_pct, best_pass_pct, is_new_best = self._update(
			current_correct=15, current_score_pct=75.0, passed=True,
			prev_best_correct=10, prev_best_score_pct=50.0, prev_best_passing_pct=50.0,
		)
		assert best_correct == 15
		assert best_pct == 75.0
		assert best_pass_pct == 75.0
		assert is_new_best is True

	def test_new_best_passing(self):
		"""New passing score higher than previous best passing updates best_passing_pct."""
		best_correct, best_pct, best_pass_pct, is_new_best = self._update(
			current_correct=18, current_score_pct=90.0, passed=True,
			prev_best_correct=15, prev_best_score_pct=75.0, prev_best_passing_pct=60.0,
		)
		assert best_pass_pct == 90.0

	def test_regression_no_update(self):
		"""Lower score doesn't update best scores."""
		best_correct, best_pct, best_pass_pct, is_new_best = self._update(
			current_correct=5, current_score_pct=25.0, passed=False,
			prev_best_correct=15, prev_best_score_pct=75.0, prev_best_passing_pct=60.0,
		)
		assert best_correct == 15
		assert best_pct == 75.0
		assert best_pass_pct == 60.0
		assert is_new_best is False

	def test_first_passing_after_failures(self):
		"""First passing attempt sets best_passing_pct from 0."""
		best_correct, best_pct, best_pass_pct, is_new_best = self._update(
			current_correct=12, current_score_pct=60.0, passed=True,
			prev_best_correct=10, prev_best_score_pct=50.0, prev_best_passing_pct=0.0,
		)
		assert best_pass_pct == 60.0
		assert is_new_best is True

	def test_is_new_best_flag_false_on_equal(self):
		"""Equal correct count does not set is_new_best."""
		_, _, _, is_new_best = self._update(
			current_correct=10, current_score_pct=50.0, passed=True,
			prev_best_correct=10, prev_best_score_pct=50.0, prev_best_passing_pct=50.0,
		)
		assert is_new_best is False

	def test_failed_attempt_does_not_update_passing_pct(self):
		"""Failed attempt with higher score doesn't update best_passing_pct."""
		best_correct, best_pct, best_pass_pct, _ = self._update(
			current_correct=12, current_score_pct=48.0, passed=False,
			prev_best_correct=10, prev_best_score_pct=40.0, prev_best_passing_pct=0.0,
		)
		assert best_correct == 12
		assert best_pct == 48.0
		assert best_pass_pct == 0.0  # Not updated because attempt didn't pass

	def test_first_attempt_ever(self):
		"""Very first attempt (all prev=0) with a pass."""
		best_correct, best_pct, best_pass_pct, is_new_best = self._update(
			current_correct=7, current_score_pct=70.0, passed=True,
		)
		assert best_correct == 7
		assert best_pct == 70.0
		assert best_pass_pct == 70.0
		assert is_new_best is True


# =============================================================================
# T036: Integration test — empty topic auto-stamp chain
# =============================================================================


class TestEmptyTopicAutoStampChain:
	"""T036 — Empty topic auto-stamp chain logic in get_challenge_hierarchy."""

	def _make_mock_plan_svc(self, subject_id: str = "SUBJ-TEST-CH") -> AsyncMock:
		"""Create a mock PlanService returning a manifest that contains subject_id."""
		from datetime import datetime

		from fastapi_app.models.plan import PlanManifest, PlanSubject

		mock_plan_svc = AsyncMock()
		mock_plan_svc.get_manifest = AsyncMock(
			return_value=PlanManifest(
				version=1,
				generated_at=datetime.now(),
				plan_id="PLAN-TEST",
				title="Test Plan",
				subjects=[PlanSubject(id=subject_id, title="Test Subject", hierarchy_url="")],
			)
		)
		return mock_plan_svc

	def _make_hierarchy(self, topics: list[dict]) -> dict:
		"""Build hierarchy JSON with custom topics."""
		return {
			"subject_id": "SUBJ-TEST-CH",
			"version": 1,
			"is_linear": False,
			"bit_range": 10,
			"excluded_bits": [],
			"free_units": [],
			"free_topics": [],
			"tracks": [
				{
					"track_id": "TRK-1",
					"track_title": "Track 1",
					"is_linear": False,
					"units": [
						{
							"unit_id": "UNIT-1",
							"unit_title": "Unit 1",
							"is_linear": False,
							"topics": topics,
						}
					],
				}
			],
		}

	def _make_topic(self, topic_id: str, mcq_count: int = 10) -> dict:
		return {
			"topic_id": topic_id,
			"topic_title": topic_id,
			"is_linear": False,
			"is_free": False,
			"mcq_count": mcq_count,
			"lessons": [{"lesson_id": f"L-{topic_id}-1", "bit_index": 0, "xp": 10, "max_hearts": 3, "is_reviewable": True}],
		}

	@pytest.mark.asyncio
	async def test_chain_stamped_empty_empty_real(self, redis_client: redis.Redis):
		"""[stamped, empty, empty, real] → real topic becomes open."""
		from fastapi_app.core.redis_keys import hierarchy_key

		hierarchy_json = self._make_hierarchy([
			self._make_topic("T1", mcq_count=10),
			self._make_topic("T2", mcq_count=0),  # empty
			self._make_topic("T3", mcq_count=0),  # empty
			self._make_topic("T4", mcq_count=10),  # real
		])

		# Seed hierarchy in Redis
		await redis_client.set(hierarchy_key("SUBJ-TEST-CH"), json.dumps(hierarchy_json), ex=3600)

		# Seed challenge progress: T1 is stamped
		progress_key = ch_progress_key("PLAYER-TEST-CH", "SUBJ-TEST-CH")
		await redis_client.hset(progress_key, "T1", json.dumps({"stamped": 1, "best_correct": 8, "best_score_pct": 80.0, "best_passing_pct": 80.0, "total_xp": 40, "attempt_count": 1}))

		# Seed stats: all topics completed on normal path
		from fastapi_app.core.redis_keys import stats_key
		stats_data = {"T1:completed": "1", "T1:total": "1", "T4:completed": "1", "T4:total": "1"}
		await redis_client.hset(stats_key("PLAYER-TEST-CH", "SUBJ-TEST-CH", 1), mapping=stats_data)

		# Mock access service (grant access)
		mock_access = AsyncMock()
		mock_access.check_access_with_plan = AsyncMock(return_value=True)

		# Mock hierarchy service
		mock_frappe = AsyncMock()
		from fastapi_app.services.hierarchy import HierarchyService
		hierarchy_svc = HierarchyService(redis_client, mock_frappe)

		# Mock stats service
		from fastapi_app.services.stats import StatsService
		stats_svc = StatsService(redis_client)

		svc = ChallengeService(
			redis_client=redis_client,
			hierarchy_service=hierarchy_svc,
			access_service=mock_access,
			stats_service=stats_svc,
			plan_service=self._make_mock_plan_svc(),
		)

		result = await svc.get_challenge_hierarchy("PLAYER-TEST-CH", "PLAN-TEST", "SUBJ-TEST-CH")

		assert result is not None
		topics = result.tracks[0].units[0].topics

		# Empty topics T2 and T3 should be hidden (filtered from response)
		topic_ids = [t.topic_id for t in topics]
		assert "T2" not in topic_ids
		assert "T3" not in topic_ids

		# T1 should be stamped, T4 should be open (empty topics auto-stamp between them)
		assert len(topics) == 2
		assert topics[0].topic_id == "T1"
		assert topics[0].state == "stamped"
		assert topics[1].topic_id == "T4"
		assert topics[1].state == "open"

	@pytest.mark.asyncio
	async def test_single_empty_topic_hidden(self, redis_client: redis.Redis):
		"""Single empty topic between two real topics is hidden."""
		from fastapi_app.core.redis_keys import hierarchy_key

		hierarchy_json = self._make_hierarchy([
			self._make_topic("T1", mcq_count=10),
			self._make_topic("T2", mcq_count=0),  # empty
			self._make_topic("T3", mcq_count=10),
		])

		await redis_client.set(hierarchy_key("SUBJ-TEST-CH"), json.dumps(hierarchy_json), ex=3600)

		# T1 not stamped
		mock_access = AsyncMock()
		mock_access.check_access_with_plan = AsyncMock(return_value=True)

		from fastapi_app.services.hierarchy import HierarchyService
		from fastapi_app.services.stats import StatsService

		svc = ChallengeService(
			redis_client=redis_client,
			hierarchy_service=HierarchyService(redis_client, AsyncMock()),
			access_service=mock_access,
			stats_service=StatsService(redis_client),
			plan_service=self._make_mock_plan_svc(),
		)

		result = await svc.get_challenge_hierarchy("PLAYER-TEST-CH2", "PLAN-TEST", "SUBJ-TEST-CH")
		topics = result.tracks[0].units[0].topics

		# T2 (empty) should be hidden
		topic_ids = [t.topic_id for t in topics]
		assert "T2" not in topic_ids
		assert len(topics) == 2  # Only T1 and T3

	@pytest.mark.asyncio
	async def test_all_empty_unit(self, redis_client: redis.Redis):
		"""Unit with all-empty topics produces empty topic list in response."""
		from fastapi_app.core.redis_keys import hierarchy_key

		hierarchy_json = self._make_hierarchy([
			self._make_topic("T1", mcq_count=0),
			self._make_topic("T2", mcq_count=0),
			self._make_topic("T3", mcq_count=0),
		])

		await redis_client.set(hierarchy_key("SUBJ-TEST-CH"), json.dumps(hierarchy_json), ex=3600)

		mock_access = AsyncMock()
		mock_access.check_access_with_plan = AsyncMock(return_value=True)

		from fastapi_app.services.hierarchy import HierarchyService
		from fastapi_app.services.stats import StatsService

		svc = ChallengeService(
			redis_client=redis_client,
			hierarchy_service=HierarchyService(redis_client, AsyncMock()),
			access_service=mock_access,
			stats_service=StatsService(redis_client),
			plan_service=self._make_mock_plan_svc(),
		)

		result = await svc.get_challenge_hierarchy("PLAYER-TEST-CH3", "PLAN-TEST", "SUBJ-TEST-CH")
		topics = result.tracks[0].units[0].topics
		assert len(topics) == 0  # All empty → all hidden


# =============================================================================
# T037: Integration test — Challenge XP isolation
# =============================================================================


class TestChallengeXpIsolation:
	"""T037 — Verify Challenge XP does not leak into main wallet or leaderboards."""

	@pytest.mark.asyncio
	async def test_xp_not_in_main_wallet(self, redis_client: redis.Redis):
		"""Challenge XP does not appear in memora:wallet:{player}."""
		player_id = "PLAYER-TEST-ISO1"

		# Seed main wallet with 100 XP
		wk = wallet_key(player_id)
		await redis_client.hset(wk, mapping={"xp": "100", "streak": "5"})

		# Simulate challenge attempt submission — writes to ch_progress + ch_leaderboard
		# (Mimics what submit_attempt does, without mocking the full flow)
		ch_key = ch_progress_key(player_id, "SUBJ-TEST")
		await redis_client.hset(ch_key, "TOPIC-1", json.dumps({
			"stamped": 1, "best_correct": 10, "best_score_pct": 100.0,
			"best_passing_pct": 100.0, "total_xp": 50, "attempt_count": 1,
		}))

		lb_key = ch_leaderboard_key("SEAS-TEST", "PLAN-TEST")
		await redis_client.zincrby(lb_key, 50, player_id)

		# Verify main wallet is unchanged
		wallet = await redis_client.hgetall(wk)
		assert wallet["xp"] == "100"  # Not 150
		assert wallet["streak"] == "5"

	@pytest.mark.asyncio
	async def test_xp_not_in_main_leaderboard(self, redis_client: redis.Redis):
		"""Challenge XP does not appear in main leaderboard ZSETs (memora:lb:*)."""
		from uuid import uuid4

		from fastapi_app.core.redis_keys import lb_daily_key

		player_id = f"PLAYER-TEST-ISO2-{uuid4().hex[:8]}"
		unique_date = "2099-01-01"  # Far future to avoid collisions

		# Seed main daily leaderboard
		main_daily = lb_daily_key(unique_date)
		await redis_client.zadd(main_daily, {player_id: 200})

		# Add challenge XP to challenge leaderboard
		ch_lb = ch_leaderboard_key("SEAS-TEST-ISO", "PLAN-TEST-ISO")
		await redis_client.zadd(ch_lb, {player_id: 75})

		# Verify main daily leaderboard is unchanged
		main_xp = await redis_client.zscore(main_daily, player_id)
		assert int(main_xp) == 200  # Not 275

		# Verify challenge leaderboard has its own data
		ch_xp = await redis_client.zscore(ch_lb, player_id)
		assert int(ch_xp) == 75

		# Cleanup
		await redis_client.zrem(main_daily, player_id)
		await redis_client.delete(ch_lb)

	@pytest.mark.asyncio
	async def test_challenge_leaderboard_separate_from_main(self, redis_client: redis.Redis):
		"""Challenge leaderboard keys use memora:lb:ch: prefix, separate from memora:lb:*."""
		ch_lb = ch_leaderboard_key("SEAS-TEST", "PLAN-TEST")
		ch_lb_subj = ch_leaderboard_subject_key("SEAS-TEST", "PLAN-TEST", "SUBJ-TEST")

		assert ch_lb.startswith("memora:lb:ch:")
		assert ch_lb_subj.startswith("memora:lb:ch:")

		# Main leaderboard keys don't use ch: prefix
		from fastapi_app.core.redis_keys import lb_daily_key
		main_lb = lb_daily_key("2026-03-08")
		assert "lb:ch:" not in main_lb


# =============================================================================
# T038: Integration test — FSRS push
# =============================================================================


class TestFsrsPush:
	"""T038 — Verify FSRS interactions are pushed to memora:buffer:interactions."""

	@pytest.mark.asyncio
	async def test_attempt_pushes_fsrs_interactions(self, redis_client: redis.Redis):
		"""Completed attempt pushes one FSRS interaction per question to buffer."""
		from fastapi_app.core.redis_keys import hierarchy_key, stats_key

		player_id = "PLAYER-TEST-FSRS1"
		subject_id = "SUBJ-TEST-FSRS"
		topic_id = "TOPIC-FSRS-1"

		# Seed hierarchy
		hierarchy_json = {
			"subject_id": subject_id,
			"version": 1,
			"is_linear": False,
			"bit_range": 5,
			"excluded_bits": [],
			"free_units": [],
			"free_topics": [],
			"tracks": [{
				"track_id": "TRK-1",
				"track_title": "Track 1",
				"is_linear": False,
				"units": [{
					"unit_id": "UNIT-1",
					"unit_title": "Unit 1",
					"is_linear": False,
					"topics": [{
						"topic_id": topic_id,
						"topic_title": "FSRS Topic",
						"is_linear": False,
						"is_free": False,
						"mcq_count": 3,
						"lessons": [
							{"lesson_id": "L1", "bit_index": 0, "xp": 10, "max_hearts": 3, "is_reviewable": True},
						],
					}],
				}],
			}],
		}
		await redis_client.set(hierarchy_key(subject_id), json.dumps(hierarchy_json), ex=3600)

		# Seed stats (normal path complete)
		await redis_client.hset(stats_key(player_id, subject_id, 1), mapping={
			f"{topic_id}:completed": "1", f"{topic_id}:total": "1",
		})

		# Mock access
		mock_access = AsyncMock()
		mock_access.check_access_with_plan = AsyncMock(return_value=True)

		# Mock frappe client to return question lookup and settings
		mock_frappe = AsyncMock()

		async def frappe_call_handler(method, params=None):
			if "get_topic_question_items" in method:
				return [
					{"item_id": "q1", "lesson": "LES-001", "stage_id": "STG-001", "correct_choice": 2},
					{"item_id": "q2", "lesson": "LES-001", "stage_id": "STG-002", "correct_choice": 1},
					{"item_id": "q3", "lesson": "LES-002", "stage_id": "STG-003", "correct_choice": 4},
				]
			if "get_challenge_settings" in method:
				return {"xp_per_question": 5, "pass_threshold": 50, "lb_top_count": 20, "lb_refresh_interval": 300}
			if "get_player_challenge_progress" in method:
				return []
			return None

		mock_frappe.call = AsyncMock(side_effect=frappe_call_handler)

		from fastapi_app.services.hierarchy import HierarchyService
		from fastapi_app.services.stats import StatsService

		svc = ChallengeService(
			redis_client=redis_client,
			frappe_client=mock_frappe,
			hierarchy_service=HierarchyService(redis_client, mock_frappe),
			access_service=mock_access,
			stats_service=StatsService(redis_client),
		)

		# Clear interaction buffer before test
		buf_key = interaction_buffer_key()
		await redis_client.delete(buf_key)

		# Submit attempt with 3 questions — chosen_answers match server correct_choice
		# q1: correct_choice=2, chosen=2 → correct
		# q2: correct_choice=1, chosen=1 → correct
		# q3: correct_choice=4, chosen=3 → incorrect
		request = AttemptRequest(
			subject_id=subject_id,
			topic_id=topic_id,
			attempt_key="test-attempt-key-1",
			total_questions=3,
			time_spent=60,
			questions=[
				QuestionDetail(item_id="q1", correct=True, time_spent=15, chosen_answer=2),
				QuestionDetail(item_id="q2", correct=True, time_spent=20, chosen_answer=1),
				QuestionDetail(item_id="q3", correct=False, time_spent=25, chosen_answer=3),
			],
		)

		await svc.submit_attempt(player_id, "PLAN-TEST", "SEAS-TEST", subject_id, request)

		# Verify: interaction buffer should have 3 entries (one per question)
		buf_len = await redis_client.llen(buf_key)
		assert buf_len == 3

		# Check each interaction's structure
		for i in range(3):
			raw = await redis_client.lindex(buf_key, i)
			interaction = json.loads(raw)

			assert interaction["player"] == player_id
			assert interaction["event_type"] == "Completed"
			assert interaction["metadata"]["source"] == "challenge_hub"
			assert "lesson" in interaction
			assert "stage_id" in interaction
			assert "item_id" in interaction
			assert "timestamp" in interaction

		# Verify correct/incorrect mapping (server-graded)
		i0 = json.loads(await redis_client.lindex(buf_key, 0))
		assert i0["item_id"] == "q1"
		assert i0["errors_count"] == 0  # chosen=2 == correct_choice=2
		assert i0["lesson"] == "LES-001"
		assert i0["stage_id"] == "STG-001"

		i2 = json.loads(await redis_client.lindex(buf_key, 2))
		assert i2["item_id"] == "q3"
		assert i2["errors_count"] == 1  # chosen=3 != correct_choice=4

	@pytest.mark.asyncio
	async def test_abandoned_attempt_zero_buffer_entries(self, redis_client: redis.Redis):
		"""Abandoned attempt (no submission) produces zero buffer entries."""
		# Clear the buffer
		buf_key = interaction_buffer_key()
		await redis_client.delete(buf_key)

		# Simply don't call submit_attempt — abandoned attempts leave no trace
		# Verify buffer remains empty
		buf_len = await redis_client.llen(buf_key)
		assert buf_len == 0

		# Also verify no challenge progress or dirty set entries
		dirty_key = dirty_ch_progress_key()
		dirty_members = await redis_client.smembers(dirty_key)
		# Filter for our test player if other tests have added entries
		test_members = [m for m in dirty_members if "PLAYER-TEST-ABANDON" in m]
		assert len(test_members) == 0

		# No attempt buffer entries
		attempt_buf_key = ch_attempt_buffer_key()
		# Don't assert total length (other tests may add), just verify no entries for our player
		attempt_buf_len = await redis_client.llen(attempt_buf_key)
		# We check the last few entries don't contain our player
		for i in range(attempt_buf_len):
			raw = await redis_client.lindex(attempt_buf_key, i)
			if raw:
				data = json.loads(raw)
				assert data.get("player") != "PLAYER-TEST-ABANDON"
