# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MemoraPlayerPlanHistory(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("PLHIST-.#####.")
