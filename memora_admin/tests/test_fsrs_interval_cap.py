"""
Tests for FSRS maximum review interval cap (90 days).

Verifies:
1. Ceiling clamp: next_review never exceeds today + 90 days
2. Floor clamp: next_review never earlier than tomorrow (unchanged existing behavior)
3. End-to-end: consecutive Good/Easy ratings stay within 90-day cap
4. Edge cases: boundary values, rating transitions, new vs existing cards

These tests are pure-logic unit tests (no database or Redis needed).
"""

import unittest
from datetime import date, datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler, State

# ---------------------------------------------------------------------------
# Group 1: Ceiling + Floor clamp logic (pure datetime arithmetic)
# ---------------------------------------------------------------------------


def _apply_clamp(due_date: date) -> date:
	"""Replicate the exact clamping logic from both files."""
	next_date = due_date
	tomorrow = date.today() + timedelta(days=1)
	if next_date < tomorrow:
		next_date = tomorrow
	max_date = date.today() + timedelta(days=90)
	if next_date > max_date:
		next_date = max_date
	return next_date


class TestClampLogic(unittest.TestCase):
	"""Test the floor + ceiling clamping applied to FSRS output."""

	def test_interval_under_90_not_clamped(self):
		"""A 30-day interval should pass through unchanged."""
		future = date.today() + timedelta(days=30)
		result = _apply_clamp(future)
		self.assertEqual(result, future)

	def test_interval_exactly_90_not_clamped(self):
		"""Exactly 90 days from today should pass through unchanged."""
		future = date.today() + timedelta(days=90)
		result = _apply_clamp(future)
		self.assertEqual(result, future)

	def test_interval_91_days_clamped(self):
		"""91 days from today should be clamped to 90."""
		future = date.today() + timedelta(days=91)
		result = _apply_clamp(future)
		self.assertEqual(result, date.today() + timedelta(days=90))

	def test_interval_365_days_clamped(self):
		"""1 year interval should be clamped to 90 days."""
		future = date.today() + timedelta(days=365)
		result = _apply_clamp(future)
		self.assertEqual(result, date.today() + timedelta(days=90))

	def test_interval_36500_days_clamped(self):
		"""100 years (old default) should be clamped to 90 days."""
		future = date.today() + timedelta(days=36500)
		result = _apply_clamp(future)
		self.assertEqual(result, date.today() + timedelta(days=90))

	def test_floor_clamp_past_date_becomes_tomorrow(self):
		"""A past date should be raised to tomorrow."""
		past = date.today() - timedelta(days=5)
		result = _apply_clamp(past)
		self.assertEqual(result, date.today() + timedelta(days=1))

	def test_floor_clamp_today_becomes_tomorrow(self):
		"""Today should be raised to tomorrow."""
		result = _apply_clamp(date.today())
		self.assertEqual(result, date.today() + timedelta(days=1))

	def test_tomorrow_not_clamped(self):
		"""Tomorrow should pass through unchanged (boundary of floor clamp)."""
		tomorrow = date.today() + timedelta(days=1)
		result = _apply_clamp(tomorrow)
		self.assertEqual(result, tomorrow)

	def test_one_day_interval_not_clamped(self):
		"""1-day-ahead should pass through unchanged."""
		future = date.today() + timedelta(days=1)
		result = _apply_clamp(future)
		self.assertEqual(result, future)

	def test_result_always_between_tomorrow_and_90_days(self):
		"""Any input should produce result in [tomorrow, today+90]."""
		tomorrow = date.today() + timedelta(days=1)
		max_date = date.today() + timedelta(days=90)
		test_offsets = [-100, -1, 0, 1, 2, 30, 89, 90, 91, 180, 365, 1000]
		for offset in test_offsets:
			input_date = date.today() + timedelta(days=offset)
			result = _apply_clamp(input_date)
			self.assertGreaterEqual(result, tomorrow, f"offset={offset}: {result} < {tomorrow}")
			self.assertLessEqual(result, max_date, f"offset={offset}: {result} > {max_date}")


# ---------------------------------------------------------------------------
# Group 3: End-to-end FSRS scheduler behavior with 90-day cap
# ---------------------------------------------------------------------------


class TestFSRSIntervalCapEndToEnd(unittest.TestCase):
	"""Test the actual FSRS Scheduler with maximum_interval=90 across review sequences."""

	def setUp(self):
		self.scheduler = Scheduler(maximum_interval=90)
		self.now = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)

	def _review_sequence(self, ratings: list[Rating], count: int | None = None) -> list[int]:
		"""Run a sequence of reviews and return the list of computed intervals (in days).

		If ratings is shorter than count, the last rating is repeated.
		"""
		if count is None:
			count = len(ratings)

		card = Card()
		now = self.now
		intervals = []

		for i in range(count):
			rating = ratings[i] if i < len(ratings) else ratings[-1]
			card, _log = self.scheduler.review_card(card, rating, now)
			interval = (card.due.date() - now.date()).days
			intervals.append(interval)
			now = card.due if card.due.date() > now.date() else now + timedelta(days=1)

		return intervals

	def test_consecutive_good_all_under_90(self):
		"""7 consecutive Good ratings — all intervals must be <= 90."""
		intervals = self._review_sequence([Rating.Good], count=7)
		for i, interval in enumerate(intervals):
			self.assertLessEqual(interval, 90, f"Review {i + 1}: interval={interval} exceeds 90")

	def test_consecutive_easy_all_under_90(self):
		"""7 consecutive Easy ratings — intervals grow fast but must cap at 90."""
		intervals = self._review_sequence([Rating.Easy], count=7)
		for i, interval in enumerate(intervals):
			self.assertLessEqual(interval, 90, f"Review {i + 1}: interval={interval} exceeds 90")

	def test_consecutive_good_eventually_hits_cap(self):
		"""After enough Good ratings, intervals should reach the 90-day cap."""
		intervals = self._review_sequence([Rating.Good], count=10)
		self.assertTrue(
			any(interval >= 85 for interval in intervals),
			f"Expected at least one interval near 90, got {intervals}",
		)

	def test_consecutive_easy_hits_cap_quickly(self):
		"""Easy ratings should hit the 90-day cap faster than Good."""
		easy_intervals = self._review_sequence([Rating.Easy], count=5)
		good_intervals = self._review_sequence([Rating.Good], count=5)

		# Easy should reach 90 sooner (or equal)
		easy_max = max(easy_intervals)
		good_max = max(good_intervals)
		self.assertGreaterEqual(easy_max, good_max)

	def test_again_resets_interval(self):
		"""After building up stability, an Again rating should reset to a short interval."""
		card = Card()
		now = self.now

		# Build up stability with Good ratings
		for _ in range(5):
			card, _ = self.scheduler.review_card(card, Rating.Good, now)
			now = card.due if card.due.date() > now.date() else now + timedelta(days=1)

		# Hit Again — should drop significantly
		card, _ = self.scheduler.review_card(card, Rating.Again, now)
		interval_after_again = (card.due.date() - now.date()).days

		self.assertLess(interval_after_again, 10, f"Again should reset interval, got {interval_after_again}")

	def test_hard_rating_smaller_than_good(self):
		"""Hard rating should produce shorter intervals than Good at same review position."""
		good_intervals = self._review_sequence([Rating.Good], count=5)
		hard_intervals = self._review_sequence([Rating.Hard], count=5)

		# By the 5th review, Good should have a larger interval than Hard
		self.assertGreater(
			good_intervals[-1],
			hard_intervals[-1],
			f"Good[-1]={good_intervals[-1]} should be > Hard[-1]={hard_intervals[-1]}",
		)

	def test_mixed_ratings_all_capped(self):
		"""Mixed Good/Hard/Again sequence — all intervals must be <= 90."""
		ratings = [
			Rating.Good,
			Rating.Good,
			Rating.Hard,
			Rating.Good,
			Rating.Again,
			Rating.Good,
			Rating.Easy,
			Rating.Good,
			Rating.Good,
			Rating.Good,
		]
		intervals = self._review_sequence(ratings)
		for i, interval in enumerate(intervals):
			self.assertLessEqual(interval, 90, f"Review {i + 1}: interval={interval} exceeds 90")

	def test_new_card_first_good_reasonable_interval(self):
		"""First review of a new card with Good should give a short interval (not 90)."""
		intervals = self._review_sequence([Rating.Good], count=1)
		self.assertLess(intervals[0], 10, f"First Good should be short, got {intervals[0]}")

	def test_new_card_first_again_minimal_interval(self):
		"""First review of a new card with Again should give minimal interval."""
		intervals = self._review_sequence([Rating.Again], count=1)
		self.assertLessEqual(intervals[0], 1, f"First Again should be <=1 day, got {intervals[0]}")

	def test_high_stability_card_still_capped(self):
		"""A card with artificially high stability should still be capped at 90."""
		card = Card()
		card.stability = 500.0  # Very high stability
		card.difficulty = 5.0
		card.state = State.Review
		card.step = None
		card.due = self.now - timedelta(days=90)  # Due 90 days ago
		card.last_review = self.now - timedelta(days=180)

		card, _ = self.scheduler.review_card(card, Rating.Good, self.now)
		interval = (card.due.date() - self.now.date()).days

		self.assertLessEqual(interval, 90, f"High-stability card: interval={interval} exceeds 90")

	def test_20_consecutive_good_never_exceeds_90(self):
		"""Extended sequence of 20 Good ratings — exhaustive check."""
		intervals = self._review_sequence([Rating.Good], count=20)
		for i, interval in enumerate(intervals):
			self.assertLessEqual(interval, 90, f"Review {i + 1}: interval={interval} exceeds 90")

	def test_alternating_good_easy_capped(self):
		"""Alternating Good/Easy sequence — all intervals must be <= 90."""
		ratings = [Rating.Good, Rating.Easy] * 5
		intervals = self._review_sequence(ratings)
		for i, interval in enumerate(intervals):
			self.assertLessEqual(interval, 90, f"Review {i + 1}: interval={interval} exceeds 90")

	def test_recovery_after_lapse(self):
		"""After a lapse (Again), subsequent Good ratings should recover normally and stay capped."""
		ratings = [
			Rating.Good,
			Rating.Good,
			Rating.Good,
			Rating.Again,  # Lapse
			Rating.Good,
			Rating.Good,
			Rating.Good,
			Rating.Good,
			Rating.Good,
		]
		intervals = self._review_sequence(ratings)
		for i, interval in enumerate(intervals):
			self.assertLessEqual(interval, 90, f"Review {i + 1}: interval={interval} exceeds 90")

		# Verify lapse caused a reset — interval at position 3 (Again) should be small
		self.assertLess(intervals[3], 5, f"Lapse interval should be small, got {intervals[3]}")


# ---------------------------------------------------------------------------
# Group 4: Verify Scheduler.maximum_interval attribute directly
# ---------------------------------------------------------------------------


class TestSchedulerAttributeDirect(unittest.TestCase):
	"""Verify the Scheduler object exposes maximum_interval=90."""

	def test_scheduler_with_cap(self):
		"""Scheduler(maximum_interval=90) should expose the attribute."""
		s = Scheduler(maximum_interval=90)
		self.assertEqual(s.maximum_interval, 90)

	def test_scheduler_default_is_not_90(self):
		"""Default Scheduler() should have a much larger maximum_interval (proves our fix is necessary)."""
		s = Scheduler()
		self.assertGreater(s.maximum_interval, 90)

	def test_scheduler_cap_is_enforced_by_library(self):
		"""FSRS library itself should enforce the cap — no interval > 90 even without our clamp."""
		s = Scheduler(maximum_interval=90)
		card = Card()
		now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

		max_interval = 0
		for _ in range(15):
			card, _ = s.review_card(card, Rating.Easy, now)
			interval = (card.due.date() - now.date()).days
			max_interval = max(max_interval, interval)
			now = card.due if card.due.date() > now.date() else now + timedelta(days=1)

		self.assertLessEqual(max_interval, 90)


if __name__ == "__main__":
	unittest.main()
