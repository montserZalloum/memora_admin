# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraAccessVoucher(Document):
	def validate(self):
		self._validate_target_fields()

	def _validate_target_fields(self):
		if self.voucher_type == "plan_premium" and not self.target_plan:
			frappe.throw("Target Plan is required when voucher type is 'plan_premium'.")
		if self.voucher_type == "live_event_access" and not self.target_event:
			frappe.throw("Target Event is required when voucher type is 'live_event_access'.")
