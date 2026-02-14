# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

VALID_TRANSITIONS = {
	"Draft": {"Generated"},
	"Generated": {"Active", "Closed"},
	"Active": {"Closed"},
	"Closed": set(),  # Terminal
}


class MemoraVoucherBatch(Document):
	def validate(self):
		self._validate_status_transition()
		self._validate_pin_length()

	def _validate_status_transition(self):
		if not self.is_new() and self.has_value_changed("status"):
			old_doc = self.get_doc_before_save()
			if old_doc:
				old_status = old_doc.status
				allowed = VALID_TRANSITIONS.get(old_status, set())
				if self.status not in allowed:
					frappe.throw(
						f"Cannot change batch status from {old_status} to {self.status}. "
						f"Allowed transitions: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}",
						frappe.ValidationError,
					)

	def _validate_pin_length(self):
		if self.pin_length and str(self.pin_length) not in ("12", "14", "16"):
			frappe.throw(
				"PIN Length must be 12, 14, or 16",
				frappe.ValidationError,
			)
