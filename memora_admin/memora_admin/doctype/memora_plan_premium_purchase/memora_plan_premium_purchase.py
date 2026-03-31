# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraPlanPremiumPurchase(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("PPP-.#####.")

	def validate(self):
		self._validate_no_duplicate_pending()

	def _validate_no_duplicate_pending(self):
		"""Reject creation if player already has a pending purchase for this plan."""
		if not self.is_new():
			return
		existing = frappe.db.exists(
			"Memora Plan Premium Purchase",
			{"player": self.player, "plan": self.plan, "status": "pending", "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(f"Player {self.player} already has a pending purchase for plan {self.plan}.")
