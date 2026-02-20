"""Shared utilities for Frappe-side API modules."""

from __future__ import annotations

import frappe

_SEASON_SEQ_CACHE_TTL = 86400  # 24 hours


def get_player_season_seq(player_id: str) -> int:
	"""Get the season_seq for a player's plan, with 24h cache.

	Resolves: Player Profile -> Academic Plan -> Season -> season_seq.
	Falls back to 1 if player has no plan/season assigned.

	Cached via frappe.cache() with 24h TTL. Invalidated by
	plan_change_sync.on_player_profile_plan_changed when admin
	changes a player's plan.
	"""
	cache_key = f"player_season_seq:{player_id}"
	cached = frappe.cache().get_value(cache_key, expires=True)
	if cached is not None:
		return int(cached)

	result = frappe.db.sql(
		"""
		SELECT s.season_seq
		FROM `tabMemora Player Profile` pp
		INNER JOIN `tabMemora Academic Plan` ap ON ap.name = pp.plan
		INNER JOIN `tabMemora Season` s ON s.name = ap.season
		WHERE pp.name = %(player)s
		LIMIT 1
		""",
		{"player": player_id},
	)
	value = int(result[0][0]) if result else 1

	frappe.cache().set_value(cache_key, value, expires_in_sec=_SEASON_SEQ_CACHE_TTL)
	return value


def invalidate_player_season_seq(player_id: str) -> None:
	"""Clear cached season_seq for a player. Call on plan change."""
	frappe.cache().delete_value(f"player_season_seq:{player_id}")
