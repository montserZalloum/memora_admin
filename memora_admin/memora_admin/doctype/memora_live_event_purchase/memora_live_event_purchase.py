# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraLiveEventPurchase(Document):
	def validate(self):
		self._validate_no_duplicate_pending()

	def _validate_no_duplicate_pending(self):
		if not self.is_new():
			return
		existing = frappe.db.exists(
			"Memora Live Event Purchase",
			{"player": self.player, "event": self.event, "status": "pending", "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(f"Player {self.player} already has a pending purchase for event {self.event}.")
