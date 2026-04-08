# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraGrantComponent(Document):
	def validate(self):
		if not self.key_type:
			self.key_type = "full"

		if self.key_type == "practice" and self.target_doctype != "Memora Subject":
			frappe.throw(
				"Practice-only grants are only valid for Memora Subject, not Memora Track.",
				title="Invalid Grant Component",
			)
