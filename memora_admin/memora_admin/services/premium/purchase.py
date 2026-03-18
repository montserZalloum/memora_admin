# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Plan Premium purchase creation service.

Handles the Frappe-side logic for creating a pending purchase record
and generating a Sales Invoice on payment confirmation (R-008).
"""

import frappe

from memora_admin.memora_admin.services.premium.access_check import is_plan_premium_usable


def create_plan_premium_purchase(player: str, plan: str) -> dict:
	"""Create a pending Plan Premium Purchase for a player.

	Validates:
	- Player doesn't already have a usable premium for this plan
	- Player doesn't already have a pending purchase for this plan

	Args:
		player: Memora Player Profile name
		plan: Memora Academic Plan name

	Returns:
		dict with purchase_id, amount, currency, payment_url

	Raises:
		frappe.ValidationError on duplicate/conflict
	"""
	# Check for existing usable premium
	check = is_plan_premium_usable(player, plan)
	if check.usable:
		frappe.throw(
			f"Player {player} already has a usable premium for plan {plan}.",
			exc=frappe.ValidationError,
		)

	# Check for existing pending purchase
	existing_pending = frappe.db.exists(
		"Memora Plan Premium Purchase",
		{"player": player, "plan": plan, "status": "pending"},
	)
	if existing_pending:
		frappe.throw(
			f"Player {player} already has a pending purchase for plan {plan}.",
			exc=frappe.ValidationError,
		)

	# Get plan pricing info
	plan_doc = frappe.get_doc("Memora Academic Plan", plan)
	price = plan_doc.get("premium_price") or 0
	currency = plan_doc.get("premium_currency") or "JOD"
	item_code = plan_doc.get("premium_item_code") or ""

	# Get current season
	season = frappe.db.get_value("Memora Player Profile", player, "current_season")
	if not season:
		frappe.throw("Player has no active season.", exc=frappe.ValidationError)

	# Create purchase record
	purchase = frappe.get_doc({
		"doctype": "Memora Plan Premium Purchase",
		"player": player,
		"plan": plan,
		"season": season,
		"status": "pending",
		"amount": price,
		"currency": currency,
		"erpnext_item_code": item_code,
	})
	purchase.insert(ignore_permissions=True)
	frappe.db.commit()

	return {
		"purchase_id": purchase.name,
		"amount": float(price),
		"currency": currency,
		"payment_url": "",  # Gateway-agnostic — caller fills in
	}


def confirm_plan_premium_purchase(
	purchase_id: str,
	transaction_id: str,
	payment_gateway: str = "",
) -> dict:
	"""Confirm a pending purchase: mark paid, create entitlement, create invoice.

	Called by the payment webhook handler. All operations within a single
	Frappe transaction for atomicity.

	Args:
		purchase_id: Memora Plan Premium Purchase name
		transaction_id: Payment gateway transaction ID
		payment_gateway: Gateway identifier

	Returns:
		dict with premium_id, invoice_id
	"""
	purchase = frappe.get_doc("Memora Plan Premium Purchase", purchase_id)

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

	# Create Plan Premium entitlement
	premium = frappe.get_doc({
		"doctype": "Memora Plan Premium",
		"player": purchase.player,
		"plan": purchase.plan,
		"season": purchase.season,
		"status": "active",
		"source_type": "purchase",
		"purchase_ref": purchase.name,
	})
	premium.insert(ignore_permissions=True)

	# Back-reference
	purchase.premium_ref = premium.name

	# Create Sales Invoice (R-008: Frappe ORM only, no direct SQL)
	invoice_name = _create_purchase_invoice(purchase)
	purchase.erpnext_invoice = invoice_name

	purchase.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"premium_id": premium.name,
		"invoice_id": invoice_name,
		"player_id": purchase.player,
		"plan_id": purchase.plan,
	}


def _create_purchase_invoice(purchase_doc) -> str:
	"""Create a Sales Invoice for a premium purchase via Frappe ORM (R-008).

	Constitution Principle VI forbids direct SQL INSERT into accounting tables.
	"""
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
			f"Invoice creation failed for purchase {purchase_doc.name}. "
			"Manual invoice creation required.",
			"Premium Purchase Invoice Failed",
		)
		return ""


def _get_player_customer(player: str) -> str | None:
	"""Get the ERPNext Customer linked to a player profile."""
	return frappe.db.get_value("Memora Player Profile", player, "customer")
