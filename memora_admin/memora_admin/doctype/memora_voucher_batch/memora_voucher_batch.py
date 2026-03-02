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
		self._validate_batch_purpose()
		self._validate_batch_purpose_immutable()

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

	def _validate_batch_purpose(self):
		if self.batch_purpose and self.batch_purpose != "Sale" and (self.face_value or 0) > 0:
			frappe.throw(
				"Non-sale batches must have zero face value.",
				frappe.ValidationError,
			)

	def _validate_batch_purpose_immutable(self):
		if self.is_new() or not self.has_value_changed("batch_purpose"):
			return
		old_doc = self.get_doc_before_save()
		if old_doc and old_doc.status != "Draft":
			frappe.throw(
				"Batch purpose cannot be changed after Draft status.",
				frappe.ValidationError,
			)
