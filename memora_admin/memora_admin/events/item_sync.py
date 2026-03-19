# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Shared ERPNext service Item for Live Challenge Event purchases.

All paid events share a single LIVE-EVENT-ACCESS Item on invoices.
Event-specific details are captured in the invoice line description.
Contract: specs/052-live-event-purchase/contracts/item-auto-creation.yaml
"""

import frappe

LIVE_EVENT_ITEM_CODE = "LIVE-EVENT-ACCESS"


def ensure_shared_live_event_item():
	"""Create the LIVE-EVENT-ACCESS service Item if it doesn't exist.

	Idempotent: frappe.db.exists() prevents duplicate creation.
	Called lazily from invoice creation and eagerly from after_migrate.
	"""
	if frappe.db.exists("Item", LIVE_EVENT_ITEM_CODE):
		return

	item = frappe.get_doc({
		"doctype": "Item",
		"item_code": LIVE_EVENT_ITEM_CODE,
		"item_name": "Live Event Access",
		"item_group": "Services",
		"stock_uom": "Nos",
		"is_stock_item": 0,
		"is_sales_item": 1,
		"include_item_in_manufacturing": 0,
		"description": "Access ticket for Memora live challenge events",
	})
	item.insert(ignore_permissions=True)
	frappe.db.commit()
