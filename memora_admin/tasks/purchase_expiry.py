# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Scheduled job: cancel expired pending Live Event Purchases.

Runs every 5 minutes via cron (hooks.py).  Executes a single atomic
UPDATE targeting all pending purchases whose 30-minute expiry window
has elapsed, enabling students to create new purchases for the same event.

Contract: specs/052-live-event-purchase/contracts/purchase-expiry.yaml
"""

import frappe


def cancel_expired_purchases():
	"""Cancel all pending purchases past their expires_at deadline."""
	frappe.db.sql("""
		UPDATE `tabMemora Live Event Purchase`
		SET status = 'cancelled', modified = NOW(), modified_by = 'Administrator'
		WHERE status = 'pending' AND expires_at < NOW()
	""")
	count = frappe.db._cursor.rowcount
	frappe.db.commit()

	if count:
		frappe.logger(__name__).info(f"Cancelled {count} expired pending purchases")
