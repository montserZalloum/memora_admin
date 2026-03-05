"""Frappe API for subscription-related operations.

Player identity is PLAYER-##### docname (not email). See Phase 32.
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate


@frappe.whitelist(allow_guest=False)
def create_subscription(
	player_id: str,
	access_key: str,
	expires_at: str,
	transaction_id: str | None = None,
) -> dict:
	"""
	Create Memora Player Subscription record.

	This triggers doc_events hook which syncs grant to Redis.

	Args:
	    player_id: Memora Player Profile name
	    access_key: Access key (e.g., "SUB-MATH")
	    expires_at: Expiration date in ISO format
	    transaction_id: Optional transaction reference

	Returns:
	    dict with subscription name and created status
	"""
	# Check if subscription already exists
	existing = frappe.db.exists("Memora Player Subscription", {"player": player_id, "access_key": access_key})

	if existing:
		frappe.logger().info(f"Subscription exists: {player_id} -> {access_key}")
		return {
			"name": existing,
			"created": False,
			"message": "Subscription already exists",
		}

	# Create new subscription
	doc = frappe.get_doc(
		{
			"doctype": "Memora Player Subscription",
			"player": player_id,
			"access_key": access_key,
			"expires_at": getdate(expires_at),
			"is_active": 1,
		}
	)
	doc.insert()

	frappe.logger().info(f"Subscription created: {doc.name} ({player_id} -> {access_key})")

	return {
		"name": doc.name,
		"created": True,
		"message": "Subscription created successfully",
	}


@frappe.whitelist(allow_guest=False)
def get_player_access_keys(player_id: str) -> list[str]:
	"""Get all active access keys for a player from MariaDB.

	Used by FastAPI AccessService to hydrate Redis access set after cache flush.
	Mirrors the pattern in wallet.get_player_wallet() for wallet hydration.

	Args:
	    player_id: Player docname (PLAYER-#####) from JWT sub claim

	Returns:
	    List of active access keys (e.g., ["SUB-SUBJ-00028", "TRK-MATH-01"])
	"""
	# Player identity is PLAYER-##### docname (from JWT sub)
	keys = frappe.get_all(
		"Memora Player Subscription",
		filters={"player": player_id, "is_active": 1},
		pluck="access_key",
	)
	return keys or []


@frappe.whitelist(allow_guest=False)
def get_plan_free_subjects(plan_id: str) -> list[str]:
	"""Get non-premium subject IDs for a plan from MariaDB.

	Used by FastAPI AccessService to hydrate plan free_subjects set after cache flush.

	Args:
	    plan_id: Plan docname (e.g., "PLAN-00572")

	Returns:
	    List of subject IDs that are non-premium in this plan
	"""
	return (
		frappe.get_all(
			"Memora Plan Subject",
			filters={"parent": plan_id, "is_premium": 0},
			pluck="subject",
		)
		or []
	)


@frappe.whitelist(allow_guest=False)
def get_season_data(season_id: str) -> dict | None:
	"""Get season metadata from MariaDB for cache hydration.

	Used by FastAPI SeasonService to hydrate Redis after cache flush.

	Args:
	    season_id: Season docname (e.g., "SEAS-00635")

	Returns:
	    Dict with season fields, or None if not found
	"""
	season = frappe.db.get_value(
		"Memora Season",
		season_id,
		["is_published", "start_date", "end_date", "season_seq"],
		as_dict=True,
	)
	if not season:
		return None
	return {
		"is_published": bool(season.is_published),
		"start_date": str(season.start_date),
		"end_date": str(season.end_date),
		"season_seq": str(season.season_seq),
	}


@frappe.whitelist(allow_guest=False)
def get_player_progress(player_id: str, subject_id: str) -> dict | None:
	"""Get player's progress bitmap from MariaDB.

	Used by FastAPI ProgressService to hydrate Redis bitmap after cache flush.

	Args:
	    player_id: Player docname (PLAYER-#####) from JWT sub claim
	    subject_id: Subject identifier (e.g., "SUBJ-00028")

	Returns:
	    Dict with hex bitset string, or None if no progress exists
	    Example: {"bitset": "80", "completion_percentage": 33.33}
	"""
	# Player identity is PLAYER-##### docname (from JWT sub)
	progress = frappe.db.get_value(
		"Memora Structure Progress",
		{"player": player_id, "subject": subject_id},
		["passed_lessons_bitset", "completion_percentage"],
		as_dict=True,
	)
	return progress
