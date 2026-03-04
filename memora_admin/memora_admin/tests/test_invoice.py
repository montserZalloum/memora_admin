"""
Integration tests for Sales Invoice and Credit Note creation.

Tests cover:
- US3 (FR-009 through FR-012): Invoice creation and submission
- US4 (FR-013, FR-014): Credit Note creation for returns
- US5 (FR-015): Full prepaid invoice flow end-to-end
"""

from decimal import Decimal

import frappe

from memora_admin.memora_admin.tests.voucher_fixtures import (
	make_batch,
	make_customer,
	make_product_grant,
)
from memora_admin.memora_admin.tests.voucher_helpers import (
	fill_and_complete_allocation,
	generate_batch_sync,
)
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestCreateInvoice(VoucherTestCase):
	"""Tests for Sales Invoice creation from prepaid allocations."""

	@classmethod
	def setUpClass(cls):
		"""Create shared test data: allocation with invoice."""
		super().setUpClass()

		# Create grant, batch, generate cards
		cls.grant = make_product_grant(season="SEAS-00027")
		cls.batch = make_batch(face_value=10, grants=[cls.grant.name])
		generate_batch_sync(cls.batch.name)

		# Create customer with commission
		cls.customer = make_customer(commission_type="Percentage", commission_value="10")

		# Create and complete allocation (triggers invoice creation)
		cls.allocation = fill_and_complete_allocation(cls.batch.name, cls.customer.name, quantity=5)

		# Reload allocation to get sales_invoice link
		cls.allocation.reload()

		# Load the created Sales Invoice
		cls.sales_invoice = frappe.get_doc("Sales Invoice", cls.allocation.sales_invoice)

	def test_invoice_is_submitted(self):
		"""FR-009: Created invoice is submitted (docstatus == 1)."""
		self.assertIsNotNone(self.allocation.sales_invoice)
		self.assertEqual(self.sales_invoice.docstatus, 1)

	def test_invoice_customer_matches_allocation(self):
		"""FR-010: Invoice customer matches allocation customer."""
		self.assertEqual(self.sales_invoice.customer, self.customer.name)

	def test_invoice_uses_voucher_item_code(self):
		"""FR-011: Invoice uses MEMORA-VOUCHER-CARD item code."""
		self.assertEqual(len(self.sales_invoice.items), 1)
		self.assertEqual(self.sales_invoice.items[0].item_code, "MEMORA-VOUCHER-CARD")

	def test_invoice_rate_and_quantity(self):
		"""FR-012: Invoice rate and quantity match commission calculation."""
		item = self.sales_invoice.items[0]
		# face_value=10, commission=10% → net_per_card=9.0
		self.assertEqual(item.rate, 9.0)
		self.assertEqual(item.qty, 5)


class TestCreateCreditNote(VoucherTestCase):
	"""Tests for Credit Note creation from prepaid return allocations."""

	@classmethod
	def setUpClass(cls):
		"""Create shared test data: original allocation with invoice, then return allocation."""
		super().setUpClass()

		# Step 1: Create original allocation and invoice
		cls.grant = make_product_grant(season="SEAS-00027")
		cls.batch = make_batch(face_value=10, grants=[cls.grant.name])
		generate_batch_sync(cls.batch.name)
		cls.customer = make_customer(commission_type="Percentage", commission_value="10")

		# Complete original allocation (creates invoice)
		cls.original_allocation = fill_and_complete_allocation(cls.batch.name, cls.customer.name, quantity=5)
		cls.original_allocation.reload()
		cls.original_invoice_name = cls.original_allocation.sales_invoice

		# Step 2: Get card names from original allocation
		card_names = [card.voucher_card for card in cls.original_allocation.allocation_cards]

		# Step 3: Create return allocation
		from memora_admin.memora_admin.tests.voucher_fixtures import make_allocation

		cls.return_allocation = make_allocation(
			batch=cls.batch.name,
			customer=cls.customer.name,
			allocation_type="Return",
			sale_model="Prepaid",
		)

		# Step 4: Fill return allocation with the same cards
		from memora_admin.memora_admin.api.allocation import fill_cards

		# Use the fill_cards API with specific card names
		for card_name in card_names:
			cls.return_allocation.append("allocation_cards", {"voucher_card": card_name})
		cls.return_allocation.save(ignore_permissions=True)

		# Step 5: Submit and complete return allocation
		from memora_admin.memora_admin.api.allocation import approve_allocation, submit_allocation

		submit_allocation(cls.return_allocation.name)

		# Check if approval needed
		requires_approval = frappe.db.get_value("Customer", cls.customer.name, "voucher_requires_approval")
		if requires_approval:
			approve_allocation(cls.return_allocation.name)

		# Step 6: Reload and load credit note
		cls.return_allocation.reload()
		cls.credit_note = frappe.get_doc("Sales Invoice", cls.return_allocation.sales_invoice)

	def test_credit_note_is_return_with_reference(self):
		"""FR-013: Credit Note has is_return=1 and return_against set."""
		self.assertEqual(self.credit_note.is_return, 1)
		self.assertEqual(self.credit_note.return_against, self.original_invoice_name)

	def test_credit_note_has_negative_quantity(self):
		"""FR-014: Credit Note has negative quantity."""
		self.assertEqual(len(self.credit_note.items), 1)
		self.assertLess(self.credit_note.items[0].qty, 0)

	def test_credit_note_is_submitted(self):
		"""FR-013: Credit Note is submitted (docstatus == 1)."""
		self.assertEqual(self.credit_note.docstatus, 1)


class TestPrepaidInvoiceFlow(VoucherTestCase):
	"""End-to-end tests for full prepaid allocation→invoice flow."""

	def test_full_prepaid_flow_creates_linked_invoice(self):
		"""FR-015, SC-006: End-to-end prepaid allocation creates linked invoice with correct values."""
		# Create grant and batch
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(face_value=10, grants=[grant.name])
		generate_batch_sync(batch.name)

		# Create customer with 20% commission
		customer = make_customer(commission_type="Percentage", commission_value="20")

		# Create and complete allocation
		alloc = fill_and_complete_allocation(batch.name, customer.name, quantity=5)

		# Verify invoice is linked
		self.assertIsNotNone(alloc.sales_invoice)

		# Load and verify invoice
		si = frappe.get_doc("Sales Invoice", alloc.sales_invoice)

		# Verify invoice fields
		self.assertEqual(si.docstatus, 1)  # Submitted
		self.assertEqual(si.customer, customer.name)
		self.assertEqual(len(si.items), 1)
		self.assertEqual(si.items[0].item_code, "MEMORA-VOUCHER-CARD")
		self.assertEqual(si.items[0].qty, 5)
		# face_value=10, commission=20% → net_per_card=8.0
		self.assertEqual(si.items[0].rate, 8.0)
		self.assertEqual(si.items[0].amount, 40.0)  # 8.0 * 5
