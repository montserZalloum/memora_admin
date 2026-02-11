# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraSeason(Document):
	def before_insert(self):
		if not self.season_seq:
			max_seq = frappe.db.sql(
				"SELECT COALESCE(MAX(season_seq), 0) FROM `tabMemora Season`"
			)[0][0]
			self.season_seq = int(max_seq) + 1
