"""Frappe whitelisted API for plan change operations.

Provides:
- execute_plan_change(): Atomic plan change with snapshot, cleanup, and profile update
- get_available_plans(): Browse eligible plans for switching
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import frappe


@frappe.whitelist(allow_guest=False)
def execute_plan_change(player_id: str, new_plan_id: str) -> dict:
	"""Execute a complete plan change with clean slate.

	Single atomic transaction:
	1. Validate cooldown (24h between changes)
	2. Validate plan eligibility (published + active season)
	3. Reject same-plan
	4. Auto-detect trigger reason (Season Expired vs Voluntary Change)
	5. Snapshot wallet + subscriptions + progress + memory state count
	6. Insert Memora Player Plan History record
	7. Delete all subscriptions for player
	8. Delete all progress for player
	8b. Delete all Memory State records for current season (FR-024)
	9. Reset wallet to zero
	10. Update player profile with new plan/grade/major/season

	Args:
		player_id: Player profile ID (e.g., "PLAYER-00001")
		new_plan_id: Target plan ID (e.g., "PLAN-00042")

	Returns:
		On success: {"status": "ok", "history_id": ..., "previous_plan": ..., "trigger_reason": ...}
		On error: {"status": "error", "code": ..., "message": ..., "retry_after": ...}
	"""
	# --- Validation ---

	# 1. Check cooldown via latest history record
	last_change = frappe.db.sql(
		"""SELECT changed_at FROM `tabMemora Player Plan History`
		WHERE player = %s ORDER BY changed_at DESC LIMIT 1""",
		(player_id,),
		as_dict=True,
	)
	if last_change:
		changed_at = last_change[0]["changed_at"]
		if isinstance(changed_at, str):
			changed_at = datetime.fromisoformat(changed_at)
		cooldown_end = changed_at + timedelta(hours=24)
		if datetime.now() < cooldown_end:
			return {
				"status": "error",
				"code": "COOLDOWN_ACTIVE",
				"message": "You can change your plan again after the cooldown period.",
				"retry_after": cooldown_end.isoformat(),
			}

	# 2. Validate plan eligibility (published + active season)
	plan = frappe.db.get_value(
		"Memora Academic Plan",
		new_plan_id,
		["name", "plan_name", "grade", "major", "season", "is_published"],
		as_dict=True,
	)
	if not plan or not plan.is_published:
		return {
			"status": "error",
			"code": "INVALID_PLAN",
			"message": "The selected plan is not available.",
		}

	# Check season is active (end_date >= today)
	season = frappe.db.get_value(
		"Memora Season",
		plan.season,
		["name", "season_title", "end_date", "is_published"],
		as_dict=True,
	)
	if not season or not season.is_published:
		return {
			"status": "error",
			"code": "INVALID_PLAN",
			"message": "The selected plan is not available (season inactive).",
		}

	from frappe.utils import today

	if str(season.end_date) < today():
		return {
			"status": "error",
			"code": "INVALID_PLAN",
			"message": "The selected plan is not available (season expired).",
		}

	# 3. Reject same-plan
	player = frappe.db.get_value(
		"Memora Player Profile",
		player_id,
		["name", "plan", "grade", "major", "season"],
		as_dict=True,
	)
	if not player:
		return {
			"status": "error",
			"code": "INVALID_PLAYER",
			"message": "Player not found.",
		}

	if player.plan == new_plan_id:
		return {
			"status": "error",
			"code": "SAME_PLAN",
			"message": "You are already on this plan.",
		}

	# 4. Auto-detect trigger reason (FR-018)
	current_season = frappe.db.get_value(
		"Memora Season",
		player.season,
		["end_date"],
		as_dict=True,
	)
	if current_season and str(current_season.end_date) < today():
		trigger_reason = "Season Expired"
	else:
		trigger_reason = "Voluntary Change"

	# --- Snapshot ---

	# 5. Snapshot wallet
	wallet = frappe.db.get_value(
		"Memora Player Wallet",
		{"player": player_id},
		["name", "total_xp", "current_streak", "total_lessons", "total_time_min"],
		as_dict=True,
	)
	snapshot_total_xp = (wallet.total_xp or 0) if wallet else 0
	snapshot_current_streak = (wallet.current_streak or 0) if wallet else 0
	snapshot_total_lessons = (wallet.total_lessons or 0) if wallet else 0
	snapshot_total_time_min = (wallet.total_time_min or 0) if wallet else 0

	# Snapshot subscriptions
	subscriptions = frappe.db.sql(
		"""SELECT access_key, expires_at, is_active
		FROM `tabMemora Player Subscription` WHERE player = %s""",
		(player_id,),
		as_dict=True,
	)
	snapshot_subscriptions = [
		{
			"access_key": sub.access_key,
			"expires_at": str(sub.expires_at) if sub.expires_at else None,
			"is_active": sub.is_active,
		}
		for sub in subscriptions
	]

	# Snapshot memory state count (before deletion)
	current_season_seq = frappe.db.get_value("Memora Season", player.season, "season_seq")
	snapshot_memory_states = 0
	if current_season_seq:
		ms_count = frappe.db.sql(
			"""SELECT COUNT(*) as cnt FROM `tabMemora Memory State`
			WHERE player = %s AND season_seq = %s""",
			(player_id, current_season_seq),
		)
		snapshot_memory_states = int(ms_count[0][0]) if ms_count else 0

	# Snapshot progress
	progress_records = frappe.db.sql(
		"""SELECT subject, passed_lessons_bitset, completion_percentage
		FROM `tabMemora Structure Progress` WHERE player = %s""",
		(player_id,),
		as_dict=True,
	)
	snapshot_progress = [
		{
			"subject": p.subject,
			"passed_lessons_bitset": p.passed_lessons_bitset,
			"completion_percentage": p.completion_percentage,
		}
		for p in progress_records
	]

	# --- Mutate ---

	now = datetime.now().replace(tzinfo=None)

	# 6. Insert history record
	history = frappe.get_doc({
		"doctype": "Memora Player Plan History",
		"player": player_id,
		"previous_plan": player.plan,
		"previous_grade": player.grade,
		"previous_major": player.major,
		"previous_season": player.season,
		"new_plan": new_plan_id,
		"new_grade": plan.grade,
		"new_major": plan.major,
		"new_season": plan.season,
		"trigger_reason": trigger_reason,
		"snapshot_total_xp": snapshot_total_xp,
		"snapshot_current_streak": snapshot_current_streak,
		"snapshot_total_lessons": snapshot_total_lessons,
		"snapshot_total_time_min": snapshot_total_time_min,
		"snapshot_subscriptions_json": json.dumps(snapshot_subscriptions),
		"snapshot_progress_json": json.dumps(snapshot_progress),
		"snapshot_memory_states": snapshot_memory_states,
		"changed_at": now,
	})
	history.insert(ignore_permissions=True)

	# 7. Delete all subscriptions
	frappe.db.delete("Memora Player Subscription", {"player": player_id})

	# 8. Delete all progress
	frappe.db.delete("Memora Structure Progress", {"player": player_id})

	# 8b. Delete all Memory State records for current season (FR-024)
	if current_season_seq:
		frappe.db.sql(
			"""DELETE FROM `tabMemora Memory State`
			WHERE player = %s AND season_seq = %s""",
			(player_id, current_season_seq),
		)

	# 9. Reset wallet
	if wallet:
		frappe.db.set_value(
			"Memora Player Wallet",
			wallet.name,
			{
				"total_xp": 0,
				"current_streak": 0,
				"total_lessons": 0,
				"total_time_min": 0,
				"daily_xp_json": "{}",
				"dirty_flag": 0,
				"last_sync_at": None,
			},
			update_modified=False,
		)

	# 10. Update player profile (FR-023: derive grade/major/season from plan)
	frappe.db.set_value(
		"Memora Player Profile",
		player_id,
		{
			"plan": new_plan_id,
			"grade": plan.grade,
			"major": plan.major,
			"season": plan.season,
		},
	)

	return {
		"status": "ok",
		"history_id": history.name,
		"previous_plan": player.plan,
		"trigger_reason": trigger_reason,
	}


@frappe.whitelist(allow_guest=False)
def get_available_plans(current_plan_id: str) -> dict:
	"""Get plans available for switching.

	Returns plans linked to active seasons (published, end_date >= today),
	excluding the player's current plan.

	Args:
		current_plan_id: Player's current plan ID

	Returns:
		{"plans": [{"name", "plan_name", "grade", "grade_name", "major", "major_name", "season", "season_title"}]}
	"""
	from frappe.utils import today

	plans = frappe.db.sql(
		"""
		SELECT ap.name, ap.plan_name, ap.grade, ap.major, ap.season,
			g.grade_title AS grade_name, m.major_title AS major_name, s.season_title
		FROM `tabMemora Academic Plan` ap
		INNER JOIN `tabMemora Season` s ON s.name = ap.season
		LEFT JOIN `tabMemora Grade` g ON g.name = ap.grade
		LEFT JOIN `tabMemora Major` m ON m.name = ap.major
		WHERE ap.is_published = 1
			AND s.is_published = 1
			AND s.end_date >= %(today)s
			AND ap.name != %(current_plan)s
		ORDER BY g.grade_title, m.major_title, ap.plan_name
		""",
		{"today": today(), "current_plan": current_plan_id},
		as_dict=True,
	)

	return {
		"plans": [
			{
				"name": p.name,
				"plan_name": p.plan_name,
				"grade": p.grade,
				"grade_name": p.grade_name,
				"major": p.major,
				"major_name": p.major_name,
				"season": p.season,
				"season_title": p.season_title,
			}
			for p in plans
		]
	}
