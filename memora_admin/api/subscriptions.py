"""Frappe API for subscription-related operations."""

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

	The player field in Memora Player Subscription links to a Player Profile,
	but may also contain the user ID directly. We check both to be safe.

	Args:
	    player_id: Player's user ID (email) or Player Profile name

	Returns:
	    List of active access keys (e.g., ["SUB-SUBJ-00028", "TRK-MATH-01"])
	"""
	# Try direct match first (player field = user_id)
	keys = frappe.get_all(
		"Memora Player Subscription",
		filters={"player": player_id, "is_active": 1},
		pluck="access_key",
	)

	if keys:
		return keys

	# If no direct match, try looking up via Player Profile
	# (player field may be Profile name, not user_id)
	profile_name = frappe.db.get_value(
		"Memora Player Profile",
		{"user": player_id},
		"name",
	)

	if profile_name and profile_name != player_id:
		keys = frappe.get_all(
			"Memora Player Subscription",
			filters={"player": profile_name, "is_active": 1},
			pluck="access_key",
		)

	return keys or []


@frappe.whitelist(allow_guest=False)
def get_player_progress(player_id: str, subject_id: str) -> dict | None:
	"""Get player's progress bitmap from MariaDB.

	Used by FastAPI ProgressService to hydrate Redis bitmap after cache flush.
	Follows the same pattern as get_player_access_keys() for access hydration.

	Args:
	    player_id: Player's user ID (email) or Player Profile name
	    subject_id: Subject identifier (e.g., "SUBJ-00028")

	Returns:
	    Dict with hex bitset string, or None if no progress exists
	    Example: {"bitset": "80", "completion_percentage": 33.33}
	"""
	# Try direct match first (player field = user_id)
	progress = frappe.db.get_value(
		"Memora Structure Progress",
		{"player": player_id, "subject": subject_id},
		["passed_lessons_bitset", "completion_percentage"],
		as_dict=True,
	)

	if progress:
		return progress

	# If no direct match, try looking up via Player Profile
	profile_name = frappe.db.get_value(
		"Memora Player Profile",
		{"user": player_id},
		"name",
	)

	if profile_name and profile_name != player_id:
		progress = frappe.db.get_value(
			"Memora Structure Progress",
			{"player": profile_name, "subject": subject_id},
			["passed_lessons_bitset", "completion_percentage"],
			as_dict=True,
		)

	return progress
