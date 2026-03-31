# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

VALID_TRANSITIONS = {
	"Draft": {"Pending Approval", "Approved", "Cancelled"},
	"Pending Approval": {"Approved", "Rejected"},
	"Approved": {"Completed"},
	"Rejected": set(),  # Terminal
	"Completed": set(),  # Terminal
	"Cancelled": set(),  # Terminal
}


class MemoraVoucherAllocation(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("VALLOC-.#####.")

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
				if self.sale_model == "Prepaid":
					self._create_prepaid_invoice()
			elif self.allocation_type == "Return":
				self._apply_return()
				if self.sale_model == "Prepaid":
					self._create_prepaid_credit_note()
				# FIN-06: Consignment returns require NO financial action
			self._update_batch_counters()

	def _create_prepaid_invoice(self):
		"""Create Sales Invoice for completed prepaid allocation (FIN-01).

		Invoice failure is logged but does NOT roll back the allocation --
		financial docs can be recreated manually if needed.
		"""
		try:
			from memora_admin.memora_admin.services.voucher.invoice import (
				create_prepaid_allocation_invoice,
			)

			invoice_name = create_prepaid_allocation_invoice(self.name)
			frappe.msgprint(f"Sales Invoice {invoice_name} created", alert=True)
		except Exception:
			frappe.log_error(title=f"Invoice creation failed for allocation {self.name}")

	def _create_prepaid_credit_note(self):
		"""Create Credit Note for completed prepaid return (FIN-02).

		Credit note failure is logged but does NOT roll back the return --
		financial docs can be recreated manually if needed.
		"""
		try:
			from memora_admin.memora_admin.services.voucher.invoice import (
				create_prepaid_return_credit_note,
			)

			credit_note_name = create_prepaid_return_credit_note(self.name)
			if credit_note_name:
				frappe.msgprint(f"Credit Note {credit_note_name} created", alert=True)
		except Exception:
			frappe.log_error(title=f"Credit note creation failed for allocation {self.name}")

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

		Only targets Available cards or cards already allocated to this customer.
		Cards allocated to a different library must be returned first.
		"""
		card_names = [row.voucher_card for row in self.allocation_cards]
		if not card_names:
			return

		# Guard: prevent stealing cards from another library
		placeholders = ", ".join(["%s"] * len(card_names))
		stolen = frappe.db.sql(
			f"""SELECT name, library FROM `tabMemora Voucher Card`
			WHERE name IN ({placeholders}) AND status = 'Allocated' AND library != %s""",
			[*card_names, self.customer],
			as_dict=True,
		)
		if stolen:
			names = ", ".join(c["name"] for c in stolen)
			frappe.throw(
				f"Cannot allocate: cards {names} belong to another library. Return them first.",
				frappe.ValidationError,
			)
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
		allocated_count = frappe.db.count("Memora Voucher Card", {"batch": self.batch, "status": "Allocated"})
		frappe.db.set_value(
			"Memora Voucher Batch",
			self.batch,
			"allocated_count",
			allocated_count,
			update_modified=True,
		)

	def _activate_batch_if_needed(self):
		"""Transition batch from Generated to Active on first allocation."""
		batch_status = frappe.db.get_value("Memora Voucher Batch", self.batch, "status")
		if batch_status == "Generated":
			frappe.db.set_value(
				"Memora Voucher Batch",
				self.batch,
				"status",
				"Active",
				update_modified=True,
			)
