"""Unit tests for Live Challenge pure functions: grading, ranking, XP awards.

Tests the pure business logic with no DB, Redis, or network dependencies.

Note: compute_ranking and compute_xp_awards live in the Frappe module
(memora_admin.tasks.live_challenge_transitions) which can't be imported
in the pytest environment. We import them inline to test the logic
without the frappe dependency.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# Stub frappe and frappe.utils so we can import the transition module
# without the actual Frappe framework.
_frappe_mock = MagicMock()
_frappe_utils_mock = MagicMock()
sys.modules.setdefault("frappe", _frappe_mock)
sys.modules.setdefault("frappe.utils", _frappe_utils_mock)
sys.modules.setdefault("frappe.model", MagicMock())
sys.modules.setdefault("frappe.model.document", MagicMock())

from fastapi_app.services.live_challenge import grade_answers
from memora_admin.tasks.live_challenge_transitions import compute_ranking, compute_xp_awards

# =============================================================================
# grade_answers
# =============================================================================


class TestScoreCalculation:
	"""Verify score = (correct/total) x 100."""

	def test_all_correct(self):
		questions = [
			{"idx": 0, "correct_answer": "A"},
			{"idx": 1, "correct_answer": "B"},
			{"idx": 2, "correct_answer": "C"},
		]
		answers = [
			{"question_idx": 0, "selected": "A"},
			{"question_idx": 1, "selected": "B"},
			{"question_idx": 2, "selected": "C"},
		]
		result = grade_answers(questions, answers)
		assert result["score"] == 100.0
		assert result["correct_count"] == 3
		assert result["total_questions"] == 3

	def test_none_correct(self):
		questions = [
			{"idx": 0, "correct_answer": "A"},
			{"idx": 1, "correct_answer": "B"},
		]
		answers = [
			{"question_idx": 0, "selected": "B"},
			{"question_idx": 1, "selected": "A"},
		]
		result = grade_answers(questions, answers)
		assert result["score"] == 0.0
		assert result["correct_count"] == 0

	def test_partial_correct(self):
		"""15 out of 20 correct = 75.0%."""
		questions = [{"idx": i, "correct_answer": "A"} for i in range(20)]
		answers = [{"question_idx": i, "selected": "A" if i < 15 else "B"} for i in range(20)]
		result = grade_answers(questions, answers)
		assert result["score"] == 75.0
		assert result["correct_count"] == 15
		assert result["total_questions"] == 20

	def test_single_question_correct(self):
		questions = [{"idx": 0, "correct_answer": "D"}]
		answers = [{"question_idx": 0, "selected": "D"}]
		result = grade_answers(questions, answers)
		assert result["score"] == 100.0

	def test_empty_questions_zero_score(self):
		"""Zero questions -> score 0, no crash."""
		result = grade_answers([], [])
		assert result["score"] == 0.0
		assert result["correct_count"] == 0
		assert result["total_questions"] == 0


class TestMissingAnswer:
	"""Verify missing/null answers count as incorrect."""

	def test_missing_answer_treated_as_wrong(self):
		"""If answer array doesn't include a question_idx, it's treated as wrong."""
		questions = [
			{"idx": 0, "correct_answer": "A"},
			{"idx": 1, "correct_answer": "B"},
			{"idx": 2, "correct_answer": "C"},
		]
		answers = [
			{"question_idx": 0, "selected": "A"},
			# question_idx 1 missing entirely
			{"question_idx": 2, "selected": "C"},
		]
		result = grade_answers(questions, answers)
		assert result["correct_count"] == 2
		assert result["total_questions"] == 3

	def test_null_selected_is_incorrect(self):
		questions = [
			{"idx": 0, "correct_answer": "A"},
			{"idx": 1, "correct_answer": "B"},
		]
		answers = [
			{"question_idx": 0, "selected": None},
			{"question_idx": 1, "selected": "B"},
		]
		result = grade_answers(questions, answers)
		assert result["score"] == 50.0
		assert result["correct_count"] == 1

	def test_all_null_zero_score(self):
		questions = [{"idx": i, "correct_answer": "A"} for i in range(3)]
		answers = [{"question_idx": i, "selected": None} for i in range(3)]
		result = grade_answers(questions, answers)
		assert result["score"] == 0.0
		assert result["correct_count"] == 0


# =============================================================================
# compute_ranking
# =============================================================================


class TestComputeRanking:
	"""Tests for the compute_ranking pure function."""

	def test_simple_ranking(self):
		"""Three distinct scores -> ranks 1, 2, 3."""
		participants = [
			{"name": "P1", "player": "PL-1", "score": 90.0},
			{"name": "P2", "player": "PL-2", "score": 80.0},
			{"name": "P3", "player": "PL-3", "score": 70.0},
		]
		ranked, top_20 = compute_ranking(participants, {"PL-1": "Ahmed", "PL-2": "Sara", "PL-3": "Omar"})
		assert ranked[0]["rank"] == 1
		assert ranked[0]["player"] == "PL-1"
		assert ranked[1]["rank"] == 2
		assert ranked[2]["rank"] == 3

	def test_tied_scores_share_rank(self):
		"""Standard competition ranking: tied -> share rank, next rank skips."""
		participants = [
			{"name": "P1", "player": "PL-1", "score": 100.0},
			{"name": "P2", "player": "PL-2", "score": 100.0},
			{"name": "P3", "player": "PL-3", "score": 80.0},
		]
		ranked, _ = compute_ranking(participants, {})
		assert ranked[0]["rank"] == 1
		assert ranked[1]["rank"] == 1
		assert ranked[2]["rank"] == 3  # skips 2

	def test_all_same_score(self):
		"""All participants same score -> all rank 1."""
		participants = [{"name": f"P{i}", "player": f"PL-{i}", "score": 50.0} for i in range(5)]
		ranked, _ = compute_ranking(participants, {})
		assert all(r["rank"] == 1 for r in ranked)

	def test_complex_tie_pattern(self):
		"""Multiple tie groups: 1,1,3,3,5."""
		participants = [
			{"name": "P1", "player": "PL-1", "score": 100.0},
			{"name": "P2", "player": "PL-2", "score": 100.0},
			{"name": "P3", "player": "PL-3", "score": 80.0},
			{"name": "P4", "player": "PL-4", "score": 80.0},
			{"name": "P5", "player": "PL-5", "score": 60.0},
		]
		ranked, _ = compute_ranking(participants, {})
		assert [r["rank"] for r in ranked] == [1, 1, 3, 3, 5]

	def test_top_20_limit(self):
		"""Leaderboard limited to 20 entries."""
		participants = [{"name": f"P{i}", "player": f"PL-{i}", "score": float(100 - i)} for i in range(30)]
		ranked, top_20 = compute_ranking(participants, {})
		assert len(ranked) == 30
		assert len(top_20) == 20

	def test_empty_participants(self):
		ranked, top_20 = compute_ranking([], {})
		assert ranked == []
		assert top_20 == []

	def test_single_participant(self):
		ranked, _ = compute_ranking(
			[{"name": "P1", "player": "PL-1", "score": 75.0}],
			{"PL-1": "Ahmed"},
		)
		assert len(ranked) == 1
		assert ranked[0]["rank"] == 1
		assert ranked[0]["display_name"] == "Ahmed"

	def test_display_name_fallback(self):
		"""Missing display_name falls back to player ID."""
		ranked, _ = compute_ranking([{"name": "P1", "player": "PL-1", "score": 75.0}], {})
		assert ranked[0]["display_name"] == "PL-1"

	def test_unsorted_input_sorts_correctly(self):
		"""Function sorts internally even if input is unsorted."""
		participants = [
			{"name": "P1", "player": "PL-1", "score": 50.0},
			{"name": "P2", "player": "PL-2", "score": 90.0},
			{"name": "P3", "player": "PL-3", "score": 70.0},
		]
		ranked, _ = compute_ranking(participants, {})
		assert ranked[0]["player"] == "PL-2"
		assert ranked[0]["rank"] == 1

	def test_top_20_has_correct_fields(self):
		"""top_20 entries have rank, player, display_name, score."""
		participants = [{"name": "P1", "player": "PL-1", "score": 99.0}]
		_, top_20 = compute_ranking(participants, {"PL-1": "Ahmed"})
		entry = top_20[0]
		assert set(entry.keys()) == {"rank", "player", "display_name", "score"}


# =============================================================================
# compute_xp_awards
# =============================================================================


class TestComputeXpAwards:
	"""Tests for the compute_xp_awards pure function."""

	XP = [
		{"rank": 0, "reward_type": "XP", "xp_amount": 50, "prize_description": ""},
		{"rank": 1, "reward_type": "XP", "xp_amount": 500, "prize_description": ""},
		{"rank": 2, "reward_type": "XP", "xp_amount": 300, "prize_description": ""},
		{"rank": 3, "reward_type": "XP", "xp_amount": 100, "prize_description": ""},
	]

	def test_standard_distribution(self):
		"""1st, 2nd, 3rd, 4th get correct XP."""
		ranked = [
			{"name": "P1", "player": "PL-1", "rank": 1},
			{"name": "P2", "player": "PL-2", "rank": 2},
			{"name": "P3", "player": "PL-3", "rank": 3},
			{"name": "P4", "player": "PL-4", "rank": 4},
		]
		awards = compute_xp_awards(ranked, self.XP)
		assert awards[0]["total_xp"] == 500  # rank 1
		assert awards[1]["total_xp"] == 300  # rank 2
		assert awards[2]["total_xp"] == 100  # rank 3
		assert awards[3]["total_xp"] == 50  # rank 0 fallback

	def test_tied_first_place_both_get_first_xp(self):
		ranked = [
			{"name": "P1", "player": "PL-1", "rank": 1},
			{"name": "P2", "player": "PL-2", "rank": 1},
			{"name": "P3", "player": "PL-3", "rank": 3},
		]
		awards = compute_xp_awards(ranked, self.XP)
		assert awards[0]["total_xp"] == 500
		assert awards[1]["total_xp"] == 500
		assert awards[2]["total_xp"] == 100

	def test_zero_xp_config(self):
		ranked = [{"name": "P1", "player": "PL-1", "rank": 1}]
		awards = compute_xp_awards(
			ranked,
			[
				{"rank": 0, "reward_type": "XP", "xp_amount": 0, "prize_description": ""},
			],
		)
		assert awards[0]["total_xp"] == 0

	def test_participation_only_no_rank_bonus(self):
		ranked = [
			{"name": "P1", "player": "PL-1", "rank": 1},
			{"name": "P2", "player": "PL-2", "rank": 2},
		]
		awards = compute_xp_awards(
			ranked,
			[
				{"rank": 0, "reward_type": "XP", "xp_amount": 100, "prize_description": ""},
			],
		)
		assert awards[0]["total_xp"] == 100
		assert awards[1]["total_xp"] == 100

	def test_empty_ranked(self):
		assert compute_xp_awards([], self.XP) == []

	def test_rank_beyond_third_gets_default(self):
		ranked = [{"name": f"P{i}", "player": f"PL-{i}", "rank": i} for i in range(4, 8)]
		awards = compute_xp_awards(ranked, self.XP)
		for a in awards:
			assert a["total_xp"] == 50  # rank 0 fallback
