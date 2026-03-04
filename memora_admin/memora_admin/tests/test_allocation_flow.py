"""
Integration Tests: Voucher Allocation Flow

Tests the complete voucher allocation workflow including:
- Card filling (Allocate/Return types)
- Approval routing and auto-completion
- Card state mutations on completion
- Batch counter and status updates
- Prepaid invoice creation with commission
- State machine enforcement

Test Class → User Story Mapping:
- TestAllocationFlowSmoke → Infrastructure validation (smoke test)
- TestFillCards → US1: Fill Cards into Allocation
- TestSubmitAndApproval → US2: Submit and Approval Workflow
- TestCardStateOnAllocate → US3: Card State Updates (Allocate)
- TestCardStateOnReturn → US3: Card State Updates (Return)
- TestBatchCountersAndStatus → US4: Batch Counter and Status Updates
- TestPrepaidInvoiceOnAllocation → US5: Prepaid Invoice Creation
- TestStateMachineEnforcement → US6: State Machine Enforcement

Functional Requirements Coverage:
- FR-001–FR-004: Card filling logic (TestFillCards)
- FR-005–FR-011: Submit/approve/reject workflow (TestSubmitAndApproval)
- FR-012, FR-013, FR-020: Card state mutations (TestCardStateOnAllocate, TestCardStateOnReturn)
- FR-014, FR-015: Batch counters/status (TestBatchCountersAndStatus)
- FR-016, FR-017: Invoice creation (TestPrepaidInvoiceOnAllocation)
- FR-018, FR-019: State machine (TestStateMachineEnforcement)

Total: 23 tests across 7 test classes
"""

from decimal import Decimal

import frappe

from memora_admin.memora_admin.api.allocation import (
	approve_allocation,
	fill_cards,
	reject_allocation,
	submit_allocation,
)
from memora_admin.memora_admin.tests.voucher_fixtures import (
	make_allocation,
	make_batch,
	make_customer,
	make_product_grant,
)
from memora_admin.memora_admin.tests.voucher_helpers import (
	assert_batch_counters,
	fill_and_complete_allocation,
	generate_batch_sync,
	get_card_statuses,
)
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase

# Use existing season to avoid MySQL partitioning constraints
SEASON = "SEAS-00027"


class TestFillCards(VoucherTestCase):
	"""US1: Fill Cards into Allocation

	Tests fill_cards() function covering:
	- FR-001: Fill Allocate type with Available cards
	- FR-002: Fill Return type with Allocated cards for specific library
	- FR-003: Respect quantity limits
	- FR-004: Reject non-Draft allocations
	"""

	@classmethod
	def setUpClass(cls):
		"""Create shared grant and library for all tests"""
		super().setUpClass()
		cls.grant = make_product_grant(season=SEASON)
		cls.library = make_customer(requires_approval=False)

	def test_fill_allocate_gets_all_available_cards(self):
		"""TC-01: Fill Allocate type with quantity=0 gets all Available cards (FR-001, FR-003)"""
		# Create fresh batch with 10 cards
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)

		# Create Draft Allocate allocation
		alloc = make_allocation(
			batch=batch.name, customer=self.library.name, allocation_type="Allocate", sale_model="Prepaid"
		)

		# Fill with quantity=0 (all cards)
		result = fill_cards(alloc.name, quantity=0)

		# Verify all 10 cards filled
		self.assertEqual(result["filled_count"], 10)

		# Reload and verify child rows
		alloc.reload()
		self.assertEqual(len(alloc.allocation_cards), 10)

		# Verify each child row has a valid voucher_card
		for card_row in alloc.allocation_cards:
			self.assertTrue(card_row.voucher_card)
			# Verify card belongs to batch
			card = frappe.get_doc("Memora Voucher Card", card_row.voucher_card)
			self.assertEqual(card.batch, batch.name)

	def test_fill_respects_quantity_limit(self):
		"""TC-03: Fill with quantity parameter limits card selection (FR-003)"""
		# Create fresh batch with 10 cards
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)

		# Create Draft Allocate allocation
		alloc = make_allocation(
			batch=batch.name, customer=self.library.name, allocation_type="Allocate", sale_model="Prepaid"
		)

		# Fill with quantity=5
		result = fill_cards(alloc.name, quantity=5)

		# Verify exactly 5 cards filled
		self.assertEqual(result["filled_count"], 5)
		alloc.reload()
		self.assertEqual(len(alloc.allocation_cards), 5)

	def test_fill_rejects_non_draft_allocation(self):
		"""TC-04: Fill on non-Draft allocation raises ValidationError (FR-004)"""
		# Create fresh batch
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)

		# Create and complete an allocation
		completed_alloc = fill_and_complete_allocation(
			batch_name=batch.name, customer_name=self.library.name, quantity=5
		)

		# Attempt to fill again on Completed allocation
		with self.assertRaises(frappe.ValidationError) as ctx:
			fill_cards(completed_alloc.name)

		self.assertIn("Draft", str(ctx.exception))

	def test_fill_replaces_existing_cards(self):
		"""TC-05: Re-fill Draft allocation replaces previous child rows (FR-001 edge case)"""
		# Create fresh batch with 10 cards
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)

		# Create Draft allocation
		alloc = make_allocation(
			batch=batch.name, customer=self.library.name, allocation_type="Allocate", sale_model="Prepaid"
		)

		# First fill with all cards (10)
		fill_cards(alloc.name, quantity=0)
		alloc.reload()
		self.assertEqual(len(alloc.allocation_cards), 10)

		# Re-fill with quantity=5
		fill_cards(alloc.name, quantity=5)
		alloc.reload()

		# Should have 5 cards, not 15 (replacement, not append)
		self.assertEqual(len(alloc.allocation_cards), 5)

	def test_fill_return_gets_allocated_cards_for_library(self):
		"""TC-02: Fill Return type gets Allocated cards belonging to specific library (FR-002)"""
		# Create fresh batch with 10 cards
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)

		# First allocate 5 cards to Library A
		library_a = make_customer(requires_approval=False)
		fill_and_complete_allocation(batch_name=batch.name, customer_name=library_a.name, quantity=5)

		# Create Return-type allocation for Library A
		return_alloc = make_allocation(
			batch=batch.name, customer=library_a.name, allocation_type="Return", sale_model="Prepaid"
		)

		# Fill the return allocation
		result = fill_cards(return_alloc.name, quantity=0)

		# Verify exactly 5 cards filled (the ones allocated to Library A)
		self.assertEqual(result["filled_count"], 5)
		return_alloc.reload()
		self.assertEqual(len(return_alloc.allocation_cards), 5)

		# Verify each card was previously allocated to Library A
		for card_row in return_alloc.allocation_cards:
			card = frappe.get_doc("Memora Voucher Card", card_row.voucher_card)
			self.assertEqual(card.library, library_a.name)
			self.assertEqual(card.status, "Allocated")


class TestSubmitAndApproval(VoucherTestCase):
	"""US2: Submit and Approval Workflow

	Tests submit_allocation(), approve_allocation(), and reject_allocation() functions covering:
	- FR-005: Auto-complete for no-approval libraries
	- FR-006: Route to Pending Approval for approval-required libraries
	- FR-007: Reject empty allocations
	- FR-008: Validate cards belong to batch
	- FR-009: Approve completes Pending Approval allocations
	- FR-010: Reject sets Rejected status with reason
	- FR-011: Approve only works on Pending Approval state
	"""

	@classmethod
	def setUpClass(cls):
		"""Create shared grant, no-approval library, and approval-required library"""
		super().setUpClass()
		cls.grant = make_product_grant(season=SEASON)
		cls.no_approval_lib = make_customer(requires_approval=False)
		cls.approval_lib = make_customer(requires_approval=True)

	def test_submit_auto_completes_no_approval_library(self):
		"""TC-06: Submit for no-approval library auto-completes to Completed (FR-005)"""
		# Create fresh batch and fill allocation
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)
		alloc = make_allocation(
			batch=batch.name,
			customer=self.no_approval_lib.name,
			allocation_type="Allocate",
			sale_model="Prepaid",
		)
		fill_cards(alloc.name, quantity=5)

		# Submit allocation
		result = submit_allocation(alloc.name)

		# Verify auto-completed
		self.assertEqual(result["status"], "Completed")
		alloc.reload()
		self.assertEqual(alloc.status, "Completed")

	def test_submit_routes_to_pending_approval(self):
		"""TC-07: Submit for approval-required library routes to Pending Approval (FR-006)"""
		# Create fresh batch and fill allocation
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)
		alloc = make_allocation(
			batch=batch.name,
			customer=self.approval_lib.name,
			allocation_type="Allocate",
			sale_model="Prepaid",
		)
		fill_cards(alloc.name, quantity=5)

		# Submit allocation
		result = submit_allocation(alloc.name)

		# Verify routed to Pending Approval
		self.assertEqual(result["status"], "Pending Approval")
		alloc.reload()
		self.assertEqual(alloc.status, "Pending Approval")

	def test_submit_rejects_empty_allocation(self):
		"""TC-08: Submit with no cards raises ValidationError (FR-007)"""
		# Create batch and allocation without filling
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)
		alloc = make_allocation(
			batch=batch.name,
			customer=self.no_approval_lib.name,
			allocation_type="Allocate",
			sale_model="Prepaid",
		)
		# DO NOT fill cards

		# Attempt to submit empty allocation
		with self.assertRaises(frappe.ValidationError) as ctx:
			submit_allocation(alloc.name)

		self.assertIn("No cards", str(ctx.exception))

	def test_submit_rejects_mismatched_batch_cards(self):
		"""TC-09: Submit with cards from wrong batch raises ValidationError (FR-008)"""
		# Create two batches
		batch_a = make_batch(grants=[self.grant.name], quantity=5)
		generate_batch_sync(batch_a.name)
		batch_b = make_batch(grants=[self.grant.name], quantity=5)
		generate_batch_sync(batch_b.name)

		# Create allocation for batch A
		alloc = make_allocation(
			batch=batch_a.name,
			customer=self.no_approval_lib.name,
			allocation_type="Allocate",
			sale_model="Prepaid",
		)

		# Manually add a card from batch B
		card_from_b = frappe.get_all(
			"Memora Voucher Card", filters={"batch": batch_b.name, "status": "Available"}, limit=1
		)[0].name

		alloc.append("allocation_cards", {"voucher_card": card_from_b})

		# Validation error should be raised on save (not submit)
		with self.assertRaises(frappe.ValidationError) as ctx:
			alloc.save()

		self.assertIn("do not belong to batch", str(ctx.exception))

	def test_approve_completes_pending_allocation(self):
		"""TC-10: Approve transitions Pending Approval to Completed (FR-009)"""
		# Create batch and submit to Pending Approval
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)
		alloc = make_allocation(
			batch=batch.name,
			customer=self.approval_lib.name,
			allocation_type="Allocate",
			sale_model="Prepaid",
		)
		fill_cards(alloc.name, quantity=5)
		submit_allocation(alloc.name)

		# Approve allocation
		result = approve_allocation(alloc.name)

		# Verify completed
		self.assertEqual(result["status"], "Completed")
		alloc.reload()
		self.assertEqual(alloc.status, "Completed")

	def test_reject_sets_rejected_with_reason(self):
		"""TC-11: Reject sets Rejected status and stores reason (FR-010)"""
		# Create batch and submit to Pending Approval
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)
		alloc = make_allocation(
			batch=batch.name,
			customer=self.approval_lib.name,
			allocation_type="Allocate",
			sale_model="Prepaid",
		)
		fill_cards(alloc.name, quantity=5)
		submit_allocation(alloc.name)

		# Reject allocation with reason
		reject_reason = "Quality issue detected"
		result = reject_allocation(alloc.name, reject_reason=reject_reason)

		# Verify rejected with reason
		self.assertEqual(result["status"], "Rejected")
		alloc.reload()
		self.assertEqual(alloc.status, "Rejected")
		self.assertEqual(alloc.notes, reject_reason)

	def test_approve_rejects_non_pending_allocation(self):
		"""TC-12: Approve on non-Pending Approval allocation raises ValidationError (FR-011)"""
		# Create filled Draft allocation (not submitted)
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)
		alloc = make_allocation(
			batch=batch.name,
			customer=self.approval_lib.name,
			allocation_type="Allocate",
			sale_model="Prepaid",
		)
		fill_cards(alloc.name, quantity=5)
		# DO NOT submit

		# Attempt to approve Draft allocation
		with self.assertRaises(frappe.ValidationError) as ctx:
			approve_allocation(alloc.name)

		self.assertIn("Pending Approval", str(ctx.exception))


class TestCardStateOnAllocate(VoucherTestCase):
	"""US3: Card State Updates on Allocate Completion

	Tests card field mutations after Allocate-type allocation completes:
	- FR-012: Allocated cards have status, library, allocation, sale_model set
	- FR-012 boundary: Non-allocated cards remain Available with null fields
	"""

	@classmethod
	def setUpClass(cls):
		"""Create batch, allocate 5 of 10 cards to library"""
		super().setUpClass()
		cls.grant = make_product_grant(season=SEASON)
		cls.batch = make_batch(grants=[cls.grant.name], quantity=10)
		generate_batch_sync(cls.batch.name)
		cls.library = make_customer(requires_approval=False)

		# Allocate 5 cards (Prepaid) to library
		cls.alloc = fill_and_complete_allocation(
			batch_name=cls.batch.name, customer_name=cls.library.name, quantity=5, sale_model="Prepaid"
		)

	def test_allocated_cards_have_correct_fields(self):
		"""TC-13: Verify allocated cards have status=Allocated, library, allocation, sale_model (FR-012)"""
		# Get cards from the completed allocation
		self.alloc.reload()
		self.assertEqual(len(self.alloc.allocation_cards), 5)

		# Verify each allocated card has correct fields
		for card_row in self.alloc.allocation_cards:
			card = frappe.get_doc("Memora Voucher Card", card_row.voucher_card)
			self.assertEqual(card.status, "Allocated")
			self.assertEqual(card.library, self.library.name)
			self.assertEqual(card.allocation, self.alloc.name)
			self.assertEqual(card.sale_model, "Prepaid")

	def test_remaining_cards_stay_available(self):
		"""TC-14: Verify non-allocated cards retain Available status with null fields (FR-012 boundary)"""
		# Get all cards in batch
		all_cards = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": self.batch.name},
			fields=["name", "status", "library", "allocation"],
		)
		self.assertEqual(len(all_cards), 10)

		# Get allocated card names
		allocated_card_names = {row.voucher_card for row in self.alloc.allocation_cards}

		# Find remaining cards (not allocated)
		remaining_cards = [c for c in all_cards if c.name not in allocated_card_names]
		self.assertEqual(len(remaining_cards), 5)

		# Verify remaining cards are still Available with null fields
		for card in remaining_cards:
			self.assertEqual(card.status, "Available")
			self.assertIsNone(card.library)
			self.assertIsNone(card.allocation)


class TestCardStateOnReturn(VoucherTestCase):
	"""US3: Card State Updates on Return Completion

	Tests card field mutations after Return-type allocation completes:
	- FR-013, FR-020: Returned cards have status=Available, cleared fields, return_allocation set
	- FR-002 edge case: Return for library with no allocated cards fills nothing
	"""

	@classmethod
	def setUpClass(cls):
		"""Allocate cards then complete a Return-type allocation"""
		super().setUpClass()
		cls.grant = make_product_grant(season=SEASON)
		cls.batch = make_batch(grants=[cls.grant.name], quantity=10)
		generate_batch_sync(cls.batch.name)
		cls.library = make_customer(requires_approval=False)

		# First allocate 5 cards to library
		cls.allocate_alloc = fill_and_complete_allocation(
			batch_name=cls.batch.name, customer_name=cls.library.name, quantity=5, sale_model="Prepaid"
		)

		# Then return those 5 cards
		cls.return_alloc = make_allocation(
			batch=cls.batch.name, customer=cls.library.name, allocation_type="Return", sale_model="Prepaid"
		)
		fill_cards(cls.return_alloc.name, quantity=0)
		submit_allocation(cls.return_alloc.name)

	def test_returned_cards_cleared_with_return_allocation(self):
		"""TC-15: Verify returned cards have status=Available, cleared fields, return_allocation set (FR-013, FR-020)"""
		# Reload return allocation
		self.return_alloc.reload()
		self.assertEqual(len(self.return_alloc.allocation_cards), 5)

		# Verify each returned card has cleared fields
		for card_row in self.return_alloc.allocation_cards:
			card = frappe.get_doc("Memora Voucher Card", card_row.voucher_card)
			self.assertEqual(card.status, "Available")
			self.assertIsNone(card.library)
			self.assertIsNone(card.allocation)
			self.assertIsNone(card.sale_model)
			self.assertEqual(card.return_allocation, self.return_alloc.name)

	def test_return_with_zero_eligible_cards(self):
		"""TC-16: Return for library with no allocated cards fills nothing (FR-002 edge case)"""
		# Create a different library with no allocated cards
		library_b = make_customer(requires_approval=False)

		# Create Return allocation for library B
		return_alloc_b = make_allocation(
			batch=self.batch.name, customer=library_b.name, allocation_type="Return", sale_model="Prepaid"
		)

		# Fill should return 0 cards
		result = fill_cards(return_alloc_b.name, quantity=0)
		self.assertEqual(result["filled_count"], 0)

		# Verify allocation has no cards
		return_alloc_b.reload()
		self.assertEqual(len(return_alloc_b.allocation_cards), 0)


class TestBatchCountersAndStatus(VoucherTestCase):
	"""US4: Batch Counter and Status Updates

	Tests batch metadata updates after allocation completion:
	- FR-014: allocated_count is recounted after allocation
	- FR-015: Batch transitions from Generated to Active on first allocation
	"""

	@classmethod
	def setUpClass(cls):
		"""Create batch with 10 cards, allocate 5, store batch name"""
		super().setUpClass()
		cls.grant = make_product_grant(season=SEASON)
		cls.batch = make_batch(grants=[cls.grant.name], quantity=10)
		generate_batch_sync(cls.batch.name)
		cls.library = make_customer(requires_approval=False)

		# Allocate 5 of 10 cards
		cls.alloc = fill_and_complete_allocation(
			batch_name=cls.batch.name, customer_name=cls.library.name, quantity=5
		)

	def test_allocated_count_updated(self):
		"""TC-17: Verify batch.allocated_count=5 after allocating 5 of 10 cards (FR-014)"""
		# Use helper to verify batch counters
		assert_batch_counters(self, self.batch.name, allocated_count=5, generated_count=10)

	def test_batch_transitions_generated_to_active(self):
		"""TC-18: Verify batch.status changes from Generated to Active on first allocation (FR-015)"""
		# Reload batch and verify status
		self.batch.reload()
		self.assertEqual(self.batch.status, "Active")


class TestPrepaidInvoiceOnAllocation(VoucherTestCase):
	"""US5: Prepaid Invoice Creation

	Tests Sales Invoice creation for Prepaid allocations:
	- FR-016: Prepaid allocations create linked Sales Invoices
	- FR-017: Invoice amounts reflect commission calculations
	- FR-016 negative: Consignment allocations create no invoice
	"""

	@classmethod
	def setUpClass(cls):
		"""Create batch with face_value, library with 10% commission, complete Prepaid allocation"""
		super().setUpClass()
		cls.grant = make_product_grant(season=SEASON)
		cls.batch = make_batch(grants=[cls.grant.name], quantity=10, face_value=10)
		generate_batch_sync(cls.batch.name)
		cls.library = make_customer(
			requires_approval=False, commission_type="Percentage", commission_value="10"
		)

		# Complete Prepaid allocation of 5 cards
		cls.alloc = fill_and_complete_allocation(
			batch_name=cls.batch.name, customer_name=cls.library.name, quantity=5, sale_model="Prepaid"
		)

	def test_prepaid_creates_linked_sales_invoice(self):
		"""TC-19: Verify allocation.sales_invoice is set and linked invoice has docstatus=1 (FR-016)"""
		# Reload allocation
		self.alloc.reload()

		# Verify sales_invoice is set
		self.assertIsNotNone(self.alloc.sales_invoice)
		self.assertTrue(self.alloc.sales_invoice)  # Not empty string

		# Load invoice and verify it's submitted
		invoice = frappe.get_doc("Sales Invoice", self.alloc.sales_invoice)
		self.assertEqual(invoice.docstatus, 1)

	def test_invoice_amount_reflects_commission(self):
		"""TC-20: Verify invoice rate=9.0 (10 - 10%), qty=5, correct customer and item_code (FR-017)"""
		# Reload allocation and get invoice
		self.alloc.reload()
		invoice = frappe.get_doc("Sales Invoice", self.alloc.sales_invoice)

		# Verify invoice fields
		self.assertEqual(invoice.customer, self.library.name)
		self.assertEqual(len(invoice.items), 1)

		# Verify item details
		item = invoice.items[0]
		self.assertEqual(item.item_code, "MEMORA-VOUCHER-CARD")
		self.assertEqual(item.qty, 5)
		# face_value=10, commission=10% → net per card = 10 - 1 = 9.0
		self.assertEqual(Decimal(str(item.rate)), Decimal("9.0"))

	def test_consignment_creates_no_invoice(self):
		"""TC-21: Complete Consignment allocation and verify no Sales Invoice created (FR-016 negative)"""
		# Create fresh batch and library
		batch = make_batch(grants=[self.grant.name], quantity=10, face_value=10)
		generate_batch_sync(batch.name)
		library = make_customer(requires_approval=False)

		# Complete Consignment allocation
		consignment_alloc = fill_and_complete_allocation(
			batch_name=batch.name, customer_name=library.name, quantity=5, sale_model="Consignment"
		)

		# Verify no invoice created
		consignment_alloc.reload()
		self.assertTrue(consignment_alloc.sales_invoice is None or consignment_alloc.sales_invoice == "")


class TestStateMachineEnforcement(VoucherTestCase):
	"""US6: State Machine Enforcement

	Tests VALID_TRANSITIONS state machine enforcement:
	- FR-018: Invalid skip transitions (Draft→Completed) are rejected
	- FR-019: Terminal states (Completed, Rejected) cannot be escaped
	"""

	@classmethod
	def setUpClass(cls):
		"""Create shared grant and approval-required library for state machine testing"""
		super().setUpClass()
		cls.grant = make_product_grant(season=SEASON)
		cls.approval_lib = make_customer(requires_approval=True)

	def test_invalid_skip_transition_rejected(self):
		"""TC-22: Set Draft allocation status directly to Completed and save raises ValidationError (FR-018)"""
		# Create fresh batch and Draft allocation
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)
		alloc = make_allocation(
			batch=batch.name,
			customer=self.approval_lib.name,
			allocation_type="Allocate",
			sale_model="Prepaid",
		)

		# Attempt invalid transition: Draft → Completed (skipping Pending Approval/Approved)
		alloc.status = "Completed"

		with self.assertRaises(frappe.ValidationError) as ctx:
			alloc.save()

		self.assertIn("Invalid allocation status transition", str(ctx.exception))

	def test_terminal_state_blocks_transitions(self):
		"""TC-23: Set Completed allocation status to Draft and save raises ValidationError (FR-019)"""
		# Create fresh batch and complete allocation
		batch = make_batch(grants=[self.grant.name], quantity=10)
		generate_batch_sync(batch.name)
		completed_alloc = fill_and_complete_allocation(
			batch_name=batch.name, customer_name=self.approval_lib.name, quantity=5
		)

		# Attempt invalid transition: Completed → Draft (terminal state escape)
		completed_alloc.reload()
		completed_alloc.status = "Draft"

		with self.assertRaises(frappe.ValidationError) as ctx:
			completed_alloc.save()

		self.assertIn("terminal state", str(ctx.exception))
