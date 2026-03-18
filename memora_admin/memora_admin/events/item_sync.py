# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Doc event hook: auto-create ERPNext Item for paid Live Challenge Events.

Registered as before_save on Memora Live Challenge Event (hooks.py).
Contract: specs/052-live-event-purchase/contracts/item-auto-creation.yaml
"""

import frappe


def ensure_paid_event_item(doc, method):
	"""Create an ERPNext Item when a Live Challenge Event is saved with is_paid=1.

	- Idempotent: frappe.db.exists() prevents duplicate creation (SC-006)
	- FR-014: When is_paid=0, does nothing — never deletes existing items
	- Sets doc.erpnext_item_code in before_save so it's included in the DB write
	"""
	if not doc.is_paid:
		return

	item_code = f"LIVE-EVENT-{doc.name}"

	if frappe.db.exists("Item", item_code):
		doc.erpnext_item_code = item_code
		return

	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = f"Live Event Ticket: {doc.event_title or doc.name}"
	item.item_group = "Services"
	item.stock_uom = "Nos"
	item.is_stock_item = 0
	item.is_sales_item = 1
	item.include_item_in_manufacturing = 0
	item.description = f"Ticket for live event {doc.name}"
	item.insert(ignore_permissions=True)

	doc.erpnext_item_code = item_code
