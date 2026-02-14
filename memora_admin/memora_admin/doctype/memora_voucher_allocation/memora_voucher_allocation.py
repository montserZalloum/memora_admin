# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

VALID_TRANSITIONS = {
	"Draft": {"Pending Approval", "Approved", "Cancelled"},
	"Pending Approval": {"Approved", "Rejected"},
	"Approved": {"Completed"},
	"Rejected": set(),     # Terminal
	"Completed": set(),    # Terminal
	"Cancelled": set(),    # Terminal
}


class MemoraVoucherAllocation(Document):
	def validate(self):
		self._validate_status_transition()
		self._update_quantity()

	def _validate_status_transition(self):
		if not self.is_new() and self.has_value_changed("status"):
			old_doc = self.get_doc_before_save()
			if old_doc:
				old_status = old_doc.status
				allowed = VALID_TRANSITIONS.get(old_status, set())
				if self.status not in allowed:
					frappe.throw(
						f"Invalid allocation status transition: {old_status} -> {self.status}. "
						f"Allowed: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}",
						frappe.ValidationError,
					)

	def _update_quantity(self):
		self.quantity = len(self.allocation_cards) if self.allocation_cards else 0
