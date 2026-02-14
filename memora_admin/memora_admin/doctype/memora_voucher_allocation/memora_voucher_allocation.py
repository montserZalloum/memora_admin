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
		self._validate_cards_belong_to_batch()

	def on_update(self):
		if not self.has_value_changed("status"):
			return
		if self.status == "Completed":
			if self.allocation_type == "Allocate":
				self._apply_allocation()
			elif self.allocation_type == "Return":
				self._apply_return()
			self._update_batch_counters()

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

	def _validate_cards_belong_to_batch(self):
		if not self.allocation_cards or not self.batch:
			return
		card_names = [row.voucher_card for row in self.allocation_cards if row.voucher_card]
		if not card_names:
			return
		mismatched = frappe.db.get_all(
			"Memora Voucher Card",
			filters={"name": ["in", card_names], "batch": ["!=", self.batch]},
			pluck="name",
		)
		if mismatched:
			frappe.throw(
				f"Cards {', '.join(mismatched)} do not belong to batch {self.batch}.",
				frappe.ValidationError,
			)

	def _apply_allocation(self):
		"""Bulk-update cards to Allocated status with library, allocation, sale_model.

		Targets status IN ('Available', 'Allocated') to support both fresh allocation
		and re-allocation of cards from another library.
		"""
		card_names = [row.voucher_card for row in self.allocation_cards]
		if not card_names:
			return

		placeholders = ", ".join(["%s"] * len(card_names))
		frappe.db.sql(
			f"""
			UPDATE `tabMemora Voucher Card`
			SET status = 'Allocated', library = %s, allocation = %s, sale_model = %s,
				modified = NOW(), modified_by = %s
			WHERE name IN ({placeholders}) AND status IN ('Available', 'Allocated')
			""",
			[self.customer, self.name, self.sale_model, frappe.session.user] + card_names,
		)

		self._activate_batch_if_needed()

	def _apply_return(self):
		"""Return cards to Available status, clearing library/allocation/sale_model fields.

		Sets return_allocation to this allocation for audit trail.
		Only targets cards with status = 'Allocated'.
		"""
		card_names = [row.voucher_card for row in self.allocation_cards]
		if not card_names:
			return

		placeholders = ", ".join(["%s"] * len(card_names))
		frappe.db.sql(
			f"""
			UPDATE `tabMemora Voucher Card`
			SET status = 'Available', library = NULL, allocation = NULL,
				sale_model = NULL, return_allocation = %s,
				modified = NOW(), modified_by = %s
			WHERE name IN ({placeholders}) AND status = 'Allocated'
			""",
			[self.name, frappe.session.user] + card_names,
		)

	def _update_batch_counters(self):
		"""Recount allocated cards for the batch and update allocated_count."""
		allocated_count = frappe.db.count(
			"Memora Voucher Card", {"batch": self.batch, "status": "Allocated"}
		)
		frappe.db.set_value(
			"Memora Voucher Batch", self.batch, "allocated_count", allocated_count,
			update_modified=True,
		)

	def _activate_batch_if_needed(self):
		"""Transition batch from Generated to Active on first allocation."""
		batch_status = frappe.db.get_value("Memora Voucher Batch", self.batch, "status")
		if batch_status == "Generated":
			frappe.db.set_value(
				"Memora Voucher Batch", self.batch, "status", "Active",
				update_modified=True,
			)
