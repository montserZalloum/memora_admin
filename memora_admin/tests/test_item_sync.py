# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Unit tests for shared LIVE-EVENT-ACCESS ERPNext Item.

Tests cover:
- Shared item creation when it doesn't exist
- Idempotency: no-op when item already exists
- Constant value check
"""

import unittest
from unittest.mock import MagicMock, patch


class TestEnsureSharedLiveEventItem(unittest.TestCase):
	"""Tests for ensure_shared_live_event_item() and LIVE_EVENT_ITEM_CODE."""

	def test_constant_value(self):
		"""LIVE_EVENT_ITEM_CODE is LIVE-EVENT-ACCESS."""
		from memora_admin.memora_admin.events.item_sync import LIVE_EVENT_ITEM_CODE

		self.assertEqual(LIVE_EVENT_ITEM_CODE, "LIVE-EVENT-ACCESS")

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_ensure_shared_item_creates_when_not_exists(self, mock_frappe):
		"""Creates LIVE-EVENT-ACCESS with correct fields when it doesn't exist."""
		mock_frappe.db.exists.return_value = False
		item_mock = MagicMock()
		mock_frappe.get_doc.return_value = item_mock

		from memora_admin.memora_admin.events.item_sync import ensure_shared_live_event_item

		ensure_shared_live_event_item()

		mock_frappe.db.exists.assert_called_once_with("Item", "LIVE-EVENT-ACCESS")
		mock_frappe.get_doc.assert_called_once()

		doc_dict = mock_frappe.get_doc.call_args[0][0]
		self.assertEqual(doc_dict["doctype"], "Item")
		self.assertEqual(doc_dict["item_code"], "LIVE-EVENT-ACCESS")
		self.assertEqual(doc_dict["item_name"], "Live Event Access")
		self.assertEqual(doc_dict["item_group"], "Services")
		self.assertEqual(doc_dict["stock_uom"], "Nos")
		self.assertEqual(doc_dict["is_stock_item"], 0)
		self.assertEqual(doc_dict["is_sales_item"], 1)
		self.assertEqual(doc_dict["include_item_in_manufacturing"], 0)

		item_mock.insert.assert_called_once_with(ignore_permissions=True)
		mock_frappe.db.commit.assert_called_once()

	@patch("memora_admin.memora_admin.events.item_sync.frappe")
	def test_ensure_shared_item_noop_when_exists(self, mock_frappe):
		"""Short-circuits when LIVE-EVENT-ACCESS already exists."""
		mock_frappe.db.exists.return_value = True

		from memora_admin.memora_admin.events.item_sync import ensure_shared_live_event_item

		ensure_shared_live_event_item()

		mock_frappe.db.exists.assert_called_once_with("Item", "LIVE-EVENT-ACCESS")
		mock_frappe.get_doc.assert_not_called()
		mock_frappe.db.commit.assert_not_called()
