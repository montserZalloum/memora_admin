# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraLiveEventAccess(Document):
	def validate(self):
		self._validate_access_type_fields()

	def _validate_access_type_fields(self):
		"""Ensure required reference fields are set based on access_type."""
		if self.access_type == "purchase" and not self.purchase_ref:
			frappe.throw("Purchase Reference is required when access type is 'purchase'.")
		if self.access_type == "voucher" and not self.voucher_ref:
			frappe.throw("Voucher Reference is required when access type is 'voucher'.")
		if self.access_type == "admin" and not self.granted_by:
			frappe.throw("Granted By is required when access type is 'admin'.")
