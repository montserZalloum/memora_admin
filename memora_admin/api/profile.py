"""Profile API for batch profile fetching.

Provides whitelisted API for FastAPI to fetch profiles on cache miss.
Per RESEARCH.md: Use frappe.get_all with filters for batch query.
"""

import frappe


@frappe.whitelist(allow_guest=False)
def get_profiles_batch(player_ids: list[str] | str) -> list[dict]:
	"""Batch fetch profiles from Memora Player Profile.

	Used by FastAPI ProfileService to fill cache misses in a single
	database query rather than N+1 queries.

	Args:
		player_ids: List of user IDs to fetch (or JSON string of list)

	Returns:
		List of profile dicts with player_id, display_name, avatar.
		Missing profiles are not included in result (caller applies fallback).
	"""
	# Handle JSON string input (Frappe API often receives strings)
	if isinstance(player_ids, str):
		import json
		try:
			player_ids = json.loads(player_ids)
		except json.JSONDecodeError:
			# Single player_id passed as string
			player_ids = [player_ids]

	if not player_ids:
		return []

	# Single query for all profiles
	profiles = frappe.get_all(
		"Memora Player Profile",
		filters={"user": ["in", player_ids]},
		fields=["user", "display_name", "avatar"],
	)

	# Transform to expected format
	return [
		{
			"player_id": p.user,
			"display_name": p.display_name or "",
			"avatar": p.avatar or "default_avatar",
		}
		for p in profiles
	]
