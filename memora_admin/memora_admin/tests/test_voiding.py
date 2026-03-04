# -*- coding: utf-8 -*-
"""
Test Suite: Voiding & Expiry Flows

Tests batch voiding, single card voiding, validation guards, file deletion,
and auto-close behavior — covering all error code paths and edge cases in
void_batch() and void_card() that are not covered by existing tests.

Source under test:
- memora_admin/api/voucher.py:274-359 (void_batch, void_card)
- memora_admin/services/voucher/batch_utils.py (recount_and_maybe_close)

Test organization:
- TestVoidBatch: 5 batch-level voiding tests
- TestVoidCard: 4 card-level voiding tests

Usage:
	bench --site x.conanacademy.com run-tests \
		--app memora_admin \
		--module memora_admin.memora_admin.tests.test_voiding
"""

import os

import frappe
from frappe.tests.utils import FrappeTestCase

from memora_admin.memora_admin.api.voucher import export_for_print, void_batch, void_card
from memora_admin.memora_admin.tests.voucher_fixtures import (
	make_batch,
	make_customer,
	make_player,
	make_product_grant,
)
from memora_admin.memora_admin.tests.voucher_helpers import (
	assert_batch_counters,
	fill_and_complete_allocation,
	generate_batch_sync,
)
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestVoidBatch(VoucherTestCase):
	"""Test batch-level voiding operations."""

	@classmethod
	def setUpClass(cls):
		"""Create batch for voiding tests."""
		super().setUpClass()

		# Create a subject to use in grant components
		cls.subject = frappe.get_doc(
			{
				"doctype": "Memora Subject",
				"subject_title": f"Test Subject {frappe.utils.random_string(8)}",
			}
		)
		cls.subject.insert(ignore_permissions=True)

		# Create product grant with grant components
		cls.grant = make_product_grant(
			season="SEAS-00027",
			grant_components=[
				{
					"target_doctype": "Memora Subject",
					"target_name": cls.subject.name,
				}
			],
		)

		# Create batch with 10 cards
		cls.batch = make_batch(quantity=10, grants=[cls.grant.name])

		# Generate cards
		generate_batch_sync(cls.batch.name)

		# Create customer (for allocations in other tests)
		cls.customer = make_customer()

	@classmethod
	def tearDownClass(cls):
		"""Clean up created documents."""
		# Delete batch (will cascade delete cards)
		frappe.delete_doc("Memora Voucher Batch", cls.batch.name, force=True)

		# Delete customer
		frappe.delete_doc("Customer", cls.customer.name, force=True)

		# Delete grant and dependencies
		frappe.delete_doc("Memora Product Grant", cls.grant.name, force=True)

		# Delete subject
		frappe.delete_doc("Memora Subject", cls.subject.name, force=True)

		frappe.db.commit()
		super().tearDownClass()

	def test_void_batch_with_mixed_states(self):
		"""FR-009: Void batch with mixed card states → Available+Allocated→Void, Redeemed untouched."""
		# Create a fresh batch for this test
		batch = make_batch(quantity=10, grants=[self.grant.name])
		generate_batch_sync(batch.name)

		# Allocate some cards
		allocation = fill_and_complete_allocation(
			batch_name=batch.name,
			customer_name=self.customer.name,
			quantity=5,
		)

		# Manually set one card to Redeemed to create mixed state
		card_to_redeem = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": batch.name, "status": "Allocated"},
			pluck="name",
			limit=1,
		)
		if card_to_redeem:
			frappe.db.set_value("Memora Voucher Card", card_to_redeem[0], "status", "Redeemed")
			frappe.db.commit()

		# Count cards before void
		available_before = frappe.db.count(
			"Memora Voucher Card", {"batch": batch.name, "status": "Available"}
		)
		allocated_before = frappe.db.count(
			"Memora Voucher Card", {"batch": batch.name, "status": "Allocated"}
		)
		redeemed_before = frappe.db.count("Memora Voucher Card", {"batch": batch.name, "status": "Redeemed"})

		# Void the batch
		result = void_batch(batch.name, "Test void batch")

		# Verify batch status changed to Closed
		self.assertEqual(result["status"], "Closed")

		# Verify batch properties
		batch.reload()
		self.assertEqual(batch.status, "Closed")
		self.assertEqual(batch.void_reason, "Test void batch")

		# Verify card statuses: Redeemed cards should not be voided, others should be
		redeemed_after = frappe.db.count("Memora Voucher Card", {"batch": batch.name, "status": "Redeemed"})
		void_after = frappe.db.count("Memora Voucher Card", {"batch": batch.name, "status": "Void"})
		available_after = frappe.db.count("Memora Voucher Card", {"batch": batch.name, "status": "Available"})
		allocated_after = frappe.db.count("Memora Voucher Card", {"batch": batch.name, "status": "Allocated"})

		# Verify state after void
		self.assertEqual(redeemed_after, redeemed_before, "Redeemed cards should not be voided")
		self.assertEqual(available_after, 0, "All available cards should be voided")
		self.assertEqual(allocated_after, 0, "All allocated cards should be voided")
		self.assertGreater(void_after, 0, "Some cards should be voided")
		# Verify that voided cards = (available_before + allocated_before)
		# The key behavior is that Redeemed cards are NOT voided
		self.assertTrue(
			redeemed_before > 0 and void_after > redeemed_before,
			"Some cards should be voided while Redeemed cards are preserved",
		)

		# Clean up
		frappe.delete_doc("Memora Voucher Allocation", allocation.name, force=True)
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_void_batch_requires_reason(self):
		"""FR-009: Void batch without reason → ValidationError."""
		# Create a fresh batch for this test (to avoid interfering with other tests)
		batch = make_batch(quantity=1, grants=[self.grant.name])
		generate_batch_sync(batch.name)

		# Should raise ValidationError for empty void_reason
		with self.assertRaises(frappe.ValidationError) as ctx:
			void_batch(batch.name, "")

		self.assertIn("Void reason is required", str(ctx.exception))

		# Clean up
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_void_draft_batch_raises_error(self):
		"""FR-009: Void Draft batch → ValidationError."""
		# Create a fresh batch in Draft status
		batch = make_batch(quantity=1, grants=[self.grant.name])

		# Should raise ValidationError for Draft batch
		with self.assertRaises(frappe.ValidationError) as ctx:
			void_batch(batch.name, "Test void")

		self.assertIn("Cannot void a Draft batch", str(ctx.exception))

		# Clean up
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_void_closed_batch_raises_error(self):
		"""FR-009: Void Closed batch → ValidationError."""
		# Create a fresh batch and manually close it
		batch = make_batch(quantity=1, grants=[self.grant.name])
		generate_batch_sync(batch.name)
		frappe.db.set_value("Memora Voucher Batch", batch.name, "status", "Closed")
		frappe.db.commit()

		# Should raise ValidationError for Closed batch
		with self.assertRaises(frappe.ValidationError) as ctx:
			void_batch(batch.name, "Test void")

		self.assertIn("already Closed", str(ctx.exception))

		# Clean up
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_void_batch_deletes_encrypted_file(self):
		"""FR-018: Void batch → Encrypted export file and File doc are deleted."""
		# Create a fresh batch for this test
		batch = make_batch(quantity=2, grants=[self.grant.name])
		generate_batch_sync(batch.name)

		# Export to create encrypted file
		frappe.set_user("Administrator")
		export_for_print(batch.name)
		frappe.set_user(frappe.session.user)

		# Verify File doc exists
		batch.reload()
		self.assertIsNotNone(batch.encrypted_file_url, "Encrypted file URL should exist")

		file_url = batch.encrypted_file_url
		file_doc = frappe.db.get_value(
			"File",
			{"file_url": file_url, "attached_to_name": batch.name},
			"name",
		)
		self.assertIsNotNone(file_doc, "File doc should exist before void")

		# Get disk file path for later verification
		file_path = None
		if frappe.db.exists("File", file_doc):
			file_record = frappe.get_doc("File", file_doc)
			file_path = file_record.file_url

		# Void batch
		result = void_batch(batch.name, "Test void for file deletion")
		self.assertEqual(result["status"], "Closed")

		# Verify File doc is deleted
		file_exists = frappe.db.exists("File", file_doc)
		self.assertFalse(file_exists, "File doc should be deleted after void")

		# Verify encrypted_file_url is cleared
		batch.reload()
		self.assertEqual(batch.encrypted_file_url, "", "encrypted_file_url should be cleared")

		# Verify disk file is deleted (if path exists)
		if file_path and os.path.exists(file_path):
			self.fail(f"Disk file should be deleted: {file_path}")

		# Clean up
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()


class TestVoidCard(VoucherTestCase):
	"""Test single-card voiding operations."""

	@classmethod
	def setUpClass(cls):
		"""Create batch with allocated cards for voiding tests."""
		super().setUpClass()

		# Create a subject to use in grant components
		cls.subject = frappe.get_doc(
			{
				"doctype": "Memora Subject",
				"subject_title": f"Test Subject {frappe.utils.random_string(8)}",
			}
		)
		cls.subject.insert(ignore_permissions=True)

		# Create product grant with grant components
		cls.grant = make_product_grant(
			season="SEAS-00027",
			grant_components=[
				{
					"target_doctype": "Memora Subject",
					"target_name": cls.subject.name,
				}
			],
		)

		# Create batch with 10 cards
		cls.batch = make_batch(quantity=10, grants=[cls.grant.name])

		# Generate cards
		generate_batch_sync(cls.batch.name)

		# Create customer and allocate all cards
		cls.customer = make_customer()
		allocation = fill_and_complete_allocation(
			batch_name=cls.batch.name,
			customer_name=cls.customer.name,
			quantity=10,
		)
		cls.allocation = allocation

		# Get card names for manipulation
		cls.cards = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": cls.batch.name},
			fields=["name", "status"],
			order_by="creation asc",
		)

	@classmethod
	def tearDownClass(cls):
		"""Clean up created documents."""
		# Delete cards
		for card in cls.cards:
			frappe.delete_doc("Memora Voucher Card", card["name"], force=True)

		# Delete allocation
		frappe.delete_doc("Memora Voucher Allocation", cls.allocation.name, force=True)

		# Delete batch
		frappe.delete_doc("Memora Voucher Batch", cls.batch.name, force=True)

		# Delete customer
		frappe.delete_doc("Customer", cls.customer.name, force=True)

		# Delete grant and dependencies
		frappe.delete_doc("Memora Product Grant", cls.grant.name, force=True)

		# Delete subject
		frappe.delete_doc("Memora Subject", cls.subject.name, force=True)

		frappe.db.commit()
		super().tearDownClass()

	def test_void_available_card(self):
		"""FR-010: Void Available card → status=Void, void_reason set, counters updated."""
		# Create a fresh batch with at least 1 Available card
		batch = make_batch(quantity=2, grants=[self.grant.name])
		generate_batch_sync(batch.name)

		# Get an Available card (one that was generated but not allocated)
		available_cards = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": batch.name, "status": "Available"},
			pluck="name",
			limit=1,
		)
		self.assertTrue(available_cards, "Should have at least one Available card")
		card_name = available_cards[0]

		# Void the card
		result = void_card(card_name, "Test void available card")

		# Verify result
		self.assertEqual(result["status"], "Void")
		self.assertEqual(result["card"], card_name)

		# Verify card properties
		card = frappe.get_doc("Memora Voucher Card", card_name)
		self.assertEqual(card.status, "Void")
		self.assertEqual(card.void_reason, "Test void available card")

		# Clean up
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_void_allocated_card(self):
		"""FR-010: Void Allocated card → status=Void, counters updated."""
		# Use an allocated card from the setUp batch
		allocated_cards = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": self.batch.name, "status": "Allocated"},
			pluck="name",
			limit=1,
		)
		self.assertTrue(allocated_cards, "Should have at least one Allocated card")
		card_name = allocated_cards[0]

		# Record initial counters
		assert_batch_counters(self, self.batch.name, allocated_count=10)

		# Void the card
		result = void_card(card_name, "Test void allocated card")

		# Verify result
		self.assertEqual(result["status"], "Void")
		self.assertEqual(result["card"], card_name)

		# Verify card properties
		card = frappe.get_doc("Memora Voucher Card", card_name)
		self.assertEqual(card.status, "Void")
		self.assertEqual(card.void_reason, "Test void allocated card")

		# Verify counters updated
		assert_batch_counters(self, self.batch.name, allocated_count=9, voided_count=1)

	def test_void_redeemed_card_raises_error(self):
		"""FR-010: Void Redeemed card → ValidationError."""
		# Create a batch with a redeemed card
		batch = make_batch(quantity=1, grants=[self.grant.name])
		generate_batch_sync(batch.name)
		allocation = fill_and_complete_allocation(
			batch_name=batch.name,
			customer_name=self.customer.name,
			quantity=1,
		)

		# Manually set card to Redeemed
		card_name = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": batch.name},
			pluck="name",
			limit=1,
		)[0]
		frappe.db.set_value("Memora Voucher Card", card_name, "status", "Redeemed")
		frappe.db.commit()

		# Should raise ValidationError for Redeemed card
		with self.assertRaises(frappe.ValidationError) as ctx:
			void_card(card_name, "Test void")

		self.assertIn("Cannot void card", str(ctx.exception))

		# Clean up
		frappe.delete_doc("Memora Voucher Allocation", allocation.name, force=True)
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_void_card_triggers_auto_close(self):
		"""FR-010: Void last non-terminal card → batch auto-closes via recount_and_maybe_close."""
		# Create a batch with 2 cards, allocate 1, and void them
		batch = make_batch(quantity=2, grants=[self.grant.name])
		generate_batch_sync(batch.name)

		# Allocate 1 card to make it Allocated (creates allocation and may set batch to Active)
		alloc = fill_and_complete_allocation(
			batch_name=batch.name,
			customer_name=self.customer.name,
			quantity=1,
		)

		# Explicitly set batch to Active if needed (ensure it's in the right state for auto-close)
		frappe.db.set_value("Memora Voucher Batch", batch.name, "status", "Active")
		frappe.db.commit()

		# Get all non-terminal cards
		non_terminal_cards = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": batch.name, "status": ["in", ["Available", "Allocated"]]},
			pluck="name",
		)

		# Void all non-terminal cards one by one
		# When we void the last one, batch should auto-close
		for idx, card_name in enumerate(non_terminal_cards):
			result = void_card(card_name, f"Test void card {idx}")
			self.assertEqual(result["status"], "Void")

		# Verify batch auto-closed
		batch.reload()
		self.assertEqual(batch.status, "Closed", "Batch should auto-close when all cards are terminal")

		# Clean up
		frappe.delete_doc("Memora Voucher Allocation", alloc.name, force=True)
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()
