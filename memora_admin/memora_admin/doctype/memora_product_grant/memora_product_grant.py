# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraProductGrant(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("GRNT-.#####.")

	def validate(self):
		for row in self.grant_components:
			if not row.key_type:
				row.key_type = "normal content"

			if row.target_doctype == "Memora Track":
				# key_type is hidden for Track — force it silently regardless of stale value
				row.key_type = "normal content"

			elif row.target_doctype == "Memora Subject":
				if row.key_type not in ("normal content", "practice"):
					frappe.throw(
						f"Row {row.idx}: Key type '{row.key_type}' is not valid for Memora Subject. "
						"Allowed: normal content, practice.",
						title="Invalid Grant Component",
					)

			elif row.target_doctype == "Memora Academic Plan":
				if row.key_type != "exam":
					frappe.throw(
						f"Row {row.idx}: Only 'exam' key type is valid for Memora Academic Plan.",
						title="Invalid Grant Component",
					)
