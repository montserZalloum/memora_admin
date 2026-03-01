"""
Tests for leaderboard cleanup task.

Tests:
- Date extraction from daily and weekly key names
- Retention threshold enforcement (30d daily, 90d weekly)
- Empty SCAN (no keys to delete) completes without error
- Archive key cleanup with same retention thresholds

Uses real Redis on port 13001 with test-prefixed keys.
"""

import re
import uuid
from datetime import datetime, timedelta

import frappe
import redis
from frappe.tests.utils import FrappeTestCase

from fastapi_app.core.redis_keys import LB_PREFIX


class TestLeaderboardCleanup(FrappeTestCase):
	"""Integration tests for cleanup_old_leaderboards()."""

	def setUp(self):
		super().setUp()
		redis_url = frappe.conf.get("redis_memora", frappe.conf.redis_cache)
		self.r = redis.from_url(redis_url, decode_responses=True)
		self._test_id = uuid.uuid4().hex[:8]
		self._cleanup_keys = []

	def tearDown(self):
		# Clean up all test keys
		if self._cleanup_keys:
			self.r.delete(*self._cleanup_keys)
		super().tearDown()

	def _track(self, key: str):
		"""Register key for cleanup."""
		self._cleanup_keys.append(key)
		return key

	# =========================================================================
	# Date extraction tests
	# =========================================================================

	def test_daily_date_extraction(self):
		"""Daily keys use YYYY-MM-DD format — regex extracts date correctly."""
		# Pattern from leaderboard_cleanup.py
		pattern = re.compile(r":daily:(\d{4}-\d{2}-\d{2})")

		key = f"{LB_PREFIX}:daily:2026-01-15"
		match = pattern.search(key)
		self.assertIsNotNone(match)
		self.assertEqual(match.group(1), "2026-01-15")

		# Subject-specific variant
		key_subj = f"{LB_PREFIX}:daily:2026-01-15:subject:math-101"
		match_subj = pattern.search(key_subj)
		self.assertIsNotNone(match_subj)
		self.assertEqual(match_subj.group(1), "2026-01-15")

		# Plan-scoped variant
		key_plan = f"{LB_PREFIX}:daily:2026-01-15:plan:PLAN-001"
		match_plan = pattern.search(key_plan)
		self.assertIsNotNone(match_plan)
		self.assertEqual(match_plan.group(1), "2026-01-15")

	def test_weekly_date_extraction(self):
		"""Weekly keys use YYYY-MM-DD (Friday date) — regex extracts date correctly."""
		pattern = re.compile(r":weekly:(\d{4}-\d{2}-\d{2})")

		key = f"{LB_PREFIX}:weekly:2026-01-10"
		match = pattern.search(key)
		self.assertIsNotNone(match)
		self.assertEqual(match.group(1), "2026-01-10")

		# Subject-specific variant
		key_subj = f"{LB_PREFIX}:weekly:2026-01-10:subject:arabic-101"
		match_subj = pattern.search(key_subj)
		self.assertIsNotNone(match_subj)
		self.assertEqual(match_subj.group(1), "2026-01-10")

	# =========================================================================
	# Retention threshold tests
	# =========================================================================

	def test_old_daily_keys_deleted(self):
		"""Daily keys older than 30 days are deleted."""
		from memora_admin.tasks.leaderboard_cleanup import cleanup_old_leaderboards

		# Create a daily key 45 days ago
		old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
		old_key = self._track(f"{LB_PREFIX}:daily:{old_date}:test:{self._test_id}")
		self.r.zadd(old_key, {"player1": 100})

		# Create a recent daily key (5 days ago — should survive)
		recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
		recent_key = self._track(f"{LB_PREFIX}:daily:{recent_date}:test:{self._test_id}")
		self.r.zadd(recent_key, {"player1": 200})

		cleanup_old_leaderboards()

		self.assertFalse(self.r.exists(old_key), f"Old daily key should be deleted: {old_key}")
		self.assertTrue(self.r.exists(recent_key), f"Recent daily key should survive: {recent_key}")

	def test_old_weekly_keys_deleted(self):
		"""Weekly keys older than 90 days are deleted."""
		from memora_admin.tasks.leaderboard_cleanup import cleanup_old_leaderboards

		# Create a weekly key 120 days ago
		old_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
		old_key = self._track(f"{LB_PREFIX}:weekly:{old_date}:test:{self._test_id}")
		self.r.zadd(old_key, {"player1": 100})

		# Create a recent weekly key (30 days ago — should survive)
		recent_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
		recent_key = self._track(f"{LB_PREFIX}:weekly:{recent_date}:test:{self._test_id}")
		self.r.zadd(recent_key, {"player1": 200})

		cleanup_old_leaderboards()

		self.assertFalse(self.r.exists(old_key), f"Old weekly key should be deleted: {old_key}")
		self.assertTrue(self.r.exists(recent_key), f"Recent weekly key should survive: {recent_key}")

	def test_old_archive_keys_deleted(self):
		"""Archive keys (daily and weekly) older than 90 days are deleted."""
		from memora_admin.tasks.leaderboard_cleanup import cleanup_old_leaderboards

		old_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")

		old_archive_daily = self._track(f"{LB_PREFIX}:archive:daily:{old_date}:test:{self._test_id}")
		self.r.zadd(old_archive_daily, {"player1": 100})

		old_archive_weekly = self._track(f"{LB_PREFIX}:archive:weekly:{old_date}:test:{self._test_id}")
		self.r.zadd(old_archive_weekly, {"player1": 100})

		cleanup_old_leaderboards()

		self.assertFalse(self.r.exists(old_archive_daily))
		self.assertFalse(self.r.exists(old_archive_weekly))

	# =========================================================================
	# Empty SCAN test
	# =========================================================================

	def test_empty_scan_no_error(self):
		"""Cleanup completes without error when no leaderboard keys exist to delete."""
		from memora_admin.tasks.leaderboard_cleanup import cleanup_old_leaderboards

		# Just run it — should not raise
		cleanup_old_leaderboards()
