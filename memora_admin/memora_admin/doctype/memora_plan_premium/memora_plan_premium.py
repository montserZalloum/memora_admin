# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraPlanPremium(Document):
	def validate(self):
		self._validate_source_type_fields()

	def _validate_source_type_fields(self):
		"""Ensure required reference fields are set based on source_type."""
		if self.source_type == "purchase" and not self.purchase_ref:
			frappe.throw("Purchase Reference is required when source type is 'purchase'.")
		if self.source_type == "admin" and not self.granted_by:
			frappe.throw("Granted By is required when source type is 'admin'.")
