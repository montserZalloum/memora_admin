# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Unit tests for Memora Live Event Purchase expiry.

Tests cover:
- FR-001: 30-minute expiry window set at purchase creation
- expires_at field is read-only (admin cannot edit)
- expires_at only applies to pending purchases
- FR-010: Auto-cancellation of expired pending purchases (US4)
"""

from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch

import frappe


class TestPurchaseExpiry(unittest.TestCase):
	"""Tests for expires_at field assignment on Live Event Purchase."""

	# ── T003(1): test_expires_at_set_on_insert ──────────────────────────

	@patch("frappe.utils.now_datetime")
	def test_expires_at_set_on_insert(self, mock_now):
		"""before_insert sets expires_at to now + 30 minutes when not already set."""
		fake_now = datetime(2026, 3, 18, 10, 0, 0)
		mock_now.return_value = fake_now

		from memora_admin.memora_admin.doctype.memora_live_event_purchase.memora_live_event_purchase import (
			MemoraLiveEventPurchase,
		)

		doc = MagicMock(spec=MemoraLiveEventPurchase)
		doc.expires_at = None
		doc.status = "pending"

		MemoraLiveEventPurchase.before_insert(doc)

		expected = fake_now + timedelta(minutes=30)
		self.assertEqual(doc.expires_at, expected)

	@patch("frappe.utils.now_datetime")
	def test_expires_at_not_overwritten_if_already_set(self, mock_now):
		"""before_insert does not overwrite expires_at if the service layer already set it."""
		fake_now = datetime(2026, 3, 18, 10, 0, 0)
		mock_now.return_value = fake_now
		pre_set = fake_now + timedelta(minutes=30)

		from memora_admin.memora_admin.doctype.memora_live_event_purchase.memora_live_event_purchase import (
			MemoraLiveEventPurchase,
		)

		doc = MagicMock(spec=MemoraLiveEventPurchase)
		doc.expires_at = pre_set
		doc.status = "pending"

		MemoraLiveEventPurchase.before_insert(doc)

		self.assertEqual(doc.expires_at, pre_set)

	# ── T003(2): test_expires_at_readonly ────────────────────────────────

	def test_expires_at_readonly(self):
		"""The expires_at field is read-only in the DocType JSON definition."""
		import json
		import os

		json_path = os.path.join(
			os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
			"memora_admin",
			"doctype",
			"memora_live_event_purchase",
			"memora_live_event_purchase.json",
		)
		with open(json_path) as f:
			doctype_def = json.load(f)

		expires_field = next(
			(f for f in doctype_def["fields"] if f["fieldname"] == "expires_at"),
			None,
		)
		self.assertIsNotNone(expires_field, "expires_at field must exist in DocType JSON")
		self.assertEqual(expires_field.get("read_only"), 1, "expires_at must be read-only")
		self.assertEqual(expires_field.get("fieldtype"), "Datetime", "expires_at must be Datetime type")

	# ── T003(3): test_expires_at_only_for_pending ────────────────────────

	@patch("frappe.utils.now_datetime")
	def test_expires_at_only_for_pending(self, mock_now):
		"""before_insert does not set expires_at for non-pending statuses."""
		fake_now = datetime(2026, 3, 18, 10, 0, 0)
		mock_now.return_value = fake_now

		from memora_admin.memora_admin.doctype.memora_live_event_purchase.memora_live_event_purchase import (
			MemoraLiveEventPurchase,
		)

		for status in ("paid", "failed", "cancelled", "refunded"):
			doc = MagicMock(spec=MemoraLiveEventPurchase)
			doc.expires_at = None
			doc.status = status

			MemoraLiveEventPurchase.before_insert(doc)

			self.assertIsNone(
				doc.expires_at,
				f"expires_at should not be set for status '{status}'",
			)


class TestCreateEventPurchaseExpiry(unittest.TestCase):
	"""Tests for expires_at being set by create_event_purchase() service."""

	@patch("memora_admin.memora_admin.services.premium.event_purchase.frappe")
	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_create_event_purchase_sets_expires_at(self, _mock_item_frappe, mock_frappe):
		"""create_event_purchase() explicitly sets expires_at = now + 30 min on the doc."""
		fake_now = datetime(2026, 3, 18, 10, 0, 0)
		mock_frappe.utils.now_datetime.return_value = fake_now
		mock_frappe.ValidationError = frappe.ValidationError

		# Mock event doc
		event_doc = MagicMock()
		event_doc.get.side_effect = lambda k: {
			"is_paid": 1, "status": "Scheduled", "price": 5.0,
			"currency": "JOD",
		}.get(k)

		# Mock player doc
		player_doc = MagicMock()
		player_doc.get.side_effect = lambda k: {
			"current_season": "S-001", "current_plan": "PLAN-A",
		}.get(k)

		# Mock no existing access or pending purchase
		mock_frappe.db.exists.return_value = None

		# Capture the doc dict passed to frappe.get_doc for insert
		created_docs = []
		doc_lookup = {
			("Memora Live Challenge Event", "EV-001"): event_doc,
			("Memora Player Profile", "PLAYER-001"): player_doc,
		}

		def mock_get_doc(*args, **kwargs):
			if args and isinstance(args[0], dict):
				doc_mock = MagicMock()
				doc_mock.name = "LEP-00001"
				created_docs.append(args[0])
				return doc_mock
			return doc_lookup.get(args, MagicMock())

		mock_frappe.get_doc.side_effect = mock_get_doc

		from memora_admin.memora_admin.services.premium.event_purchase import (
			create_event_purchase,
		)

		create_event_purchase("PLAYER-001", "EV-001")

		# Find the purchase doc dict
		purchase_dicts = [d for d in created_docs if d.get("doctype") == "Memora Live Event Purchase"]
		self.assertEqual(len(purchase_dicts), 1, "Should create exactly one purchase doc")

		expected_expires = fake_now + timedelta(minutes=30)
		self.assertEqual(
			purchase_dicts[0]["expires_at"],
			expected_expires,
			"create_event_purchase must set expires_at = now + 30 min",
		)


class TestCancelExpiredPurchases(unittest.TestCase):
	"""Tests for cancel_expired_purchases() scheduled job (US4 / FR-010).

	The job runs a single atomic UPDATE targeting pending purchases
	past their expires_at deadline.  Contract: purchase-expiry.yaml
	"""

	# ── T006(1): test_cancels_expired_pending ────────────────────────────

	@patch("memora_admin.tasks.purchase_expiry.frappe")
	def test_cancels_expired_pending(self, mock_frappe):
		"""Job executes UPDATE that cancels pending purchases past expires_at."""
		mock_frappe.db._cursor.rowcount = 1

		from memora_admin.tasks.purchase_expiry import cancel_expired_purchases

		cancel_expired_purchases()

		update_sql = mock_frappe.db.sql.call_args_list[0][0][0]
		self.assertIn("status = 'pending'", update_sql)
		self.assertIn("expires_at < NOW()", update_sql)
		self.assertIn("status = 'cancelled'", update_sql)
		self.assertIn("modified = NOW()", update_sql)
		self.assertIn("modified_by = 'Administrator'", update_sql)

		# Logging reports affected count
		mock_frappe.logger.return_value.info.assert_called_once()
		log_msg = mock_frappe.logger.return_value.info.call_args[0][0]
		self.assertIn("1", log_msg)

	# ── T006(2): test_ignores_non_pending ────────────────────────────────

	@patch("memora_admin.tasks.purchase_expiry.frappe")
	def test_ignores_non_pending(self, mock_frappe):
		"""WHERE clause filters strictly on status='pending'; paid/failed/refunded untouched."""
		mock_frappe.db._cursor.rowcount = 0

		from memora_admin.tasks.purchase_expiry import cancel_expired_purchases

		cancel_expired_purchases()

		update_sql = mock_frappe.db.sql.call_args_list[0][0][0]
		# Only pending — no OR clause that might widen the scope
		self.assertIn("status = 'pending'", update_sql)
		# When zero rows affected, no info log emitted
		mock_frappe.logger.return_value.info.assert_not_called()

	# ── T006(3): test_idempotent ─────────────────────────────────────────

	@patch("memora_admin.tasks.purchase_expiry.frappe")
	def test_idempotent(self, mock_frappe):
		"""Running the job twice causes no errors; second run finds zero matches."""
		rowcounts = iter([2, 0])

		def sql_side_effect(*args, **kwargs):
			mock_frappe.db._cursor.rowcount = next(rowcounts)

		mock_frappe.db.sql.side_effect = sql_side_effect

		from memora_admin.tasks.purchase_expiry import cancel_expired_purchases

		cancel_expired_purchases()
		cancel_expired_purchases()

		# 1 SQL call per run (UPDATE only, no separate ROW_COUNT query)
		self.assertEqual(mock_frappe.db.sql.call_count, 2)
		# commit called each run
		self.assertEqual(mock_frappe.db.commit.call_count, 2)

	# ── T006(4): test_batch_update ───────────────────────────────────────

	@patch("memora_admin.tasks.purchase_expiry.frappe")
	def test_batch_update(self, mock_frappe):
		"""Single SQL UPDATE handles multiple expired rows atomically."""
		mock_frappe.db._cursor.rowcount = 3

		from memora_admin.tasks.purchase_expiry import cancel_expired_purchases

		cancel_expired_purchases()

		# Exactly 1 SQL call (UPDATE only, no separate ROW_COUNT query)
		self.assertEqual(mock_frappe.db.sql.call_count, 1)
		# Log message reports the batch count
		log_msg = mock_frappe.logger.return_value.info.call_args[0][0]
		self.assertIn("3", log_msg)
