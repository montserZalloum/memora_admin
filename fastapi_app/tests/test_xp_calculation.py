"""Pure function tests for XP and level calculation."""

import pytest

from fastapi_app.services.wallet import calculate_xp_award
from fastapi_app.core.constants import calculate_level


class TestXpCalculation:
	"""Tests for calculate_xp_award pure function."""

	def test_fresh_base_xp(self):
		"""Test fresh completion with base XP (no lesson override)."""
		result = calculate_xp_award(
			base_xp=50,
			lesson_xp=0,
			current_streak=0,
			max_multiplier_percent=50,
			is_replay=False,
			replay_xp=0,
		)
		assert result == 50

	def test_fresh_lesson_xp_override(self):
		"""Test that lesson_xp takes precedence over base_xp when > 0."""
		result = calculate_xp_award(
			base_xp=50,
			lesson_xp=75,
			current_streak=0,
			max_multiplier_percent=50,
			is_replay=False,
			replay_xp=0,
		)
		assert result == 75

	def test_replay_fixed_amount(self):
		"""Test replay completion ignores base_xp and lesson_xp, uses replay_xp."""
		result = calculate_xp_award(
			base_xp=50,
			lesson_xp=75,
			current_streak=0,
			max_multiplier_percent=50,
			is_replay=True,
			replay_xp=10,
		)
		assert result == 10

	def test_replay_ignores_hearts(self):
		"""Test replay completion ignores hearts bonus."""
		result = calculate_xp_award(
			base_xp=50,
			lesson_xp=75,
			current_streak=0,
			max_multiplier_percent=50,
			is_replay=True,
			replay_xp=10,
			hearts_remaining=5,
			xp_per_heart=3,
		)
		assert result == 10

	def test_hearts_bonus_fresh(self):
		"""Test hearts bonus is added to fresh completion."""
		result = calculate_xp_award(
			base_xp=50,
			lesson_xp=0,
			current_streak=0,
			max_multiplier_percent=50,
			is_replay=False,
			replay_xp=0,
			hearts_remaining=3,
			xp_per_heart=5,
		)
		# base_xp (50) + hearts_bonus (3*5=15) = 65
		assert result == 65

	def test_streak_multiplier_linear(self):
		"""Test streak multiplier is linear: +1% per day."""
		result = calculate_xp_award(
			base_xp=100,
			lesson_xp=0,
			current_streak=10,
			max_multiplier_percent=50,
			is_replay=False,
			replay_xp=0,
		)
		# 100 * (1 + 10*0.01) = 100 * 1.10 = 110
		assert result == 110

	def test_streak_multiplier_capped(self):
		"""Test streak multiplier is capped at max_multiplier_percent."""
		result = calculate_xp_award(
			base_xp=100,
			lesson_xp=0,
			current_streak=100,
			max_multiplier_percent=50,
			is_replay=False,
			replay_xp=0,
		)
		# 100 * (1 + 50*0.01) = 100 * 1.50 = 150 (capped, not 1 + 100*0.01)
		assert result == 150

	def test_streak_zero_no_bonus(self):
		"""Test streak=0 applies no multiplier (1.0x)."""
		result = calculate_xp_award(
			base_xp=100,
			lesson_xp=0,
			current_streak=0,
			max_multiplier_percent=50,
			is_replay=False,
			replay_xp=0,
		)
		# 100 * 1.0 = 100
		assert result == 100

	def test_result_floored(self):
		"""Test result is floored (not rounded)."""
		result = calculate_xp_award(
			base_xp=33,
			lesson_xp=0,
			current_streak=1,
			max_multiplier_percent=50,
			is_replay=False,
			replay_xp=0,
		)
		# 33 * (1 + 1*0.01) = 33 * 1.01 = 33.33 → floor to 33
		assert result == 33

	def test_zero_inputs(self):
		"""Test all zero inputs returns 0."""
		result = calculate_xp_award(
			base_xp=0,
			lesson_xp=0,
			current_streak=0,
			max_multiplier_percent=50,
			is_replay=False,
			replay_xp=0,
		)
		assert result == 0

	def test_replay_with_streak(self):
		"""Test replay applies streak multiplier."""
		result = calculate_xp_award(
			base_xp=0,
			lesson_xp=0,
			current_streak=10,
			max_multiplier_percent=50,
			is_replay=True,
			replay_xp=10,
		)
		# 10 * (1 + 10*0.01) = 10 * 1.10 = 11.0 → floor to 11
		assert result == 11


class TestLevelCalculation:
	"""Tests for calculate_level pure function."""

	def test_level_zero_xp(self):
		"""Test level calculation at 0 XP."""
		level, title, xp_in_level, xp_to_next = calculate_level(0)
		assert level == 1
		assert title == "Beginner"
		assert xp_in_level == 0
		assert xp_to_next == 100

	def test_level_exact_boundary(self):
		"""Test level calculation at exact threshold (100 XP = Level 2)."""
		level, title, xp_in_level, xp_to_next = calculate_level(100)
		assert level == 2
		assert title == "Learner"
		assert xp_in_level == 0
		assert xp_to_next == 200

	def test_level_max(self):
		"""Test level calculation at max level (11000+ XP = Level 15)."""
		level, title, xp_in_level, xp_to_next = calculate_level(11000)
		assert level == 15
		assert title == "Transcendent"
		assert xp_in_level == 0
		assert xp_to_next == 0

		# Also test overflow (12000 XP still = Level 15)
		level, title, xp_in_level, xp_to_next = calculate_level(12000)
		assert level == 15
		assert xp_to_next == 0

	def test_level_mid_level(self):
		"""Test level calculation in middle of range (500 XP = Level 3)."""
		level, title, xp_in_level, xp_to_next = calculate_level(500)
		assert level == 3
		assert title == "Explorer"
		# xp_in_level: 500 - 300 (L3 threshold) = 200
		assert xp_in_level == 200
		# xp_to_next: 600 (L4 threshold) - 500 = 100
		assert xp_to_next == 100
