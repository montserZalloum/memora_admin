# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

from datetime import timedelta

import frappe
from frappe.model.document import Document


class MemoraLiveEventPurchase(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("LEP-.#####.")

	def before_insert(self):
		if self.status == "pending" and not self.expires_at:
			self.expires_at = frappe.utils.now_datetime() + timedelta(minutes=30)

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
