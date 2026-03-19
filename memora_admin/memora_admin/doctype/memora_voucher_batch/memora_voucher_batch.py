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
		self._validate_grant_type_immutable()
		self._validate_grant_type_fields()

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

	def _validate_grant_type_immutable(self):
		if self.is_new() or not self.has_value_changed("grant_type"):
			return
		old_doc = self.get_doc_before_save()
		if old_doc and old_doc.status != "Draft":
			frappe.throw(
				"Grant type cannot be changed after Draft status.",
				frappe.ValidationError,
			)

	def _validate_grant_type_fields(self):
		grant_type = self.grant_type or "product_grant"
		if grant_type == "product_grant":
			if not self.batch_grants or len(self.batch_grants) == 0:
				frappe.throw(
					"Product Grants table must not be empty for product_grant batches.",
					frappe.ValidationError,
				)
		elif grant_type == "live_event_access":
			if not self.target_event:
				frappe.throw(
					"Target Event is required for live_event_access batches.",
					frappe.ValidationError,
				)
			event_status = frappe.db.get_value(
				"Memora Live Challenge Event", self.target_event, "status"
			)
			if not event_status:
				frappe.throw(
					f"Target Event {self.target_event} does not exist.",
					frappe.ValidationError,
				)
			if event_status == "Ended":
				frappe.throw(
					"Target Event has already ended.",
					frappe.ValidationError,
				)
