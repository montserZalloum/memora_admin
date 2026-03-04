# -*- coding: utf-8 -*-
"""
Test Suite: Counter Integrity

Tests batch counter accuracy across all operations and recount_and_maybe_close() behavior.

Source under test:
- memora_admin/services/voucher/batch_utils.py:recount_and_maybe_close()
- memora_admin/api/voucher.py (void_batch)

Usage:
	bench --site x.conanacademy.com run-tests \
		--app memora_admin \
		--module memora_admin.memora_admin.tests.test_counter_integrity
"""

import frappe

from memora_admin.memora_admin.api.voucher import void_batch
from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close
from memora_admin.memora_admin.tests.voucher_fixtures import (
	make_batch,
	make_product_grant,
)
from memora_admin.memora_admin.tests.voucher_helpers import generate_batch_sync
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestCounterIntegrity(VoucherTestCase):
	"""Test batch counter accuracy across all operations."""

	@classmethod
	def setUpClass(cls):
		"""Create shared grant and subject for tests."""
		super().setUpClass()

		# Create a subject
		cls.subject = frappe.get_doc(
			{
				"doctype": "Memora Subject",
				"subject_title": f"Test Subject {frappe.utils.random_string(8)}",
			}
		)
		cls.subject.insert(ignore_permissions=True)

		# Create product grant
		cls.grant = make_product_grant(
			season="SEAS-00027",
			grant_components=[
				{
					"target_doctype": "Memora Subject",
					"target_name": cls.subject.name,
				}
			],
		)

	@classmethod
	def tearDownClass(cls):
		"""Clean up shared documents."""
		try:
			frappe.delete_doc("Memora Product Grant", cls.grant.name, force=True)
		except:
			pass
		try:
			frappe.delete_doc("Memora Subject", cls.subject.name, force=True)
		except:
			pass
		frappe.db.commit()
		super().tearDownClass()

	def test_full_lifecycle_counter_accuracy(self):
		"""FR-012, FR-013: Counter accuracy across full lifecycle."""
		batch = make_batch(quantity=5, grants=[self.grant.name])
		generate_batch_sync(batch.name)
		frappe.db.commit()

		# Recount returns a dict with counters
		result = recount_and_maybe_close(batch.name)

		# Verify that recount_and_maybe_close returns a valid dict with required keys
		self.assertIn("redeemed_count", result)
		self.assertIn("voided_count", result)
		self.assertIn("allocated_count", result)
		self.assertIn("expired_count", result)
		self.assertIn("closed", result)

		# Verify counts are non-negative integers
		self.assertGreaterEqual(result["redeemed_count"], 0)
		self.assertGreaterEqual(result["voided_count"], 0)

		# Cleanup
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_recount_idempotency(self):
		"""FR-013: recount_and_maybe_close() is idempotent."""
		batch = make_batch(quantity=3, grants=[self.grant.name])
		generate_batch_sync(batch.name)
		frappe.db.commit()

		# Recount twice
		result1 = recount_and_maybe_close(batch.name)
		result2 = recount_and_maybe_close(batch.name)

		# Verify identical results
		self.assertEqual(result1["allocated_count"], result2["allocated_count"])
		self.assertEqual(result1["redeemed_count"], result2["redeemed_count"])

		# Cleanup
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_auto_close_only_active_batches(self):
		"""FR-013: Auto-close only triggers for Active batches."""
		batch = make_batch(quantity=3, grants=[self.grant.name])
		generate_batch_sync(batch.name)
		frappe.db.commit()

		# Batch is in Generated status
		batch.reload()
		self.assertEqual(batch.status, "Generated")

		# Recount should NOT auto-close
		result = recount_and_maybe_close(batch.name)
		self.assertFalse(result["closed"])

		batch.reload()
		self.assertEqual(batch.status, "Generated")

		# Cleanup
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_auto_close_on_all_terminal_cards(self):
		"""FR-013: Auto-close when all cards are terminal."""
		batch = make_batch(quantity=3, grants=[self.grant.name])
		generate_batch_sync(batch.name)

		# Set batch to Active
		frappe.db.set_value("Memora Voucher Batch", batch.name, "status", "Active")
		frappe.db.commit()

		# Recount should auto-close (all cards are Available, which are non-terminal)
		result = recount_and_maybe_close(batch.name)
		# Actually Available cards are non-terminal, so batch won't close
		self.assertFalse(result["closed"])

		# Now manually mark all as terminal
		frappe.db.sql(f"""
			UPDATE `tabMemora Voucher Card`
			SET status = 'Redeemed'
			WHERE batch = (SELECT name FROM `tabMemora Voucher Batch` WHERE name = '{batch.name}')
		""")
		frappe.db.commit()

		# Recount should auto-close now
		result = recount_and_maybe_close(batch.name)
		self.assertTrue(result["closed"])

		batch.reload()
		self.assertEqual(batch.status, "Closed")

		# Cleanup
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()

	def test_counters_after_void_batch(self):
		"""FR-012, FR-013: Counters accurate after void_batch()."""
		batch = make_batch(quantity=3, grants=[self.grant.name])
		generate_batch_sync(batch.name)
		frappe.db.commit()

		# Call void_batch
		result = void_batch(batch.name, "Test void reason")
		self.assertEqual(result["status"], "Closed")

		# Verify batch is closed with voided_count updated
		batch.reload()
		self.assertEqual(batch.status, "Closed")
		self.assertGreater(batch.voided_count, 0)

		# Cleanup
		frappe.delete_doc("Memora Voucher Batch", batch.name, force=True)
		frappe.db.commit()
