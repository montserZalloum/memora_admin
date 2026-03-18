# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Live Event ticket purchase creation and confirmation service.

Handles the Frappe-side logic for creating a pending event purchase record
and confirming payment via webhook (R-008 invoice pattern).
"""

from datetime import timedelta

import frappe


def create_event_purchase(player: str, event: str) -> dict:
	"""Create a pending Live Event Purchase for a player.

	Validates:
	- Event is paid (is_paid=1)
	- Player doesn't already have active event access
	- Player doesn't already have a pending purchase for this event

	Args:
		player: Memora Player Profile name
		event: Memora Live Challenge Event name

	Returns:
		dict with purchase_id, amount, currency, payment_url

	Raises:
		frappe.ValidationError on conflict
	"""
	# Validate event is paid and not ended
	event_doc = frappe.get_doc("Memora Live Challenge Event", event)
	if not event_doc.get("is_paid"):
		frappe.throw(
			f"Event {event} is not a paid event.",
			exc=frappe.ValidationError,
		)
	if event_doc.get("status") == "Ended":
		frappe.throw(
			f"Event {event} has already ended.",
			exc=frappe.ValidationError,
		)

	# Check for existing active access
	existing_access = frappe.db.exists(
		"Memora Live Event Access",
		{"player": player, "event": event, "status": "active"},
	)
	if existing_access:
		frappe.throw(
			f"Player {player} already has active access to event {event}.",
			exc=frappe.ValidationError,
		)

	# Check for existing pending purchase
	existing_pending = frappe.db.exists(
		"Memora Live Event Purchase",
		{"player": player, "event": event, "status": "pending"},
	)
	if existing_pending:
		frappe.throw(
			f"Player {player} already has a pending purchase for event {event}.",
			exc=frappe.ValidationError,
		)

	# Get pricing from event
	price = event_doc.get("price") or 0
	currency = event_doc.get("currency") or "JOD"
	item_code = event_doc.get("erpnext_item_code") or ""

	# Get player's current season and plan
	player_doc = frappe.get_doc("Memora Player Profile", player)
	season = player_doc.get("current_season")
	if not season:
		frappe.throw("Player has no active season.", exc=frappe.ValidationError)

	plan_snapshot = player_doc.get("current_plan") or ""

	# Create purchase record
	purchase = frappe.get_doc({
		"doctype": "Memora Live Event Purchase",
		"player": player,
		"event": event,
		"plan_snapshot": plan_snapshot,
		"season": season,
		"status": "pending",
		"amount": price,
		"currency": currency,
		"erpnext_item_code": item_code,
		"expires_at": frappe.utils.now_datetime() + timedelta(minutes=30),
	})
	purchase.insert(ignore_permissions=True)
	frappe.db.commit()

	return {
		"purchase_id": purchase.name,
		"amount": float(price),
		"currency": currency,
		"payment_url": "",  # Gateway-agnostic — caller fills in
	}


def confirm_event_purchase(
	purchase_id: str,
	transaction_id: str,
	payment_gateway: str = "",
) -> dict:
	"""Confirm a pending event purchase: mark paid, create access, create invoice.

	Called by the payment webhook handler. All operations within a single
	Frappe transaction for atomicity.

	Args:
		purchase_id: Memora Live Event Purchase name
		transaction_id: Payment gateway transaction ID
		payment_gateway: Gateway identifier

	Returns:
		dict with access_id, invoice_id
	"""
	purchase = frappe.get_doc("Memora Live Event Purchase", purchase_id)

	if purchase.status != "pending":
		frappe.throw(
			f"Purchase {purchase_id} is not in pending state (current: {purchase.status}).",
			exc=frappe.ValidationError,
		)

	# Mark purchase as paid
	purchase.status = "paid"
	purchase.paid_at = frappe.utils.now_datetime()
	purchase.payment_reference = transaction_id
	purchase.payment_gateway = payment_gateway

	# Create Live Event Access entitlement
	access_doc = frappe.get_doc({
		"doctype": "Memora Live Event Access",
		"player": purchase.player,
		"event": purchase.event,
		"status": "active",
		"access_type": "purchase",
		"purchase_ref": purchase.name,
	})
	access_doc.insert(ignore_permissions=True)

	# Back-reference
	purchase.event_access_ref = access_doc.name

	# Create Sales Invoice (R-008: Frappe ORM only, no direct SQL)
	invoice_name = _create_purchase_invoice(purchase)
	purchase.erpnext_invoice = invoice_name

	purchase.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"access_id": access_doc.name,
		"invoice_id": invoice_name,
		"player_id": purchase.player,
		"event_id": purchase.event,
	}


def _create_purchase_invoice(purchase_doc) -> str:
	"""Create a Sales Invoice for an event ticket purchase via Frappe ORM (R-008)."""
	customer = _get_player_customer(purchase_doc.player)
	if not customer:
		return ""

	try:
		invoice = frappe.get_doc({
			"doctype": "Sales Invoice",
			"customer": customer,
			"items": [{
				"item_code": purchase_doc.erpnext_item_code,
				"qty": 1,
				"rate": purchase_doc.amount,
			}],
			"currency": purchase_doc.currency,
		})
		invoice.insert(ignore_permissions=True)
		invoice.submit()
		return invoice.name
	except Exception:
		frappe.log_error(
			f"Invoice creation failed for event purchase {purchase_doc.name}. "
			"Manual invoice creation required.",
			"Event Purchase Invoice Failed",
		)
		return ""


def _get_player_customer(player: str) -> str | None:
	"""Get the ERPNext Customer linked to a player profile."""
	return frappe.db.get_value("Memora Player Profile", player, "customer")
