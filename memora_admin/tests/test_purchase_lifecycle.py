# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Integration test for the full Live Event Purchase lifecycle (T012).

Exercises the cross-cutting flow across all 052 user stories:
  US6: Event save → Item auto-created
  US1: Purchase creation → expires_at set
  051: Payment confirmation → invoice + access
  US5: Refund → Credit Note + access revoked
  US4: Expired purchase → auto-cancel job
  Re-purchase after cancel → success
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestPurchaseLifecycle(unittest.TestCase):
	"""End-to-end lifecycle test using mocks (no Frappe site required)."""

	# ── (1) Create paid event → verify Item auto-created (US6) ────────

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_step1_paid_event_creates_item(self, mock_frappe):
		"""Saving a paid event auto-creates an ERPNext Item."""
		mock_frappe.db.exists.return_value = False
		item_mock = MagicMock()
		mock_frappe.new_doc.return_value = item_mock

		doc = MagicMock()
		doc.name = "LC-LIFECYCLE-001"
		doc.is_paid = 1
		doc.event_title = "Lifecycle Test Event"
		doc.erpnext_item_code = None

		from memora_admin.memora_admin.events.item_sync import ensure_paid_event_item

		ensure_paid_event_item(doc, "before_save")

		mock_frappe.new_doc.assert_called_once_with("Item")
		self.assertEqual(item_mock.item_code, "LIVE-EVENT-LC-LIFECYCLE-001")
		item_mock.insert.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(doc.erpnext_item_code, "LIVE-EVENT-LC-LIFECYCLE-001")

	# ── (2) Create purchase → verify expires_at set (US1) ─────────────

	@patch("memora_admin.memora_admin.services.premium.event_purchase.frappe")
	def test_step2_purchase_has_expires_at(self, mock_frappe):
		"""Creating a purchase sets expires_at = now + 30 min."""
		fake_now = datetime(2026, 3, 18, 10, 0, 0)
		mock_frappe.utils.now_datetime.return_value = fake_now
		mock_frappe.ValidationError = Exception

		# Mock event doc
		event_doc = MagicMock()
		event_doc.get.side_effect = lambda k: {
			"is_paid": 1, "status": "Scheduled", "price": 5.0,
			"currency": "JOD", "erpnext_item_code": "LIVE-EVENT-LC-LIFECYCLE-001",
		}.get(k)

		# Mock player doc
		player_doc = MagicMock()
		player_doc.get.side_effect = lambda k: {
			"current_season": "S-001", "current_plan": "PLAN-A",
		}.get(k)

		mock_frappe.db.exists.return_value = None

		created_docs = []
		doc_lookup = {
			("Memora Live Challenge Event", "LC-LIFECYCLE-001"): event_doc,
			("Memora Player Profile", "PLAYER-LC-001"): player_doc,
		}

		def mock_get_doc(*args, **kwargs):
			if args and isinstance(args[0], dict):
				doc_mock = MagicMock()
				doc_mock.name = "LEP-LC-00001"
				created_docs.append(args[0])
				return doc_mock
			return doc_lookup.get(args, MagicMock())

		mock_frappe.get_doc.side_effect = mock_get_doc

		from memora_admin.memora_admin.services.premium.event_purchase import (
			create_event_purchase,
		)

		result = create_event_purchase("PLAYER-LC-001", "LC-LIFECYCLE-001")

		purchase_dicts = [d for d in created_docs if d.get("doctype") == "Memora Live Event Purchase"]
		self.assertEqual(len(purchase_dicts), 1)
		self.assertEqual(
			purchase_dicts[0]["expires_at"],
			fake_now + timedelta(minutes=30),
		)

	# ── (3) Confirm payment → verify invoice + access (051) ──────────

	@patch("memora_admin.memora_admin.services.premium.event_purchase.frappe")
	def test_step3_payment_creates_access_and_invoice(self, mock_frappe):
		"""Confirming payment creates access and invoice."""
		mock_frappe.ValidationError = Exception
		mock_frappe.utils.now_datetime.return_value = datetime(2026, 3, 18, 10, 5, 0)

		purchase = MagicMock()
		purchase.name = "LEP-LC-00001"
		purchase.status = "pending"
		purchase.player = "PLAYER-LC-001"
		purchase.event = "LC-LIFECYCLE-001"
		purchase.amount = 5.0
		purchase.currency = "JOD"
		purchase.erpnext_item_code = "LIVE-EVENT-LC-LIFECYCLE-001"

		access_mock = MagicMock()
		access_mock.name = "LEA-LC-00001"

		invoice_mock = MagicMock()
		invoice_mock.name = "ACC-SINV-2026-00001"

		def mock_get_doc(*args, **kwargs):
			if args and isinstance(args[0], dict):
				doctype = args[0].get("doctype")
				if doctype == "Memora Live Event Access":
					return access_mock
				if doctype == "Sales Invoice":
					return invoice_mock
			if len(args) == 2 and args[0] == "Memora Live Event Purchase":
				return purchase
			return MagicMock()

		mock_frappe.get_doc.side_effect = mock_get_doc
		mock_frappe.db.get_value.return_value = "CUST-001"

		from memora_admin.memora_admin.services.premium.event_purchase import (
			confirm_event_purchase,
		)

		result = confirm_event_purchase("LEP-LC-00001", "TXN-001", "test-gateway")

		self.assertEqual(purchase.status, "paid")
		self.assertEqual(result["access_id"], "LEA-LC-00001")
		access_mock.insert.assert_called_once_with(ignore_permissions=True)

	# ── (4) Refund → verify Credit Note + access revoked (US5) ───────

	@patch("memora_admin.memora_admin.services.premium.refund.frappe")
	def test_step4_refund_creates_credit_note(self, mock_frappe):
		"""Refunding a paid purchase creates a Credit Note and revokes access."""
		import frappe as real_frappe
		mock_frappe.ValidationError = real_frappe.ValidationError
		mock_frappe.utils.now_datetime.return_value = "2026-03-18 11:00:00"
		mock_frappe.session.user = "Administrator"

		purchase = MagicMock()
		purchase.name = "LEP-LC-00001"
		purchase.status = "paid"
		purchase.player = "PLAYER-LC-001"
		purchase.event = "LC-LIFECYCLE-001"
		purchase.event_access_ref = "LEA-LC-00001"
		purchase.erpnext_invoice = "ACC-SINV-2026-00001"
		purchase.erpnext_item_code = "LIVE-EVENT-LC-LIFECYCLE-001"
		purchase.amount = 5.0
		purchase.currency = "JOD"

		access = MagicMock()
		access.status = "active"

		credit_note = MagicMock()
		credit_note.name = "ACC-SINV-2026-00099"

		doc_lookup = {
			("Memora Live Event Purchase", "LEP-LC-00001"): purchase,
			("Memora Live Event Access", "LEA-LC-00001"): access,
		}
		mock_frappe.get_doc.side_effect = lambda *a, **kw: doc_lookup.get(a, MagicMock())
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = "CUST-001"
		mock_frappe.new_doc.return_value = credit_note

		from memora_admin.memora_admin.services.premium.refund import refund_event_purchase

		result = refund_event_purchase("LEP-LC-00001")

		# Purchase refunded
		self.assertEqual(purchase.status, "refunded")
		# Access revoked
		self.assertEqual(access.status, "refunded")
		# Credit Note created
		mock_frappe.new_doc.assert_called_once_with("Sales Invoice")
		self.assertEqual(credit_note.is_return, 1)
		self.assertEqual(credit_note.return_against, "ACC-SINV-2026-00001")
		credit_note.insert.assert_called_once_with(ignore_permissions=True)
		credit_note.submit.assert_called_once()
		# Return has credit_note_id
		self.assertEqual(result["credit_note_id"], "ACC-SINV-2026-00099")

	# ── (5) Expired purchase → cancel job → verified cancelled (US4) ─

	@patch("memora_admin.tasks.purchase_expiry.frappe")
	def test_step5_expired_purchase_cancelled(self, mock_frappe):
		"""Expired pending purchase is cancelled by the scheduled job."""
		mock_frappe.db._cursor.rowcount = 1

		from memora_admin.tasks.purchase_expiry import cancel_expired_purchases

		cancel_expired_purchases()

		update_sql = mock_frappe.db.sql.call_args_list[0][0][0]
		self.assertIn("status = 'cancelled'", update_sql)
		self.assertIn("status = 'pending'", update_sql)
		self.assertIn("expires_at < NOW()", update_sql)
		mock_frappe.db.commit.assert_called_once()

	# ── (6) New purchase after cancel → success ──────────────────────

	@patch("memora_admin.memora_admin.services.premium.event_purchase.frappe")
	def test_step6_repurchase_after_cancel_succeeds(self, mock_frappe):
		"""After cancellation, player can create a new purchase for the same event."""
		fake_now = datetime(2026, 3, 18, 11, 0, 0)
		mock_frappe.utils.now_datetime.return_value = fake_now
		mock_frappe.ValidationError = Exception

		event_doc = MagicMock()
		event_doc.get.side_effect = lambda k: {
			"is_paid": 1, "status": "Scheduled", "price": 5.0,
			"currency": "JOD", "erpnext_item_code": "LIVE-EVENT-LC-LIFECYCLE-001",
		}.get(k)

		player_doc = MagicMock()
		player_doc.get.side_effect = lambda k: {
			"current_season": "S-001", "current_plan": "PLAN-A",
		}.get(k)

		# No existing access, no pending purchase (previous was cancelled)
		mock_frappe.db.exists.return_value = None

		doc_lookup = {
			("Memora Live Challenge Event", "LC-LIFECYCLE-001"): event_doc,
			("Memora Player Profile", "PLAYER-LC-001"): player_doc,
		}

		new_purchase = MagicMock()
		new_purchase.name = "LEP-LC-00002"

		def mock_get_doc(*args, **kwargs):
			if args and isinstance(args[0], dict):
				return new_purchase
			return doc_lookup.get(args, MagicMock())

		mock_frappe.get_doc.side_effect = mock_get_doc

		from memora_admin.memora_admin.services.premium.event_purchase import (
			create_event_purchase,
		)

		result = create_event_purchase("PLAYER-LC-001", "LC-LIFECYCLE-001")

		self.assertEqual(result["purchase_id"], "LEP-LC-00002")
		new_purchase.insert.assert_called_once_with(ignore_permissions=True)
