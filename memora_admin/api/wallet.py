"""Frappe whitelisted API for wallet operations.

Provides wallet data from MariaDB for Redis hydration after cache loss.
"""

from __future__ import annotations

import frappe


@frappe.whitelist(allow_guest=False)
def get_player_wallet(player_id: str) -> dict:
	"""Get wallet data from MariaDB for a player.

	Used by FastAPI WalletService to hydrate Redis wallet after cache flush.

	Args:
		player_id: Player's user ID (email)

	Returns:
		Dict with total_xp and current_streak, or defaults if no wallet exists.
	"""
	wallet = frappe.db.get_value(
		"Memora Player Wallet",
		{"player": player_id},
		["total_xp", "current_streak"],
		as_dict=True,
	)

	if wallet:
		return {
			"total_xp": wallet.total_xp or 0,
			"current_streak": wallet.current_streak or 0,
		}

	return {"total_xp": 0, "current_streak": 0}
