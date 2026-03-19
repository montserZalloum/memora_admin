# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Unit tests for Credit Note creation during live event purchase refund (US5 / FR-011).

Tests cover:
- Credit Note reads item_code and description from original invoice
- Refund succeeds without Credit Note when no invoice exists
- Exception propagates on Credit Note creation failure (Frappe rollback)
- Return dict includes credit_note_id field
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe


class TestRefundCreditNote(unittest.TestCase):
	"""Tests for Credit Note creation in refund_event_purchase()."""

	def _make_purchase_doc(self, **overrides):
		"""Create a mock purchase doc with sensible defaults."""
		doc = MagicMock()
		doc.name = "LEP-00042"
		doc.status = "paid"
		doc.player = "PLAYER-001"
		doc.event = "EV-001"
		doc.event_access_ref = "LEA-00042"
		doc.erpnext_invoice = "ACC-SINV-2026-00001"
		doc.amount = 5.0
		doc.currency = "JOD"
		for k, v in overrides.items():
			setattr(doc, k, v)
		return doc

	def _make_access_doc(self):
		"""Create a mock access doc."""
		doc = MagicMock()
		doc.status = "active"
		return doc

	def _make_original_invoice(self, item_code="LIVE-EVENT-ACCESS", description="Live Event Ticket: Math Finals (EV-001)"):
		"""Create a mock original Sales Invoice with one item row."""
		inv = MagicMock()
		inv.name = "ACC-SINV-2026-00001"
		item_row = MagicMock()
		item_row.item_code = item_code
		item_row.description = description
		inv.items = [item_row]
		return inv

	# ── T008(1): test_credit_note_created_on_refund ──────────────────────

	@patch("memora_admin.memora_admin.services.premium.refund.frappe")
	def test_credit_note_created_on_refund(self, mock_frappe):
		"""Refunding a purchase with erpnext_invoice creates a Credit Note
		using item_code and description from the original invoice."""
		mock_frappe.ValidationError = frappe.ValidationError
		mock_frappe.utils.now_datetime.return_value = "2026-03-18 10:00:00"
		mock_frappe.session.user = "Administrator"

		purchase = self._make_purchase_doc()
		access = self._make_access_doc()
		original_inv = self._make_original_invoice()
		credit_note = MagicMock()
		credit_note.name = "ACC-SINV-2026-00099"

		doc_lookup = {
			("Memora Live Event Purchase", "LEP-00042"): purchase,
			("Memora Live Event Access", "LEA-00042"): access,
			("Sales Invoice", "ACC-SINV-2026-00001"): original_inv,
		}
		mock_frappe.get_doc.side_effect = lambda *a, **kw: doc_lookup.get(a, MagicMock())
		mock_frappe.db.exists.return_value = True

		# new_doc returns the credit note mock
		mock_frappe.new_doc.return_value = credit_note

		# _get_player_customer returns a customer name
		mock_frappe.db.get_value.return_value = "CUST-001"

		from memora_admin.memora_admin.services.premium.refund import refund_event_purchase

		result = refund_event_purchase("LEP-00042")

		# Credit Note was created
		mock_frappe.new_doc.assert_called_once_with("Sales Invoice")

		# Verify Credit Note fields
		self.assertEqual(credit_note.customer, "CUST-001")
		self.assertEqual(credit_note.is_return, 1)
		self.assertEqual(credit_note.return_against, "ACC-SINV-2026-00001")
		self.assertEqual(credit_note.currency, "JOD")

		# Verify item row appended with item_code from original invoice
		credit_note.append.assert_called_once()
		append_args = credit_note.append.call_args
		self.assertEqual(append_args[0][0], "items")
		item_row = append_args[0][1]
		self.assertEqual(item_row["item_code"], "LIVE-EVENT-ACCESS")
		self.assertEqual(item_row["description"], "Live Event Ticket: Math Finals (EV-001)")
		self.assertEqual(item_row["qty"], -1)
		self.assertEqual(item_row["rate"], 5.0)

		# Credit Note was inserted and submitted
		credit_note.insert.assert_called_once_with(ignore_permissions=True)
		credit_note.submit.assert_called_once()

		# Return includes credit_note_id
		self.assertEqual(result["credit_note_id"], "ACC-SINV-2026-00099")

	# ── T008(1b): test_credit_note_backward_compat_old_item ──────────────

	@patch("memora_admin.memora_admin.services.premium.refund.frappe")
	def test_credit_note_backward_compat_old_item(self, mock_frappe):
		"""Old purchases with per-event item codes: credit note reads item from original invoice."""
		mock_frappe.ValidationError = frappe.ValidationError
		mock_frappe.utils.now_datetime.return_value = "2026-03-18 10:00:00"
		mock_frappe.session.user = "Administrator"

		purchase = self._make_purchase_doc()
		access = self._make_access_doc()
		# Old invoice with per-event item code
		original_inv = self._make_original_invoice(
			item_code="LIVE-EVENT-EV-001",
			description="Ticket for live event EV-001",
		)
		credit_note = MagicMock()
		credit_note.name = "ACC-SINV-2026-00100"

		doc_lookup = {
			("Memora Live Event Purchase", "LEP-00042"): purchase,
			("Memora Live Event Access", "LEA-00042"): access,
			("Sales Invoice", "ACC-SINV-2026-00001"): original_inv,
		}
		mock_frappe.get_doc.side_effect = lambda *a, **kw: doc_lookup.get(a, MagicMock())
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = "CUST-001"
		mock_frappe.new_doc.return_value = credit_note

		from memora_admin.memora_admin.services.premium.refund import refund_event_purchase

		result = refund_event_purchase("LEP-00042")

		# Uses item_code from original invoice, not a constant
		item_row = credit_note.append.call_args[0][1]
		self.assertEqual(item_row["item_code"], "LIVE-EVENT-EV-001")
		self.assertEqual(item_row["description"], "Ticket for live event EV-001")

	# ── T008(2): test_no_credit_note_without_invoice ─────────────────────

	@patch("memora_admin.memora_admin.services.premium.refund.frappe")
	def test_no_credit_note_without_invoice(self, mock_frappe):
		"""Refund succeeds with credit_note_id=None when purchase has no invoice."""
		mock_frappe.ValidationError = frappe.ValidationError
		mock_frappe.utils.now_datetime.return_value = "2026-03-18 10:00:00"
		mock_frappe.session.user = "Administrator"

		purchase = self._make_purchase_doc(erpnext_invoice=None)
		access = self._make_access_doc()

		doc_lookup = {
			("Memora Live Event Purchase", "LEP-00042"): purchase,
			("Memora Live Event Access", "LEA-00042"): access,
		}
		mock_frappe.get_doc.side_effect = lambda *a, **kw: doc_lookup.get(a, MagicMock())
		mock_frappe.db.exists.return_value = True

		from memora_admin.memora_admin.services.premium.refund import refund_event_purchase

		result = refund_event_purchase("LEP-00042")

		# No Credit Note created
		mock_frappe.new_doc.assert_not_called()

		# Refund still succeeded
		self.assertEqual(result["status"], "refunded")
		self.assertIsNone(result["credit_note_id"])

	# ── T008(3): test_rollback_on_credit_note_failure ────────────────────

	@patch("memora_admin.memora_admin.services.premium.refund.frappe")
	def test_rollback_on_credit_note_failure(self, mock_frappe):
		"""Credit Note insert failure propagates — Frappe transaction rolls back."""
		mock_frappe.ValidationError = frappe.ValidationError
		mock_frappe.utils.now_datetime.return_value = "2026-03-18 10:00:00"
		mock_frappe.session.user = "Administrator"

		purchase = self._make_purchase_doc()
		access = self._make_access_doc()
		original_inv = self._make_original_invoice()

		doc_lookup = {
			("Memora Live Event Purchase", "LEP-00042"): purchase,
			("Memora Live Event Access", "LEA-00042"): access,
			("Sales Invoice", "ACC-SINV-2026-00001"): original_inv,
		}
		mock_frappe.get_doc.side_effect = lambda *a, **kw: doc_lookup.get(a, MagicMock())
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = "CUST-001"

		# Credit Note insert raises
		credit_note = MagicMock()
		credit_note.insert.side_effect = Exception("GL Entry creation failed")
		mock_frappe.new_doc.return_value = credit_note

		from memora_admin.memora_admin.services.premium.refund import refund_event_purchase

		with self.assertRaises(Exception) as ctx:
			refund_event_purchase("LEP-00042")

		self.assertIn("GL Entry creation failed", str(ctx.exception))

	# ── T008(4): test_return_value_includes_credit_note_id ───────────────

	@patch("memora_admin.memora_admin.services.premium.refund.frappe")
	def test_return_value_includes_credit_note_id(self, mock_frappe):
		"""Return dict always contains credit_note_id key."""
		mock_frappe.ValidationError = frappe.ValidationError
		mock_frappe.utils.now_datetime.return_value = "2026-03-18 10:00:00"
		mock_frappe.session.user = "Administrator"

		purchase = self._make_purchase_doc()
		access = self._make_access_doc()
		original_inv = self._make_original_invoice()
		credit_note = MagicMock()
		credit_note.name = "ACC-SINV-2026-00099"

		doc_lookup = {
			("Memora Live Event Purchase", "LEP-00042"): purchase,
			("Memora Live Event Access", "LEA-00042"): access,
			("Sales Invoice", "ACC-SINV-2026-00001"): original_inv,
		}
		mock_frappe.get_doc.side_effect = lambda *a, **kw: doc_lookup.get(a, MagicMock())
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = "CUST-001"
		mock_frappe.new_doc.return_value = credit_note

		from memora_admin.memora_admin.services.premium.refund import refund_event_purchase

		result = refund_event_purchase("LEP-00042")

		# credit_note_id key must always be present
		self.assertIn("credit_note_id", result)
		self.assertEqual(result["credit_note_id"], "ACC-SINV-2026-00099")
		self.assertEqual(result["status"], "refunded")
		self.assertEqual(result["purchase_id"], "LEP-00042")
		self.assertEqual(result["access_id"], "LEA-00042")

	# ── Edge: test_no_credit_note_when_empty_string_invoice ──────────────

	@patch("memora_admin.memora_admin.services.premium.refund.frappe")
	def test_no_credit_note_when_empty_string_invoice(self, mock_frappe):
		"""Empty string erpnext_invoice is treated same as None — no credit note."""
		mock_frappe.ValidationError = frappe.ValidationError
		mock_frappe.utils.now_datetime.return_value = "2026-03-18 10:00:00"
		mock_frappe.session.user = "Administrator"

		purchase = self._make_purchase_doc(erpnext_invoice="")
		access = self._make_access_doc()

		doc_lookup = {
			("Memora Live Event Purchase", "LEP-00042"): purchase,
			("Memora Live Event Access", "LEA-00042"): access,
		}
		mock_frappe.get_doc.side_effect = lambda *a, **kw: doc_lookup.get(a, MagicMock())
		mock_frappe.db.exists.return_value = True

		from memora_admin.memora_admin.services.premium.refund import refund_event_purchase

		result = refund_event_purchase("LEP-00042")

		mock_frappe.new_doc.assert_not_called()
		self.assertIsNone(result["credit_note_id"])

	# ── Edge: test_no_credit_note_when_no_customer ───────────────────────

	@patch("memora_admin.memora_admin.services.premium.refund.frappe")
	def test_no_credit_note_when_no_customer(self, mock_frappe):
		"""Missing customer mapping skips credit note, logs warning, refund succeeds."""
		mock_frappe.ValidationError = frappe.ValidationError
		mock_frappe.utils.now_datetime.return_value = "2026-03-18 10:00:00"
		mock_frappe.session.user = "Administrator"

		purchase = self._make_purchase_doc()
		access = self._make_access_doc()

		doc_lookup = {
			("Memora Live Event Purchase", "LEP-00042"): purchase,
			("Memora Live Event Access", "LEA-00042"): access,
		}
		mock_frappe.get_doc.side_effect = lambda *a, **kw: doc_lookup.get(a, MagicMock())
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = None  # No customer mapping

		from memora_admin.memora_admin.services.premium.refund import refund_event_purchase

		result = refund_event_purchase("LEP-00042")

		# No Credit Note created
		mock_frappe.new_doc.assert_not_called()
		# Warning logged
		mock_frappe.log_error.assert_called()
		# Refund succeeded
		self.assertEqual(result["status"], "refunded")
		self.assertIsNone(result["credit_note_id"])
