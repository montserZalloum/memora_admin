"""Profile API for batch profile fetching, memory mastery, and avatar management.

Provides whitelisted APIs for FastAPI:
- Batch profile fetch (cache miss hydration)
- Memory mastery breakdown (FSRS stability classification)
- Avatar update and options (DocType meta-driven validation)

NOTE: get_memory_mastery queries tabMemora Memory State which is a RANGE-partitioned
table designed for 10+ billion rows. It uses raw SQL only (Frappe ORM is forbidden).
All queries include season_seq for partition pruning. See setup.py for details.
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


@frappe.whitelist(allow_guest=False)
def get_memory_mastery(player_id: str, subject_id: str | None = None) -> dict:
	"""Get memory mastery breakdown for a player.

	Classifies Memory States into mature/learning/new based on FSRS stability:
	- Mature: stability >= 21.0 days (high retention confidence)
	- Learning: 0 < stability < 21.0 days (reviewed but not yet mature)
	- New: stability == 0 (initial FSRS state, first review)

	Counts items (each Memory State row = 1 item) with season_seq for partition pruning.

	Args:
		player_id: User identifier (email).
		subject_id: Optional subject filter. None or JSON "null" returns all subjects.

	Returns:
		Dict with mature, learning, new_items, total counts.
	"""
	# Handle JSON "null" string from API calls
	if subject_id in (None, "null", ""):
		subject_id = None

	# Get active season seq for partition pruning
	from datetime import date as date_type

	today = date_type.today()
	season_seq = frappe.db.get_value(
		"Memora Season",
		{"is_published": 1, "start_date": ["<=", today], "end_date": [">=", today]},
		"season_seq",
	)
	season_seq = int(season_seq) if season_seq else 1

	subject_filter = "AND subject = %(subject)s" if subject_id else ""

	result = frappe.db.sql(
		f"""
		SELECT
			COALESCE(SUM(CASE WHEN stability >= 21.0 THEN 1 ELSE 0 END), 0) as mature,
			COALESCE(SUM(CASE WHEN stability > 0 AND stability < 21.0 THEN 1 ELSE 0 END), 0) as learning,
			COALESCE(SUM(CASE WHEN stability = 0 THEN 1 ELSE 0 END), 0) as new_items
		FROM `tabMemora Memory State`
		WHERE player = %(player)s
		  AND season_seq = %(season_seq)s
		{subject_filter}
	""",
		{"player": player_id, "season_seq": season_seq, "subject": subject_id},
		as_dict=True,
	)

	row = result[0] if result else {}
	mature = int(row.get("mature") or 0)
	learning = int(row.get("learning") or 0)
	new_items = int(row.get("new_items") or 0)

	return {
		"mature": mature,
		"learning": learning,
		"new_items": new_items,
		"total": mature + learning + new_items,
	}


@frappe.whitelist(allow_guest=False)
def update_player_avatar(player_id: str, avatar: str) -> dict:
	"""Update player's avatar selection.

	Validates avatar against the DocType field options to prevent
	stale hardcoded allow-lists (options are admin-configurable).

	Args:
		player_id: User identifier (email).
		avatar: Avatar identifier from the predefined options list.

	Returns:
		Dict with updated avatar and success status.

	Raises:
		frappe.ValidationError: If avatar is not in valid options.
		frappe.DoesNotExistError: If player profile not found.
	"""
	# Validate avatar against DocType field options
	valid_options = _get_avatar_options_from_meta()
	if valid_options and avatar not in valid_options:
		frappe.throw(f"Invalid avatar option: {avatar}", frappe.ValidationError)

	# Look up profile by user field (not doctype name)
	profile_name = frappe.get_value("Memora Player Profile", {"user": player_id}, "name")
	if not profile_name:
		frappe.throw(f"Player profile not found for: {player_id}", frappe.DoesNotExistError)

	frappe.db.set_value("Memora Player Profile", profile_name, "avatar", avatar)
	frappe.db.commit()

	return {"avatar": avatar, "success": True}


def _get_avatar_options_from_meta() -> list[str]:
	"""Read valid avatar options from DocType field definition.

	Internal helper used by update_player_avatar.
	"""
	meta = frappe.get_meta("Memora Player Profile")
	avatar_field = meta.get_field("avatar")
	if not avatar_field or not avatar_field.options:
		return []
	return [opt.strip() for opt in avatar_field.options.split("\n") if opt.strip()]
