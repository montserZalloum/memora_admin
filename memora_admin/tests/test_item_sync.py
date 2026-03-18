# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Unit tests for ERPNext Item auto-creation on paid Live Challenge Events (US6).

Tests cover:
- FR-013: Auto-create Item when is_paid=1
- FR-014: Never delete items when is_paid toggles to 0
- Idempotency: saving paid event twice creates only one item
- Item code format: LIVE-EVENT-{doc.name}
- Item name uses event title
"""

import unittest
from unittest.mock import MagicMock, patch


class TestEnsurePaidEventItem(unittest.TestCase):
	"""Tests for ensure_paid_event_item() doc event handler."""

	def _make_event_doc(self, **overrides):
		"""Create a mock Live Challenge Event doc with sensible defaults."""
		doc = MagicMock()
		doc.name = "LC-00042"
		doc.is_paid = 1
		doc.event_title = "Math Championship Finals"
		doc.erpnext_item_code = None
		for k, v in overrides.items():
			setattr(doc, k, v)
		return doc

	# -- T010(1): test_creates_item_for_paid_event ---------------------

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_creates_item_for_paid_event(self, mock_frappe):
		"""When is_paid=1 and item doesn't exist, creates ERPNext Item with correct fields."""
		mock_frappe.db.exists.return_value = False
		item_mock = MagicMock()
		mock_frappe.new_doc.return_value = item_mock

		doc = self._make_event_doc()

		from memora_admin.memora_admin.events.item_sync import ensure_paid_event_item

		ensure_paid_event_item(doc, "before_save")

		# Item created via frappe.new_doc
		mock_frappe.new_doc.assert_called_once_with("Item")

		# Verify Item fields
		self.assertEqual(item_mock.item_code, "LIVE-EVENT-LC-00042")
		self.assertEqual(item_mock.item_group, "Services")
		self.assertEqual(item_mock.stock_uom, "Nos")
		self.assertEqual(item_mock.is_stock_item, 0)
		self.assertEqual(item_mock.is_sales_item, 1)
		self.assertEqual(item_mock.include_item_in_manufacturing, 0)
		self.assertEqual(item_mock.description, "Ticket for live event LC-00042")

		# Item was inserted
		item_mock.insert.assert_called_once_with(ignore_permissions=True)

		# erpnext_item_code set on event doc
		self.assertEqual(doc.erpnext_item_code, "LIVE-EVENT-LC-00042")

	# -- T010(2): test_idempotent_no_duplicate -------------------------

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_idempotent_no_duplicate(self, mock_frappe):
		"""When item already exists, no new item is created but erpnext_item_code is still set."""
		mock_frappe.db.exists.return_value = True

		doc = self._make_event_doc(erpnext_item_code="LIVE-EVENT-LC-00042")

		from memora_admin.memora_admin.events.item_sync import ensure_paid_event_item

		ensure_paid_event_item(doc, "before_save")

		# No new Item created
		mock_frappe.new_doc.assert_not_called()

		# erpnext_item_code still set
		self.assertEqual(doc.erpnext_item_code, "LIVE-EVENT-LC-00042")

	# -- T010(3): test_noop_for_free_event -----------------------------

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_noop_for_free_event(self, mock_frappe):
		"""When is_paid=0, no item creation and no changes to erpnext_item_code."""
		doc = self._make_event_doc(is_paid=0, erpnext_item_code=None)

		from memora_admin.memora_admin.events.item_sync import ensure_paid_event_item

		ensure_paid_event_item(doc, "before_save")

		# No DB check, no item creation
		mock_frappe.db.exists.assert_not_called()
		mock_frappe.new_doc.assert_not_called()

		# erpnext_item_code unchanged
		self.assertIsNone(doc.erpnext_item_code)

	# -- T010(4): test_item_code_format --------------------------------

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_item_code_format(self, mock_frappe):
		"""Item code follows LIVE-EVENT-{doc.name} pattern."""
		mock_frappe.db.exists.return_value = False
		mock_frappe.new_doc.return_value = MagicMock()

		doc = self._make_event_doc(name="LC-99999")

		from memora_admin.memora_admin.events.item_sync import ensure_paid_event_item

		ensure_paid_event_item(doc, "before_save")

		# Check exists was called with correct item code
		mock_frappe.db.exists.assert_called_once_with("Item", "LIVE-EVENT-LC-99999")

		# erpnext_item_code matches pattern
		self.assertEqual(doc.erpnext_item_code, "LIVE-EVENT-LC-99999")

	# -- T010(5): test_item_name_uses_title ----------------------------

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_item_name_uses_title(self, mock_frappe):
		"""Item name is 'Live Event Ticket: {event_title}'."""
		mock_frappe.db.exists.return_value = False
		item_mock = MagicMock()
		mock_frappe.new_doc.return_value = item_mock

		doc = self._make_event_doc(event_title="Math Championship Finals")

		from memora_admin.memora_admin.events.item_sync import ensure_paid_event_item

		ensure_paid_event_item(doc, "before_save")

		self.assertEqual(item_mock.item_name, "Live Event Ticket: Math Championship Finals")

	# -- Edge: test_item_name_fallback_to_doc_name ---------------------

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_item_name_fallback_to_doc_name(self, mock_frappe):
		"""When event_title is empty, falls back to doc.name for item_name."""
		mock_frappe.db.exists.return_value = False
		item_mock = MagicMock()
		mock_frappe.new_doc.return_value = item_mock

		doc = self._make_event_doc(event_title=None)

		from memora_admin.memora_admin.events.item_sync import ensure_paid_event_item

		ensure_paid_event_item(doc, "before_save")

		self.assertEqual(item_mock.item_name, "Live Event Ticket: LC-00042")

	# -- Edge: test_toggle_paid_off_preserves_item ---------------------

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_toggle_paid_off_preserves_item(self, mock_frappe):
		"""Toggling is_paid=0 does NOT delete existing item (FR-014)."""
		doc = self._make_event_doc(is_paid=0, erpnext_item_code="LIVE-EVENT-LC-00042")

		from memora_admin.memora_admin.events.item_sync import ensure_paid_event_item

		ensure_paid_event_item(doc, "before_save")

		# No deletion attempt
		mock_frappe.delete_doc.assert_not_called()
		# erpnext_item_code is NOT cleared
		self.assertEqual(doc.erpnext_item_code, "LIVE-EVENT-LC-00042")
