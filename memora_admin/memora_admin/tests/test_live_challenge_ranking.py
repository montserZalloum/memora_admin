"""Unit tests for Live Challenge leaderboard ranking computation.

Tests:
- Standard competition ranking (scores [100, 100, 95, 90] -> ranks [1, 1, 3, 4])
- Single participant gets rank 1
- All same scores share rank 1
- Top 20 truncation
- display_name resolved in leaderboard entries
"""

import unittest

from memora_admin.tasks.live_challenge_transitions import compute_ranking


class TestStandardCompetitionRanking(unittest.TestCase):
	"""Test standard competition ranking (1, 1, 3 — NOT dense 1, 1, 2)."""

	def test_basic_ranking(self):
		"""scores [100, 100, 95, 90] -> ranks [1, 1, 3, 4]."""
		participants = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0},
			{"name": "P2", "player": "PLAYER-002", "score": 100.0},
			{"name": "P3", "player": "PLAYER-003", "score": 95.0},
			{"name": "P4", "player": "PLAYER-004", "score": 90.0},
		]
		display_names = {
			"PLAYER-001": "Ahmed",
			"PLAYER-002": "Sara",
			"PLAYER-003": "Omar",
			"PLAYER-004": "Fatima",
		}

		ranked, top20 = compute_ranking(participants, display_names)

		self.assertEqual(ranked[0]["rank"], 1)
		self.assertEqual(ranked[1]["rank"], 1)
		self.assertEqual(ranked[2]["rank"], 3)
		self.assertEqual(ranked[3]["rank"], 4)

	def test_single_participant(self):
		"""Single participant gets rank 1."""
		participants = [
			{"name": "P1", "player": "PLAYER-001", "score": 50.0},
		]
		display_names = {"PLAYER-001": "Ahmed"}

		ranked, top20 = compute_ranking(participants, display_names)

		self.assertEqual(len(ranked), 1)
		self.assertEqual(ranked[0]["rank"], 1)

	def test_all_same_scores_share_rank_1(self):
		"""All participants with same score share rank 1."""
		participants = [
			{"name": f"P{i}", "player": f"PLAYER-{i:03}", "score": 75.0}
			for i in range(1, 6)
		]
		display_names = {f"PLAYER-{i:03}": f"Student {i}" for i in range(1, 6)}

		ranked, top20 = compute_ranking(participants, display_names)

		for entry in ranked:
			self.assertEqual(entry["rank"], 1)

	def test_top_20_truncation(self):
		"""Leaderboard top 20 is truncated even if more participants exist."""
		participants = [
			{"name": f"P{i}", "player": f"PLAYER-{i:03}", "score": float(100 - i)}
			for i in range(30)
		]
		display_names = {f"PLAYER-{i:03}": f"Student {i}" for i in range(30)}

		ranked, top20 = compute_ranking(participants, display_names)

		self.assertEqual(len(ranked), 30)
		self.assertEqual(len(top20), 20)
		# Top 20 should have ranks 1 through 20
		self.assertEqual(top20[0]["rank"], 1)
		self.assertEqual(top20[-1]["rank"], 20)

	def test_display_name_resolved(self):
		"""display_name is populated from the name lookup."""
		participants = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0},
			{"name": "P2", "player": "PLAYER-002", "score": 90.0},
		]
		display_names = {
			"PLAYER-001": "Ahmed",
			"PLAYER-002": "Sara",
		}

		ranked, top20 = compute_ranking(participants, display_names)

		self.assertEqual(top20[0]["display_name"], "Ahmed")
		self.assertEqual(top20[1]["display_name"], "Sara")

	def test_missing_display_name_fallback(self):
		"""Missing display_name falls back to player ID."""
		participants = [
			{"name": "P1", "player": "PLAYER-001", "score": 100.0},
		]
		display_names = {}  # No names resolved

		ranked, top20 = compute_ranking(participants, display_names)

		self.assertEqual(top20[0]["display_name"], "PLAYER-001")

	def test_three_way_tie_then_gap(self):
		"""Three-way tie at top: ranks [1, 1, 1, 4, 5]."""
		participants = [
			{"name": f"P{i}", "player": f"PLAYER-{i:03}", "score": s}
			for i, s in enumerate([100.0, 100.0, 100.0, 80.0, 70.0], 1)
		]
		display_names = {f"PLAYER-{i:03}": f"S{i}" for i in range(1, 6)}

		ranked, top20 = compute_ranking(participants, display_names)

		self.assertEqual([r["rank"] for r in ranked], [1, 1, 1, 4, 5])

	def test_empty_participants(self):
		"""No participants returns empty results."""
		ranked, top20 = compute_ranking([], {})

		self.assertEqual(ranked, [])
		self.assertEqual(top20, [])

	def test_zero_scores(self):
		"""Participants with 0 score are still ranked."""
		participants = [
			{"name": "P1", "player": "PLAYER-001", "score": 0.0},
			{"name": "P2", "player": "PLAYER-002", "score": 0.0},
		]
		display_names = {"PLAYER-001": "A", "PLAYER-002": "B"}

		ranked, top20 = compute_ranking(participants, display_names)

		self.assertEqual(ranked[0]["rank"], 1)
		self.assertEqual(ranked[1]["rank"], 1)


if __name__ == "__main__":
	unittest.main()
