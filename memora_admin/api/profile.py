"""Profile API for batch profile fetching, memory mastery, and avatar management.

Player identity is PLAYER-##### docname (not email). See Phase 32.

Provides whitelisted APIs for FastAPI:
- Batch profile fetch (cache miss hydration)
- Memory mastery breakdown (FSRS stability classification)
- Avatar update and options (DocType meta-driven validation)

NOTE: get_memory_mastery queries tabMemora Memory State which is a RANGE-partitioned
table designed for 10+ billion rows. It uses raw SQL only (Frappe ORM is forbidden).
All queries include season_seq for partition pruning. See setup.py for details.
"""

import frappe
import redis as _redis

from fastapi_app.core.redis_keys import mastery_key
from memora_admin.api.utils import (
	_MASTERY_COUNTER_TTL,
)
from memora_admin.api.utils import (
	get_player_season_seq as _get_player_season_seq,
)
from memora_admin.utils.redis_connection import get_memora_redis


@frappe.whitelist(allow_guest=False)
def get_player_plan(player_id: str) -> dict:
	"""Get the plan assigned to a player.

	Used by FastAPI to cache player→plan mapping in Redis,
	avoiding repeated 3-table JOINs.

	Args:
		player_id: Player docname (PLAYER-#####).

	Returns:
		Dict with plan (plan docname or None).
	"""
	plan = frappe.db.get_value("Memora Player Profile", player_id, "plan")
	return {"plan": plan}


@frappe.whitelist(allow_guest=False)
def get_plan_season_seq(plan_id: str) -> dict:
	"""Get the season_seq for an academic plan.

	2-table JOIN: Plan → Season. Used by FastAPI to cache
	plan→season_seq mapping in Redis.

	Args:
		plan_id: Academic Plan docname.

	Returns:
		Dict with season_seq (int, defaults to 1).
	"""
	result = frappe.db.sql(
		"""
		SELECT s.season_seq
		FROM `tabMemora Academic Plan` ap
		INNER JOIN `tabMemora Season` s ON s.name = ap.season
		WHERE ap.name = %(plan)s
		LIMIT 1
		""",
		{"plan": plan_id},
	)
	return {"season_seq": int(result[0][0]) if result else 1}


@frappe.whitelist(allow_guest=False)
def get_items_learned_count(
	player_id: str, subject_id: str | None = None, season_seq: int | None = None
) -> dict:
	"""Count Memory State records (items learned) for a player.

	Each Memory State row represents one SRS item the player has encountered.
	Uses season_seq for partition pruning (resolved via player's academic plan).

	Args:
		player_id: Player docname (PLAYER-#####).
		subject_id: Optional subject filter. None or JSON "null" returns all subjects.
		season_seq: Optional pre-resolved season_seq. If None, resolves internally.

	Returns:
		Dict with items_learned count.
	"""
	# Handle JSON "null" string from API calls
	if subject_id in (None, "null", ""):
		subject_id = None

	if season_seq is None:
		season_seq = _get_player_season_seq(player_id)
	else:
		season_seq = int(season_seq)

	subject_filter = "AND subject = %(subject)s" if subject_id else ""

	result = frappe.db.sql(
		f"""
		SELECT COUNT(*) as cnt
		FROM `tabMemora Memory State`
		WHERE player = %(player)s
		  AND season_seq = %(season_seq)s
		{subject_filter}
	""",
		{"player": player_id, "season_seq": season_seq, "subject": subject_id},
	)
	count = int(result[0][0]) if result else 0

	return {"items_learned": count}


@frappe.whitelist(allow_guest=False)
def get_profiles_batch(player_ids: list[str] | str) -> list[dict]:
	"""Batch fetch profiles from Memora Player Profile.

	Used by FastAPI ProfileService to fill cache misses in a single
	database query rather than N+1 queries.

	Args:
		player_ids: List of player docnames (PLAYER-#####) to fetch (or JSON string of list)

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

	# Single query for all profiles (filter by docname, not user field)
	profiles = frappe.get_all(
		"Memora Player Profile",
		filters={"name": ["in", player_ids]},
		fields=["name", "display_name", "avatar"],
	)

	# Transform to expected format
	return [
		{
			"player_id": p.name,
			"display_name": p.display_name or "",
			"avatar": p.avatar or "default_avatar",
		}
		for p in profiles
	]


@frappe.whitelist(allow_guest=False)
def get_memory_mastery(player_id: str, subject_id: str | None = None, season_seq: int | None = None) -> dict:
	"""Get memory mastery breakdown for a player.

	Classifies Memory States into mature/learning based on FSRS stability:
	- Mature: stability >= 21.0 days (high retention confidence)
	- Learning: stability > 0 AND stability < 21.0 days (reviewed but not yet mature)

	Reads from Redis HASH counters first (O(1), sub-millisecond).
	On cache miss, falls back to SQL scan and populates the counters.

	Args:
		player_id: Player docname (PLAYER-#####).
		subject_id: Optional subject filter. None or JSON "null" returns all subjects.
		season_seq: Optional pre-resolved season_seq. If None, resolves internally.

	Returns:
		Dict with mature and learning counts.
	"""
	# Handle JSON "null" string from API calls
	if subject_id in (None, "null", ""):
		subject_id = None

	# Resolve season_seq via player's plan (same approach as reviews.py)
	if season_seq is None:
		season_seq = _get_player_season_seq(player_id)
	else:
		season_seq = int(season_seq)

	# --- Try Redis counters first ---
	try:
		r = get_memora_redis()
		counter_key = mastery_key(player_id, subject_id, season_seq)
		data = r.hgetall(counter_key)
		if data:
			raw_mature = int(data.get(b"mature", 0))
			raw_learning = int(data.get(b"learning", 0))
			# A negative field means the counter drifted (a delta was applied to a
			# resurrected/empty hash). Treat as a cache miss and rebuild from SQL
			# below instead of trusting (and clamping) corrupt counts.
			if raw_mature >= 0 and raw_learning >= 0:
				return {
					"mature": raw_mature,
					"learning": raw_learning,
				}
			r.delete(counter_key)
	except Exception:
		r = None  # Fall through to SQL

	# --- Cache miss: SQL scan (partition-pruned) ---
	subject_filter = "AND subject = %(subject)s" if subject_id else ""

	result = frappe.db.sql(
		f"""
		SELECT
			COALESCE(SUM(CASE WHEN stability >= 21.0 THEN 1 ELSE 0 END), 0) as mature,
			COALESCE(SUM(CASE WHEN stability > 0 AND stability < 21.0 THEN 1 ELSE 0 END), 0) as learning
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

	# --- Populate Redis counters as side effect ---
	try:
		if r is None:
			r = get_memora_redis()
		counter_key = mastery_key(player_id, subject_id, season_seq)
		pipe = r.pipeline(transaction=False)
		pipe.hset(counter_key, mapping={"mature": mature, "learning": learning})
		pipe.expire(counter_key, _MASTERY_COUNTER_TTL)
		# Also populate the "all" aggregate if we queried a specific subject
		if subject_id:
			_populate_all_counter(pipe, r, player_id, season_seq)
		pipe.execute()
	except Exception:
		pass  # Best-effort

	return {
		"mature": mature,
		"learning": learning,
	}


def _populate_all_counter(pipe, r: _redis.Redis, player_id: str, season_seq: int) -> None:
	"""Populate the 'all' aggregate mastery counter from SQL if it doesn't exist."""
	all_key = mastery_key(player_id, None, season_seq)
	if r.exists(all_key):
		return
	row = frappe.db.sql(
		"""
		SELECT
			COALESCE(SUM(CASE WHEN stability >= 21.0 THEN 1 ELSE 0 END), 0) as mature,
			COALESCE(SUM(CASE WHEN stability > 0 AND stability < 21.0 THEN 1 ELSE 0 END), 0) as learning
		FROM `tabMemora Memory State`
		WHERE player = %(player)s
		  AND season_seq = %(season_seq)s
		""",
		{"player": player_id, "season_seq": season_seq},
		as_dict=True,
	)
	if row:
		pipe.hset(
			all_key,
			mapping={
				"mature": int(row[0].get("mature") or 0),
				"learning": int(row[0].get("learning") or 0),
			},
		)
		pipe.expire(all_key, _MASTERY_COUNTER_TTL)


@frappe.whitelist(allow_guest=False)
def get_player_daily_xp_json(player_id: str) -> dict:
	"""Get the persisted daily XP JSON for a player from MariaDB.

	Used by FastAPI as a Phase 3 fallback when Redis daily XP data is lost
	(restart, eviction, or manual flush). Returns the last synced daily XP
	summary so the activity chart can be recovered without a Redis ZSET.

	Args:
		player_id: Player docname (PLAYER-#####).

	Returns:
		Dict with daily_xp_json (JSON string of {date: xp} or "{}").
	"""
	val = frappe.db.get_value(
		"Memora Player Wallet",
		{"player": player_id},
		"daily_xp_json",
	)
	return {"daily_xp_json": val or "{}"}


@frappe.whitelist(allow_guest=False)
def update_player_avatar(player_id: str, avatar: str) -> dict:
	"""Update player's avatar selection.

	Validates avatar against the DocType field options to prevent
	stale hardcoded allow-lists (options are admin-configurable).

	Args:
		player_id: Player docname (PLAYER-#####).
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

	# Player identity is PLAYER-##### docname
	if not frappe.db.exists("Memora Player Profile", player_id):
		frappe.throw(f"Player profile not found for: {player_id}", frappe.DoesNotExistError)
	profile_name = player_id

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
