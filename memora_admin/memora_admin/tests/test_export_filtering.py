"""Tests for export_for_print() CSV filtering — only Available cards should be exported.

Covers:
- US1: Export excludes non-Available cards (Redeemed, Void, Expired, Allocated)
- US2: Export log card_count reflects actual filtered count
- US3: Export raises error when no Available cards remain
- Edge cases: mixed statuses, CSV format preservation
"""

import csv
import io

import frappe
from memora_admin.memora_admin.api.voucher import export_for_print
from memora_admin.memora_admin.tests.voucher_fixtures import make_batch, make_product_grant
from memora_admin.memora_admin.tests.voucher_helpers import generate_batch_sync
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestExportFiltering(VoucherTestCase):
	"""Test that export_for_print() only includes Available cards."""

	BATCH_SIZE = 5

	def setUp(self):
		"""Generate a 5-card batch for each test."""
		self.grant = make_product_grant(season="SEAS-00027")
		self.batch = make_batch(grants=[self.grant.name], quantity=self.BATCH_SIZE)
		generate_batch_sync(self.batch.name)
		self.batch.reload()

		# Collect serial numbers from the batch
		self.serial_nos = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": self.batch.name},
			pluck="serial_no",
			order_by="serial_no asc",
		)

	def _set_card_status(self, serial_no, status):
		"""Set a card's status via direct SQL UPDATE."""
		frappe.db.sql(
			"UPDATE `tabMemora Voucher Card` SET status = %s WHERE serial_no = %s",
			(status, serial_no),
		)

	def _export_and_parse(self):
		"""Call export_for_print() and return parsed CSV rows."""
		frappe.set_user("Administrator")
		export_for_print(self.batch.name)

		csv_content = frappe.local.response.filecontent
		if isinstance(csv_content, bytes):
			csv_content = csv_content.decode("utf-8")

		reader = csv.DictReader(io.StringIO(csv_content))
		return list(reader)

	def _get_serial_nos(self):
		"""Return list of serial_nos from the batch."""
		return self.serial_nos

	# ── US1 Tests ──────────────────────────────────────────────────────────

	def test_export_excludes_redeemed_cards(self):
		"""US1/T002: Redeemed cards must not appear in the export CSV."""
		serials = self._get_serial_nos()
		self._set_card_status(serials[0], "Redeemed")
		self._set_card_status(serials[1], "Redeemed")

		rows = self._export_and_parse()
		self.assertEqual(len(rows), 3)

		exported_serials = {row["serial_no"] for row in rows}
		for s in exported_serials:
			status = frappe.db.get_value("Memora Voucher Card", {"serial_no": s}, "status")
			self.assertEqual(status, "Available")

	def test_export_excludes_void_and_expired_cards(self):
		"""US1/T003: Void and Expired cards must not appear in the export CSV."""
		serials = self._get_serial_nos()
		self._set_card_status(serials[0], "Void")
		self._set_card_status(serials[1], "Expired")

		rows = self._export_and_parse()
		self.assertEqual(len(rows), 3)

	def test_export_excludes_allocated_cards(self):
		"""US1/T004: Allocated cards must not appear in the export CSV (FR-002)."""
		serials = self._get_serial_nos()
		self._set_card_status(serials[0], "Allocated")
		self._set_card_status(serials[1], "Allocated")

		rows = self._export_and_parse()
		self.assertEqual(len(rows), 3)

	def test_export_all_available_no_regression(self):
		"""US1/T005: When all cards are Available, all appear in CSV (regression guard)."""
		rows = self._export_and_parse()
		self.assertEqual(len(rows), 5)

	# ── US2 Tests ──────────────────────────────────────────────────────────

	def test_export_log_count_matches_filtered(self):
		"""US2/T007: export_log card_count must match actual filtered CSV row count."""
		serials = self._get_serial_nos()
		self._set_card_status(serials[0], "Redeemed")
		self._set_card_status(serials[1], "Redeemed")

		self._export_and_parse()

		self.batch.reload()
		last_log = self.batch.export_log[-1]
		self.assertEqual(last_log.card_count, 3)

	# ── US3 Tests ──────────────────────────────────────────────────────────

	def test_export_no_available_cards_throws(self):
		"""US3/T009: Export must raise when all cards are non-Available."""
		serials = self._get_serial_nos()
		for s in serials:
			self._set_card_status(s, "Redeemed")

		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError) as ctx:
			export_for_print(self.batch.name)

		self.assertIn("No available cards", str(ctx.exception))

	# ── Polish Tests ───────────────────────────────────────────────────────

	def test_export_mixed_statuses(self):
		"""T011: Only the single Available card appears when all others have different non-Available statuses."""
		serials = self._get_serial_nos()
		self._set_card_status(serials[0], "Allocated")
		self._set_card_status(serials[1], "Redeemed")
		self._set_card_status(serials[2], "Void")
		self._set_card_status(serials[3], "Expired")
		# serials[4] stays Available

		rows = self._export_and_parse()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["serial_no"], serials[4])

	def test_export_csv_format_preserved(self):
		"""T012: CSV header and row format must be exactly [serial_no, pin, product_names, face_value]."""
		rows = self._export_and_parse()

		# Verify header columns
		expected_columns = ["serial_no", "pin", "product_names", "face_value"]
		self.assertEqual(list(rows[0].keys()), expected_columns)

		# Verify each row has non-empty values for all 4 columns
		for row in rows:
			for col in expected_columns:
				self.assertTrue(row[col], f"Column '{col}' should not be empty in row {row}")
