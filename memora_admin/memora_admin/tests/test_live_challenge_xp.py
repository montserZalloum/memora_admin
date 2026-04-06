"""Unit tests for Live Challenge XP calculation.

Tests:
- Rank-based reward lookup with rank 0 fallback
- Multiple XP rows per rank are summed
- Prize-only ranks yield total_xp = 0
- Mixed XP + Prize rows (XP summed, Prize ignored in XP calc)
- No stacking — explicit rank rows fully replace fallback
"""

import unittest

from memora_admin.tasks.live_challenge_transitions import compute_xp_awards


def _make_rewards(
	rank_0_xp=10,
	rank_1_xp=50,
	rank_2_xp=30,
	rank_3_xp=20,
):
	"""Build a standard rewards list matching the old participation + rank bonus pattern."""
	return [
		{"rank": 0, "reward_type": "XP", "xp_amount": rank_0_xp, "prize_description": ""},
		{"rank": 1, "reward_type": "XP", "xp_amount": rank_1_xp, "prize_description": ""},
		{"rank": 2, "reward_type": "XP", "xp_amount": rank_2_xp, "prize_description": ""},
		{"rank": 3, "reward_type": "XP", "xp_amount": rank_3_xp, "prize_description": ""},
	]


class TestXPCalculation(unittest.TestCase):
	"""Test XP awards based on rank and reward rows."""

	def test_rank_1_gets_rank_1_xp(self):
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
		]
		rewards = _make_rewards(rank_1_xp=50)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 50)

	def test_rank_2_gets_rank_2_xp(self):
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 60.0, "rank": 3},
		]
		rewards = _make_rewards(rank_2_xp=30)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[1]["total_xp"], 30)

	def test_rank_3_gets_rank_3_xp(self):
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 60.0, "rank": 3},
		]
		rewards = _make_rewards(rank_3_xp=20)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[2]["total_xp"], 20)

	def test_unmatched_rank_falls_back_to_rank_0(self):
		"""Rank 4+ with no explicit row falls back to rank 0."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P4", "player": "PLAYER-004", "score": 40.0, "rank": 4},
			{"name": "P5", "player": "PLAYER-005", "score": 20.0, "rank": 5},
		]
		rewards = _make_rewards(rank_0_xp=10)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[1]["total_xp"], 10)
		self.assertEqual(awards[2]["total_xp"], 10)

	def test_explicit_rank_overrides_fallback_no_stacking(self):
		"""Rank 1 row is used instead of rank 0 — no stacking."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		rewards = [
			{"rank": 0, "reward_type": "XP", "xp_amount": 10, "prize_description": ""},
			{"rank": 1, "reward_type": "XP", "xp_amount": 50, "prize_description": ""},
		]
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 50)  # NOT 60 (no stacking)

	def test_tied_rank_1_both_get_rank_1_xp(self):
		"""Two players tied at rank 1 both get rank 1 rewards."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 100.0, "rank": 1},
			{"name": "P3", "player": "PLAYER-003", "score": 80.0, "rank": 3},
		]
		rewards = _make_rewards(rank_1_xp=50, rank_3_xp=20)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 50)
		self.assertEqual(awards[1]["total_xp"], 50)
		self.assertEqual(awards[2]["total_xp"], 20)

	def test_total_equals_sum_of_xp_rows_for_rank(self):
		"""Verify total_xp = sum of all XP rows for that rank."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 90.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 80.0, "rank": 3},
			{"name": "P4", "player": "PLAYER-004", "score": 70.0, "rank": 4},
		]
		rewards = _make_rewards(rank_0_xp=10, rank_1_xp=100, rank_2_xp=60, rank_3_xp=30)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 100)
		self.assertEqual(awards[1]["total_xp"], 60)
		self.assertEqual(awards[2]["total_xp"], 30)
		self.assertEqual(awards[3]["total_xp"], 10)  # fallback

	def test_all_xp_zero(self):
		"""No XP awarded when all XP amounts are 0."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		rewards = _make_rewards(rank_0_xp=0, rank_1_xp=0, rank_2_xp=0, rank_3_xp=0)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 0)

	def test_empty_ranked_returns_empty(self):
		"""No participants = no awards."""
		rewards = _make_rewards()
		awards = compute_xp_awards([], rewards)
		self.assertEqual(awards, [])

	def test_empty_rewards_returns_zero_xp(self):
		"""No rewards defined = 0 XP for all."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		awards = compute_xp_awards(ranked, [])
		self.assertEqual(awards[0]["total_xp"], 0)

	def test_three_way_tie_at_rank_2(self):
		"""Three players tied at rank 2 all get rank 2 rewards."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 80.0, "rank": 2},
			{"name": "P4", "player": "PLAYER-004", "score": 80.0, "rank": 2},
			{"name": "P5", "player": "PLAYER-005", "score": 60.0, "rank": 5},
		]
		rewards = _make_rewards(rank_0_xp=5, rank_2_xp=30)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[1]["total_xp"], 30)
		self.assertEqual(awards[2]["total_xp"], 30)
		self.assertEqual(awards[3]["total_xp"], 30)
		self.assertEqual(awards[4]["total_xp"], 5)  # fallback

	def test_award_contains_required_fields(self):
		"""Each award dict has name, player, and total_xp."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		rewards = _make_rewards(rank_1_xp=50)
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(len(awards), 1)
		self.assertEqual(awards[0]["name"], "P1")
		self.assertEqual(awards[0]["player"], "PLAYER-001")
		self.assertEqual(awards[0]["total_xp"], 50)

	def test_multiple_xp_rows_per_rank_are_summed(self):
		"""Multiple XP reward rows for the same rank are summed."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		rewards = [
			{"rank": 1, "reward_type": "XP", "xp_amount": 50, "prize_description": ""},
			{"rank": 1, "reward_type": "XP", "xp_amount": 25, "prize_description": ""},
		]
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 75)

	def test_prize_only_rank_yields_zero_xp(self):
		"""A rank with only Prize rows yields total_xp = 0."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		rewards = [
			{"rank": 1, "reward_type": "Prize", "xp_amount": 0, "prize_description": "Gold Trophy"},
		]
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 0)

	def test_mixed_xp_and_prize_rows(self):
		"""XP rows are summed; Prize rows are ignored for XP calculation."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		rewards = [
			{"rank": 1, "reward_type": "XP", "xp_amount": 100, "prize_description": ""},
			{"rank": 1, "reward_type": "Prize", "xp_amount": 0, "prize_description": "Gold Medal"},
		]
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 100)

	def test_fallback_used_when_no_explicit_rank_row(self):
		"""Rank 0 fallback is used for ranks without explicit rows."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 7},
		]
		rewards = [
			{"rank": 0, "reward_type": "XP", "xp_amount": 15, "prize_description": ""},
			{"rank": 1, "reward_type": "XP", "xp_amount": 100, "prize_description": ""},
		]
		awards = compute_xp_awards(ranked, rewards)
		self.assertEqual(awards[0]["total_xp"], 15)


if __name__ == "__main__":
	unittest.main()
