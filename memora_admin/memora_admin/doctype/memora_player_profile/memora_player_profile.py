# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraPlayerProfile(Document):
	def after_insert(self):
		"""Auto-create Player Wallet when new player profile is created."""
		self._create_player_wallet()

	def _create_player_wallet(self):
		"""Create a Player Wallet record for this player."""
		# Check if wallet already exists (safety check)
		existing = frappe.db.get_value("Memora Player Wallet", {"player": self.name}, "name")
		if existing:
			return

		# Create new wallet
		wallet = frappe.get_doc(
			{
				"doctype": "Memora Player Wallet",
				"player": self.name,
				"total_xp": 0,
				"current_streak": 0,
				"dirty_flag": 0,
				"status": "Active",
				"total_lessons": 0,
				"total_time_min": 0,
			}
		)
		wallet.insert(ignore_permissions=True)
		frappe.msgprint(f"Created wallet {wallet.name} for player {self.name}")
