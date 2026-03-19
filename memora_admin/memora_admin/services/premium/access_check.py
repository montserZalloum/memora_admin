# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Centralized premium usability check (FR-003).

Evaluates computed validity at query time — premium has NO stored expiry.
Used by:
- Frappe event handlers (premium_sync.py) to populate Redis cache
- Admin API (grant/revoke) for pre-condition checks
- Any Frappe-side code that needs to check premium usability
"""

from dataclasses import dataclass
from datetime import date

import frappe
from frappe.utils import getdate, nowdate


@dataclass
class PremiumCheckResult:
	"""Result of a premium usability check."""

	usable: bool
	reason: str  # none | plan_mismatch | season_ended | revoked | no_premium
	premium_id: str | None = None
	season_end: date | None = None
	source_type: str | None = None


def is_plan_premium_usable(player: str, plan: str) -> PremiumCheckResult:
	"""Check if a player has a usable premium for a given plan.

	Computed validity logic (data-model.md):
	  usable = premium.status == 'active'
	           AND premium.plan == player.plan
	           AND NOW() <= season.end_date

	Args:
		player: Memora Player Profile name
		plan: Memora Academic Plan name

	Returns:
		PremiumCheckResult with usable flag and reason
	"""
	# Find active premium for this player and plan
	premium = frappe.db.get_value(
		"Memora Plan Premium",
		{"player": player, "plan": plan, "status": "active"},
		["name", "season", "source_type", "plan"],
		as_dict=True,
	)

	if not premium:
		return PremiumCheckResult(usable=False, reason="no_premium")

	# Check plan matches player's current plan
	current_plan = frappe.db.get_value("Memora Player Profile", player, "plan")
	if current_plan != plan:
		season_end = _get_season_end(premium.season)
		return PremiumCheckResult(
			usable=False,
			reason="plan_mismatch",
			premium_id=premium.name,
			season_end=season_end,
			source_type=premium.source_type,
		)

	# Check season hasn't ended
	season_end = _get_season_end(premium.season)
	if season_end and getdate(nowdate()) > season_end:
		return PremiumCheckResult(
			usable=False,
			reason="season_ended",
			premium_id=premium.name,
			season_end=season_end,
			source_type=premium.source_type,
		)

	return PremiumCheckResult(
		usable=True,
		reason="none",
		premium_id=premium.name,
		season_end=season_end,
		source_type=premium.source_type,
	)


@frappe.whitelist(methods=["POST"])
def check_premium_api(player: str, plan: str) -> dict:
	"""Whitelisted API for FastAPI hydration. Returns dict for JSON serialization."""
	result = is_plan_premium_usable(player, plan)
	return {
		"usable": result.usable,
		"reason": result.reason,
		"premium_id": result.premium_id,
		"season_end": str(result.season_end) if result.season_end else None,
		"source_type": result.source_type,
	}


def _get_season_end(season: str) -> date | None:
	"""Get the end_date for a season."""
	if not season:
		return None
	end_date = frappe.db.get_value("Memora Season", season, "end_date")
	return getdate(end_date) if end_date else None
