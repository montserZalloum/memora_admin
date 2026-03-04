# Copyright (c) 2026, corex and Contributors
# See license.txt

import csv
import io
import os
import re
from unittest.mock import patch

import frappe

from memora_admin.memora_admin.api.voucher import (
	MAX_BATCH_QUANTITY,
	export_for_print,
	generate_batch,
	generate_cards_job,
)
from memora_admin.memora_admin.tests.voucher_fixtures import make_batch, make_product_grant
from memora_admin.memora_admin.tests.voucher_helpers import (
	assert_batch_counters,
	generate_batch_sync,
	get_card_statuses,
)
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestMemoraVoucherBatch(VoucherTestCase):
	def setUp(self):
		"""Create common fixtures for most tests."""
		self.grant = make_product_grant(season="SEAS-00027")
		self.batch = make_batch(grants=[self.grant.name], quantity=10)

	# User Story 1: Happy Path Generation Validation

	def test_generate_creates_cards(self):
		"""FR-001: Verify correct card count after generation."""
		generate_batch_sync(self.batch.name)
		statuses = get_card_statuses(self.batch.name)
		self.assertEqual(statuses, {"Available": 10})

	def test_generate_status_transition(self):
		"""FR-002: Verify batch status transitions from Draft to Generated."""
		generate_batch_sync(self.batch.name)
		self.batch.reload()
		self.assertEqual(self.batch.status, "Generated")

	def test_generate_counters(self):
		"""FR-003: Verify counter fields are accurate after generation."""
		generate_batch_sync(self.batch.name)
		assert_batch_counters(
			self,
			self.batch.name,
			generated_count=10,
			allocated_count=0,
			redeemed_count=0,
			voided_count=0,
			expired_count=0,
		)

	def test_generate_encrypted_file(self):
		"""FR-004: Verify encrypted file is created and exists on disk."""
		generate_batch_sync(self.batch.name)
		self.batch.reload()
		self.assertTrue(self.batch.encrypted_file_url)
		file_path = frappe.get_site_path(self.batch.encrypted_file_url.lstrip("/"))
		self.assertTrue(os.path.exists(file_path))

	def test_generate_serial_format(self):
		"""FR-005: Verify all serial numbers match VCH-NNNNNN format."""
		generate_batch_sync(self.batch.name)
		cards = frappe.get_all(
			"Memora Voucher Card", filters={"batch": self.batch.name}, fields=["serial_no"]
		)
		serial_pattern = re.compile(r"^VCH-\d{6}$")
		for card in cards:
			self.assertIsNotNone(
				serial_pattern.match(card.serial_no),
				f"Serial {card.serial_no} does not match VCH-NNNNNN format",
			)

	def test_generate_hmac_stored(self):
		"""FR-006: Verify HMAC is stored and no plaintext PIN column exists."""
		generate_batch_sync(self.batch.name)
		cards = frappe.get_all(
			"Memora Voucher Card", filters={"batch": self.batch.name}, fields=["name", "pin_hmac"]
		)
		# Verify every card has a non-empty HMAC
		for card in cards:
			self.assertTrue(card.pin_hmac, f"Card {card.name} has empty pin_hmac")

		# Verify no 'pin' field exists in the DocType
		meta = frappe.get_meta("Memora Voucher Card")
		pin_field = meta.get_field("pin")
		self.assertIsNone(pin_field, "DocType should not have a 'pin' field")

	# User Story 2: Generation Guard Rails

	def test_generate_non_draft_fails(self):
		"""FR-007: Verify generation fails for non-Draft batches."""
		generate_batch_sync(self.batch.name)
		# Batch is now "Generated"
		with self.assertRaises(frappe.ValidationError):
			generate_batch(self.batch.name)

	def test_generate_zero_quantity_fails(self):
		"""FR-008: Verify generation fails for zero quantity."""
		batch = make_batch(grants=[self.grant.name], quantity=0)
		with self.assertRaises(frappe.ValidationError):
			generate_batch(batch.name)

	def test_generate_exceeds_max_fails(self):
		"""FR-009: Verify generation fails when quantity exceeds maximum."""
		batch = make_batch(grants=[self.grant.name], quantity=1001)
		with self.assertRaises(frappe.ValidationError):
			generate_batch(batch.name)

	def test_generate_no_hmac_secret_fails(self):
		"""FR-010: Verify generation fails when HMAC secret is missing."""
		# Store original secret
		original_secret = frappe.conf.get("voucher_hmac_secret")
		try:
			# Remove secret
			frappe.conf.voucher_hmac_secret = ""
			with self.assertRaises(frappe.ValidationError):
				generate_batch(self.batch.name)
		finally:
			# Restore original secret
			if original_secret:
				frappe.conf.voucher_hmac_secret = original_secret

	def test_generate_already_generated_fails(self):
		"""FR-013: Verify re-generation fails for already-generated batches."""
		generate_batch_sync(self.batch.name)
		# Batch is now "Generated"
		with self.assertRaises(frappe.ValidationError):
			generate_batch(self.batch.name)

	# User Story 3: Export and Audit Trail

	def test_export_decrypts_correctly(self):
		"""FR-011: Verify encrypted export decrypts to valid CSV."""
		generate_batch_sync(self.batch.name)
		self.batch.reload()

		# Set System Manager role for export
		frappe.set_user("Administrator")

		# Call export function
		export_for_print(self.batch.name)

		# Read CSV from response
		csv_content = frappe.local.response.filecontent
		self.assertTrue(csv_content, "Export should return CSV content")

		# Decode bytes to string if necessary
		if isinstance(csv_content, bytes):
			csv_content = csv_content.decode("utf-8")

		# Parse CSV
		csv_reader = csv.DictReader(io.StringIO(csv_content))
		rows = list(csv_reader)

		# Verify row count matches card count
		self.assertEqual(len(rows), 10, "CSV should have 10 rows")

		# Verify serial numbers exist in DB
		for row in rows:
			serial_no = row.get("serial_no")
			self.assertTrue(serial_no, "CSV row should have serial_no")
			card = frappe.get_value("Memora Voucher Card", {"serial_no": serial_no}, "name")
			self.assertTrue(card, f"Serial {serial_no} should exist in database")

		# Verify PIN column exists (decrypted)
		for row in rows:
			self.assertIn("pin", row, "CSV should have 'pin' column")
			self.assertTrue(row["pin"], "PIN should not be empty")

	def test_export_audit_logged(self):
		"""FR-012: Verify export actions are audit-logged."""
		generate_batch_sync(self.batch.name)
		self.batch.reload()

		# Count initial export log entries
		initial_log_count = len(self.batch.export_log) if self.batch.export_log else 0

		# Set System Manager role for export
		frappe.set_user("Administrator")

		# Call export function
		export_for_print(self.batch.name)

		# Reload and verify log entry added
		self.batch.reload()
		new_log_count = len(self.batch.export_log) if self.batch.export_log else 0
		self.assertEqual(new_log_count, initial_log_count + 1, "Export log should have one new entry")

	# User Story 4: Rollback on Failure

	def test_generate_rollback_on_failure(self):
		"""FR-014: Verify failed generation leaves no partial data."""
		batch_name = self.batch.name

		# Commit the batch creation to ensure it persists across the rollback
		frappe.db.commit()

		with patch("frappe.db.bulk_insert", side_effect=Exception("simulated failure")):
			try:
				generate_cards_job(batch_name)
			except Exception:
				pass  # Expected to fail

		# Verify no cards were created
		statuses = get_card_statuses(batch_name)
		self.assertEqual(statuses, {}, "No cards should exist after failed generation")

		# Verify batch status is still Draft (query DB directly)
		batch_status = frappe.db.get_value("Memora Voucher Batch", batch_name, "status")
		self.assertEqual(batch_status, "Draft", "Batch should remain in Draft status after failure")
