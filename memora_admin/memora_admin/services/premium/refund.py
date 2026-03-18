# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Atomic refund processing service (FR-012).

Refunds atomically mark the purchase as refunded AND revoke the linked
entitlement within a single Frappe transaction, then invalidate the Redis cache.
"""

import frappe


def refund_plan_premium_purchase(purchase_id: str) -> dict:
	"""Atomically refund a plan premium purchase (FR-012).

	Single transaction:
	1. Mark purchase status → refunded, set refunded_at
	2. Mark linked premium status → revoked, set revoked_at
	3. Invalidate Redis cache

	Args:
		purchase_id: Memora Plan Premium Purchase name

	Returns:
		dict with purchase_id, premium_id, status

	Raises:
		frappe.ValidationError if purchase not in refundable state
	"""
	purchase = frappe.get_doc("Memora Plan Premium Purchase", purchase_id)

	if purchase.status != "paid":
		frappe.throw(
			f"Purchase {purchase_id} is not in refundable state (current: {purchase.status}). "
			"Only 'paid' purchases can be refunded.",
			exc=frappe.ValidationError,
		)

	# 1. Mark purchase as refunded
	purchase.status = "refunded"
	purchase.refunded_at = frappe.utils.now_datetime()
	purchase.save(ignore_permissions=True)

	# 2. Revoke linked premium (if exists)
	premium_id = purchase.premium_ref
	if not premium_id:
		frappe.log_error(
			f"Purchase {purchase_id} has no premium_ref. Premium may remain active. "
			"Manual review required.",
			"Refund Missing Premium Reference",
		)
	elif frappe.db.exists("Memora Plan Premium", premium_id):
		premium = frappe.get_doc("Memora Plan Premium", premium_id)
		if premium.status == "active":
			premium.status = "revoked"
			premium.revoked_at = frappe.utils.now_datetime()
			premium.revoked_by = frappe.session.user
			premium.save(ignore_permissions=True)

	# Commit atomically
	frappe.db.commit()

	# 3. Invalidate Redis cache (after commit)
	_invalidate_premium_cache(purchase.player, purchase.plan)

	return {
		"purchase_id": purchase.name,
		"premium_id": premium_id or "",
		"status": "refunded",
	}


def refund_event_purchase(purchase_id: str) -> dict:
	"""Atomically refund a live event purchase (FR-011, FR-012).

	Single transaction:
	1. Mark purchase status → refunded, set refunded_at
	2. Mark linked event access status → refunded, set revoked_at
	3. Create Credit Note if purchase has an erpnext_invoice
	4. Invalidate Redis cache (after commit)

	If Credit Note creation fails, the exception propagates and Frappe
	rolls back steps 1-3 atomically.

	Args:
		purchase_id: Memora Live Event Purchase name

	Returns:
		dict with purchase_id, access_id, credit_note_id, status

	Raises:
		frappe.ValidationError if purchase not in refundable state
	"""
	purchase = frappe.get_doc("Memora Live Event Purchase", purchase_id)

	if purchase.status != "paid":
		frappe.throw(
			f"Purchase {purchase_id} is not in refundable state (current: {purchase.status}). "
			"Only 'paid' purchases can be refunded.",
			exc=frappe.ValidationError,
		)

	# 1. Mark purchase as refunded
	purchase.status = "refunded"
	purchase.refunded_at = frappe.utils.now_datetime()
	purchase.save(ignore_permissions=True)

	# 2. Revoke linked event access (if exists)
	access_id = purchase.event_access_ref
	if not access_id:
		frappe.log_error(
			f"Purchase {purchase_id} has no event_access_ref. Access may remain active. "
			"Manual review required.",
			"Refund Missing Access Reference",
		)
	elif frappe.db.exists("Memora Live Event Access", access_id):
		access = frappe.get_doc("Memora Live Event Access", access_id)
		if access.status == "active":
			access.status = "refunded"
			access.revoked_at = frappe.utils.now_datetime()
			access.revoked_by = frappe.session.user
			access.save(ignore_permissions=True)

	# 3. Create Credit Note (conditional — FR-011)
	credit_note_id = None
	if purchase.erpnext_invoice:
		credit_note_id = _create_event_credit_note(purchase)

	# Commit atomically
	frappe.db.commit()

	# 4. Invalidate Redis cache (after commit)
	_invalidate_event_access_cache(purchase.player, purchase.event)

	return {
		"purchase_id": purchase.name,
		"access_id": access_id or "",
		"credit_note_id": credit_note_id,
		"status": "refunded",
	}


def _create_event_credit_note(purchase) -> str | None:
	"""Create a Credit Note (return Sales Invoice) for a refunded event purchase.

	Returns the Credit Note name, or None if no customer mapping exists.
	Raises on insert/submit failure — caller's transaction will roll back.
	"""
	customer = frappe.db.get_value("Memora Player Profile", purchase.player, "customer")
	if not customer:
		frappe.log_error(
			f"No customer mapping for player {purchase.player}. "
			f"Skipping Credit Note for purchase {purchase.name}.",
			"Refund Credit Note Skipped",
		)
		return None

	cn = frappe.new_doc("Sales Invoice")
	cn.customer = customer
	cn.is_return = 1
	cn.return_against = purchase.erpnext_invoice
	cn.currency = purchase.currency
	cn.append("items", {
		"item_code": purchase.erpnext_item_code,
		"qty": -1,
		"rate": float(purchase.amount),
	})
	cn.insert(ignore_permissions=True)
	cn.submit()
	return cn.name


def _invalidate_premium_cache(player: str, plan: str):
	"""Invalidate premium Redis cache."""
	try:
		from memora_admin.memora_admin.events.premium_sync import invalidate_premium_cache
		invalidate_premium_cache(player, plan)
	except Exception:
		frappe.log_error("Failed to invalidate premium cache during refund")


def _invalidate_event_access_cache(player: str, event: str):
	"""Invalidate event access Redis cache."""
	try:
		from memora_admin.memora_admin.events.event_access_sync import invalidate_event_access_cache
		invalidate_event_access_cache(player, event)
	except Exception:
		frappe.log_error("Failed to invalidate event access cache during refund")
