"""Lifecycle tests for memora_admin.api.plan_change.execute_plan_change().

Tests the complete plan change flow: validation, snapshot, cleanup, and profile update.
Uses existing season SEAS-00027 to avoid MySQL partition constraints.
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, random_string, today

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEASON_ID = "SEAS-00027"


def _make_grade(label: str | None = None):
	"""Create and return a Memora Grade document."""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Grade",
			"grade_title": label or f"G-PC-{random_string(6)}",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _make_major(label: str | None = None):
	"""Create and return a Memora Major document."""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Major",
			"major_title": label or f"M-PC-{random_string(6)}",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _make_plan(
	season: str = _SEASON_ID,
	grade: str | None = None,
	major: str | None = None,
	is_published: int = 1,
):
	"""Create and return a Memora Academic Plan document."""
	if grade is None:
		grade = _make_grade().name
	doc = frappe.get_doc(
		{
			"doctype": "Memora Academic Plan",
			"plan_name": f"Plan-PC-{random_string(6)}",
			"grade": grade,
			"major": major,
			"season": season,
			"is_published": is_published,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _make_player(plan, grade, major, season):
	"""Create and return a Memora Player Profile document.

	The after_insert hook on the DocType automatically creates a wallet.
	"""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Player Profile",
			"display_name": f"Player-PC-{random_string(6)}",
			"plan": plan,
			"grade": grade,
			"major": major,
			"season": season,
			"avatar": "pre",
			"mobile": f"20{str(abs(hash(random_string(16))))[:10]}",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _make_subscription(player_id: str, access_key: str, expires_at: str | None = None):
	"""Create and return a Memora Player Subscription document."""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Player Subscription",
			"player": player_id,
			"access_key": access_key,
			"expires_at": expires_at or add_days(today(), 90),
			"is_active": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _make_progress(player_id: str, subject_id: str, bitset: str = "00", pct: float = 0.0):
	"""Create and return a Memora Structure Progress document."""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Structure Progress",
			"player": player_id,
			"subject": subject_id,
			"passed_lessons_bitset": bitset,
			"completion_percentage": pct,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _make_subject(title: str | None = None):
	"""Create and return a Memora Subject document."""
	doc = frappe.get_doc(
		{
			"doctype": "Memora Subject",
			"subject_title": title or f"Subj-PC-{random_string(6)}",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _make_expired_season():
	"""Create a season whose end_date is in the past (expired).

	Uses a high-randomised season_seq to avoid constraint collisions.
	"""
	import hashlib
	import time

	seq = int(hashlib.md5(f"{time.time()}{random_string(8)}".encode()).hexdigest()[:6], 16) % 10000 + 1
	doc = frappe.get_doc(
		{
			"doctype": "Memora Season",
			"season_title": f"Expired-PC-{random_string(6)}",
			"season_seq": seq,
			"start_date": add_days(today(), -365),
			"end_date": add_days(today(), -1),
			"is_published": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _set_wallet_values(player_id: str, **kwargs):
	"""Set wallet fields for a player (total_xp, current_streak, etc.)."""
	wallet_name = frappe.db.get_value("Memora Player Wallet", {"player": player_id}, "name")
	if wallet_name:
		frappe.db.set_value("Memora Player Wallet", wallet_name, kwargs, update_modified=False)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestExecutePlanChange(FrappeTestCase):
	"""Tests for memora_admin.api.plan_change.execute_plan_change()."""

	def setUp(self):
		super().setUp()

		# -- Shared fixtures: two distinct grades, majors, plans --
		self.grade_old = _make_grade("OldGrade")
		self.grade_new = _make_grade("NewGrade")
		self.major_old = _make_major("OldMajor")
		self.major_new = _make_major("NewMajor")

		self.plan_old = _make_plan(
			season=_SEASON_ID,
			grade=self.grade_old.name,
			major=self.major_old.name,
		)
		self.plan_new = _make_plan(
			season=_SEASON_ID,
			grade=self.grade_new.name,
			major=self.major_new.name,
		)

		# -- Player on old plan (wallet auto-created by after_insert hook) --
		self.player = _make_player(
			plan=self.plan_old.name,
			grade=self.grade_old.name,
			major=self.major_old.name,
			season=_SEASON_ID,
		)

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	# ------------------------------------------------------------------
	# 1. Successful plan change -- history record + accurate snapshots
	# ------------------------------------------------------------------
	def test_success_creates_history_with_accurate_snapshots(self):
		"""A successful plan change creates a history record with accurate wallet,
		subscription, and progress snapshots."""
		from memora_admin.api.plan_change import execute_plan_change

		# Seed wallet values
		_set_wallet_values(
			self.player.name,
			total_xp=1500,
			current_streak=7,
			total_lessons=42,
			total_time_min=320,
		)

		# Seed subscriptions
		_make_subscription(self.player.name, "SUB-MATH-101")

		# Seed progress
		subj = _make_subject()
		_make_progress(self.player.name, subj.name, bitset="FF", pct=50.0)

		result = execute_plan_change(self.player.name, self.plan_new.name)

		self.assertEqual(result["status"], "ok")
		self.assertTrue(result["history_id"].startswith("PLHIST-"))
		self.assertEqual(result["previous_plan"], self.plan_old.name)

		# Verify history record contents
		hist = frappe.get_doc("Memora Player Plan History", result["history_id"])
		self.assertEqual(hist.player, self.player.name)
		self.assertEqual(hist.previous_plan, self.plan_old.name)
		self.assertEqual(hist.previous_grade, self.grade_old.name)
		self.assertEqual(hist.previous_major, self.major_old.name)
		self.assertEqual(hist.previous_season, _SEASON_ID)
		self.assertEqual(hist.new_plan, self.plan_new.name)
		self.assertEqual(hist.new_grade, self.grade_new.name)
		self.assertEqual(hist.new_major, self.major_new.name)
		self.assertEqual(hist.new_season, _SEASON_ID)

		# Snapshot wallet values
		self.assertEqual(hist.snapshot_total_xp, 1500)
		self.assertEqual(hist.snapshot_current_streak, 7)
		self.assertEqual(hist.snapshot_total_lessons, 42)
		self.assertEqual(hist.snapshot_total_time_min, 320)

		# Snapshot subscriptions JSON
		subs_snapshot = json.loads(hist.snapshot_subscriptions_json)
		self.assertEqual(len(subs_snapshot), 1)
		self.assertEqual(subs_snapshot[0]["access_key"], "SUB-MATH-101")
		self.assertEqual(subs_snapshot[0]["is_active"], 1)

		# Snapshot progress JSON
		prog_snapshot = json.loads(hist.snapshot_progress_json)
		self.assertEqual(len(prog_snapshot), 1)
		self.assertEqual(prog_snapshot[0]["subject"], subj.name)
		self.assertEqual(prog_snapshot[0]["passed_lessons_bitset"], "FF")

	# ------------------------------------------------------------------
	# 2. Subscriptions deleted after change
	# ------------------------------------------------------------------
	def test_subscriptions_deleted_after_change(self):
		"""All player subscriptions are deleted after a successful plan change."""
		from memora_admin.api.plan_change import execute_plan_change

		_make_subscription(self.player.name, "SUB-SCI-201")
		_make_subscription(self.player.name, "SUB-HIST-301")

		count_before = frappe.db.count("Memora Player Subscription", {"player": self.player.name})
		self.assertEqual(count_before, 2)

		result = execute_plan_change(self.player.name, self.plan_new.name)
		self.assertEqual(result["status"], "ok")

		count_after = frappe.db.count("Memora Player Subscription", {"player": self.player.name})
		self.assertEqual(count_after, 0)

	# ------------------------------------------------------------------
	# 3. Progress records deleted after change
	# ------------------------------------------------------------------
	def test_progress_deleted_after_change(self):
		"""All player structure progress records are deleted after a successful plan change."""
		from memora_admin.api.plan_change import execute_plan_change

		subj1 = _make_subject()
		subj2 = _make_subject()
		_make_progress(self.player.name, subj1.name, bitset="0F", pct=25.0)
		_make_progress(self.player.name, subj2.name, bitset="F0", pct=75.0)

		count_before = frappe.db.count("Memora Structure Progress", {"player": self.player.name})
		self.assertEqual(count_before, 2)

		result = execute_plan_change(self.player.name, self.plan_new.name)
		self.assertEqual(result["status"], "ok")

		count_after = frappe.db.count("Memora Structure Progress", {"player": self.player.name})
		self.assertEqual(count_after, 0)

	# ------------------------------------------------------------------
	# 4. Wallet reset to zero
	# ------------------------------------------------------------------
	def test_wallet_reset_to_zero(self):
		"""Player wallet counters are reset to zero after a successful plan change."""
		from memora_admin.api.plan_change import execute_plan_change

		_set_wallet_values(
			self.player.name,
			total_xp=5000,
			current_streak=14,
			total_lessons=100,
			total_time_min=800,
			daily_xp_json='{"2026-02-25": 200}',
		)

		result = execute_plan_change(self.player.name, self.plan_new.name)
		self.assertEqual(result["status"], "ok")

		wallet = frappe.db.get_value(
			"Memora Player Wallet",
			{"player": self.player.name},
			["total_xp", "current_streak", "total_lessons", "total_time_min", "daily_xp_json"],
			as_dict=True,
		)
		self.assertEqual(wallet.total_xp, 0)
		self.assertEqual(wallet.current_streak, 0)
		self.assertEqual(wallet.total_lessons, 0)
		self.assertEqual(wallet.total_time_min, 0)
		self.assertEqual(wallet.daily_xp_json, "{}")

	# ------------------------------------------------------------------
	# 5. Player profile updated with new plan/grade/major/season
	# ------------------------------------------------------------------
	def test_player_profile_updated(self):
		"""Player profile is updated with the new plan's grade, major, and season."""
		from memora_admin.api.plan_change import execute_plan_change

		result = execute_plan_change(self.player.name, self.plan_new.name)
		self.assertEqual(result["status"], "ok")

		profile = frappe.db.get_value(
			"Memora Player Profile",
			self.player.name,
			["plan", "grade", "major", "season"],
			as_dict=True,
		)
		self.assertEqual(profile.plan, self.plan_new.name)
		self.assertEqual(profile.grade, self.grade_new.name)
		self.assertEqual(profile.major, self.major_new.name)
		self.assertEqual(profile.season, _SEASON_ID)

	# ------------------------------------------------------------------
	# 6. Cooldown enforcement -- second call within 24h returns COOLDOWN_ACTIVE
	# ------------------------------------------------------------------
	def test_cooldown_enforcement(self):
		"""A second plan change within 24 hours is rejected with COOLDOWN_ACTIVE."""
		from memora_admin.api.plan_change import execute_plan_change

		# First change succeeds
		result1 = execute_plan_change(self.player.name, self.plan_new.name)
		self.assertEqual(result1["status"], "ok")

		# Create a third plan so we can attempt another change (player is now on plan_new)
		plan_third = _make_plan(
			season=_SEASON_ID,
			grade=self.grade_old.name,
			major=self.major_old.name,
		)

		# Second change within 24h should fail
		result2 = execute_plan_change(self.player.name, plan_third.name)
		self.assertEqual(result2["status"], "error")
		self.assertEqual(result2["code"], "COOLDOWN_ACTIVE")
		self.assertIn("retry_after", result2)

	# ------------------------------------------------------------------
	# 7. Same-plan rejection returns SAME_PLAN
	# ------------------------------------------------------------------
	def test_same_plan_rejected(self):
		"""Attempting to change to the player's current plan returns SAME_PLAN."""
		from memora_admin.api.plan_change import execute_plan_change

		result = execute_plan_change(self.player.name, self.plan_old.name)
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "SAME_PLAN")
		self.assertIn("message", result)

	# ------------------------------------------------------------------
	# 8. Invalid/unpublished plan returns INVALID_PLAN
	# ------------------------------------------------------------------
	def test_invalid_plan_rejected(self):
		"""An unpublished plan is rejected with INVALID_PLAN."""
		from memora_admin.api.plan_change import execute_plan_change

		unpublished_plan = _make_plan(
			season=_SEASON_ID,
			grade=self.grade_new.name,
			major=self.major_new.name,
			is_published=0,
		)

		result = execute_plan_change(self.player.name, unpublished_plan.name)
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "INVALID_PLAN")

	def test_nonexistent_plan_rejected(self):
		"""A plan ID that does not exist is rejected with INVALID_PLAN."""
		from memora_admin.api.plan_change import execute_plan_change

		result = execute_plan_change(self.player.name, "PLAN-DOES-NOT-EXIST-99999")
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "INVALID_PLAN")

	def test_expired_season_plan_rejected(self):
		"""A plan whose season has expired is rejected with INVALID_PLAN."""
		from memora_admin.api.plan_change import execute_plan_change

		expired_season = _make_expired_season()
		expired_plan = _make_plan(
			season=expired_season.name,
			grade=self.grade_new.name,
			major=self.major_new.name,
		)

		result = execute_plan_change(self.player.name, expired_plan.name)
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "INVALID_PLAN")

	# ------------------------------------------------------------------
	# 9. Trigger reason auto-detected
	# ------------------------------------------------------------------
	def test_trigger_reason_voluntary_change(self):
		"""When the player's current season is still active, trigger_reason is 'Voluntary Change'."""
		from memora_admin.api.plan_change import execute_plan_change

		# Player is on SEAS-00027 which is an active season (end_date >= today)
		result = execute_plan_change(self.player.name, self.plan_new.name)
		self.assertEqual(result["status"], "ok")
		self.assertEqual(result["trigger_reason"], "Voluntary Change")

		hist = frappe.get_doc("Memora Player Plan History", result["history_id"])
		self.assertEqual(hist.trigger_reason, "Voluntary Change")

	def test_trigger_reason_season_expired(self):
		"""When the player's current season has expired, trigger_reason is 'Season Expired'."""
		from memora_admin.api.plan_change import execute_plan_change

		# Create an expired season and put the player on a plan in that season
		expired_season = _make_expired_season()
		expired_plan = _make_plan(
			season=expired_season.name,
			grade=self.grade_old.name,
			major=self.major_old.name,
		)

		# Update the player to be on the expired season's plan
		frappe.db.set_value(
			"Memora Player Profile",
			self.player.name,
			{
				"plan": expired_plan.name,
				"grade": self.grade_old.name,
				"major": self.major_old.name,
				"season": expired_season.name,
			},
		)

		# The new plan is on the active season SEAS-00027
		result = execute_plan_change(self.player.name, self.plan_new.name)
		self.assertEqual(result["status"], "ok")
		self.assertEqual(result["trigger_reason"], "Season Expired")

		hist = frappe.get_doc("Memora Player Plan History", result["history_id"])
		self.assertEqual(hist.trigger_reason, "Season Expired")
