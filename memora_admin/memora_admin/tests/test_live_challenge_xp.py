"""Unit tests for Live Challenge XP calculation.

Tests:
- participation_xp added to all submitters
- rank 1 gets first_place_xp, rank 2 second_place_xp, rank 3 third_place_xp, rank 4+ default_xp
- Tied rank 1 students both get first_place_xp
- total = participation_xp + rank_bonus
"""

import unittest

from memora_admin.tasks.live_challenge_transitions import compute_xp_awards


class TestXPCalculation(unittest.TestCase):
	"""Test XP awards based on rank and participation."""

	def _make_xp_config(
		self,
		participation_xp=10,
		first_place_xp=50,
		second_place_xp=30,
		third_place_xp=20,
		default_xp=5,
	):
		return {
			"participation_xp": participation_xp,
			"first_place_xp": first_place_xp,
			"second_place_xp": second_place_xp,
			"third_place_xp": third_place_xp,
			"default_xp": default_xp,
		}

	def test_participation_xp_added_to_all(self):
		"""All submitters get participation_xp regardless of rank."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 60.0, "rank": 3},
			{"name": "P4", "player": "PLAYER-004", "score": 40.0, "rank": 4},
			{"name": "P5", "player": "PLAYER-005", "score": 20.0, "rank": 5},
		]
		config = self._make_xp_config(participation_xp=10)
		awards = compute_xp_awards(ranked, config)

		for award in awards:
			self.assertGreaterEqual(award["total_xp"], 10)

	def test_rank_1_gets_first_place_xp(self):
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
		]
		config = self._make_xp_config(participation_xp=10, first_place_xp=50)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(awards[0]["total_xp"], 60)  # 10 + 50

	def test_rank_2_gets_second_place_xp(self):
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 60.0, "rank": 3},
		]
		config = self._make_xp_config(participation_xp=10, second_place_xp=30)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(awards[1]["total_xp"], 40)  # 10 + 30

	def test_rank_3_gets_third_place_xp(self):
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 60.0, "rank": 3},
		]
		config = self._make_xp_config(participation_xp=10, third_place_xp=20)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(awards[2]["total_xp"], 30)  # 10 + 20

	def test_rank_4_plus_gets_default_xp(self):
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 60.0, "rank": 3},
			{"name": "P4", "player": "PLAYER-004", "score": 40.0, "rank": 4},
			{"name": "P5", "player": "PLAYER-005", "score": 20.0, "rank": 5},
		]
		config = self._make_xp_config(participation_xp=10, default_xp=5)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(awards[3]["total_xp"], 15)  # 10 + 5
		self.assertEqual(awards[4]["total_xp"], 15)  # 10 + 5

	def test_tied_rank_1_both_get_first_place_xp(self):
		"""Two players tied at rank 1 both get first_place_xp."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 100.0, "rank": 1},
			{"name": "P3", "player": "PLAYER-003", "score": 80.0, "rank": 3},
		]
		config = self._make_xp_config(participation_xp=10, first_place_xp=50)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(awards[0]["total_xp"], 60)  # 10 + 50
		self.assertEqual(awards[1]["total_xp"], 60)  # 10 + 50
		# Rank 3 gets third_place_xp (not second — standard competition ranking)
		self.assertEqual(awards[2]["total_xp"], 30)  # 10 + 20 (third_place_xp)

	def test_total_equals_participation_plus_rank_bonus(self):
		"""Verify total_xp = participation_xp + rank_bonus for each player."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 90.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 80.0, "rank": 3},
			{"name": "P4", "player": "PLAYER-004", "score": 70.0, "rank": 4},
		]
		config = self._make_xp_config(
			participation_xp=15,
			first_place_xp=100,
			second_place_xp=60,
			third_place_xp=30,
			default_xp=10,
		)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(awards[0]["total_xp"], 115)  # 15 + 100
		self.assertEqual(awards[1]["total_xp"], 75)  # 15 + 60
		self.assertEqual(awards[2]["total_xp"], 45)  # 15 + 30
		self.assertEqual(awards[3]["total_xp"], 25)  # 15 + 10

	def test_zero_participation_xp(self):
		"""participation_xp=0 means only rank bonus is awarded."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		config = self._make_xp_config(participation_xp=0, first_place_xp=50)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(awards[0]["total_xp"], 50)

	def test_all_xp_zero(self):
		"""No XP awarded when all values are 0."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		config = self._make_xp_config(
			participation_xp=0,
			first_place_xp=0,
			second_place_xp=0,
			third_place_xp=0,
			default_xp=0,
		)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(awards[0]["total_xp"], 0)

	def test_empty_ranked_returns_empty(self):
		"""No participants = no awards."""
		config = self._make_xp_config()
		awards = compute_xp_awards([], config)

		self.assertEqual(awards, [])

	def test_three_way_tie_at_rank_2(self):
		"""Three players tied at rank 2 all get second_place_xp."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
			{"name": "P2", "player": "PLAYER-002", "score": 80.0, "rank": 2},
			{"name": "P3", "player": "PLAYER-003", "score": 80.0, "rank": 2},
			{"name": "P4", "player": "PLAYER-004", "score": 80.0, "rank": 2},
			{"name": "P5", "player": "PLAYER-005", "score": 60.0, "rank": 5},
		]
		config = self._make_xp_config(participation_xp=10, second_place_xp=30, default_xp=5)
		awards = compute_xp_awards(ranked, config)

		# All 3 tied at rank 2 get second_place_xp
		self.assertEqual(awards[1]["total_xp"], 40)  # 10 + 30
		self.assertEqual(awards[2]["total_xp"], 40)  # 10 + 30
		self.assertEqual(awards[3]["total_xp"], 40)  # 10 + 30
		# Rank 5 gets default_xp (not third — there is no rank 3/4)
		self.assertEqual(awards[4]["total_xp"], 15)  # 10 + 5

	def test_award_contains_required_fields(self):
		"""Each award dict has name, player, and total_xp."""
		ranked = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0, "rank": 1},
		]
		config = self._make_xp_config(participation_xp=10, first_place_xp=50)
		awards = compute_xp_awards(ranked, config)

		self.assertEqual(len(awards), 1)
		self.assertEqual(awards[0]["name"], "P1")
		self.assertEqual(awards[0]["player"], "PLAYER-001")
		self.assertEqual(awards[0]["total_xp"], 60)


if __name__ == "__main__":
	unittest.main()
