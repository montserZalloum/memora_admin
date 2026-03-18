# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Admin API for Access Voucher management.

Whitelisted methods:
- create_access_voucher: generate code + store HMAC hash + return plaintext once
- deactivate_access_voucher: set is_active=0
"""

import frappe

from memora_admin.memora_admin.services.premium.voucher import generate_code, compute_code_hmac


@frappe.whitelist(methods=["POST"])
def create_access_voucher(
	voucher_type: str,
	target_plan: str | None = None,
	target_event: str | None = None,
	max_redemptions: int = 1,
	valid_until: str | None = None,
	notes: str | None = None,
) -> dict:
	"""Create a new Access Voucher and return the plaintext code ONCE.

	The code is generated, HMAC-hashed, and the hash stored. The plaintext
	code is returned to the admin and CANNOT be retrieved later.

	Args:
		voucher_type: "plan_premium" or "live_event_access"
		target_plan: Required for plan_premium voucher type
		target_event: Required for live_event_access voucher type
		max_redemptions: Maximum number of redemptions allowed (default: 1)
		valid_until: Optional expiry date (YYYY-MM-DD)
		notes: Optional admin notes

	Returns:
		dict with voucher_id and plaintext code (shown ONCE)
	"""
	frappe.only_for("System Manager")

	# Generate cryptographically secure code
	plaintext_code = generate_code()

	# Compute HMAC-SHA256 hash — only the hash is stored
	code_hash = compute_code_hmac(plaintext_code)

	# Create voucher document
	voucher = frappe.get_doc({
		"doctype": "Memora Access Voucher",
		"code_hash": code_hash,
		"voucher_type": voucher_type,
		"target_plan": target_plan,
		"target_event": target_event,
		"max_redemptions": int(max_redemptions),
		"total_redemptions": 0,
		"valid_until": valid_until,
		"is_active": 1,
		"created_by_admin": frappe.session.user,
		"notes": notes,
	})

	# DocType validation handles voucher_type ↔ target field requirements
	voucher.insert(ignore_permissions=True)
	frappe.db.commit()

	return {
		"voucher_id": voucher.name,
		"code": plaintext_code,
	}


@frappe.whitelist(methods=["POST"])
def deactivate_access_voucher(voucher_id: str) -> dict:
	"""Deactivate an Access Voucher, preventing future redemptions.

	Args:
		voucher_id: Memora Access Voucher name

	Returns:
		dict with voucher_id and status
	"""
	frappe.only_for("System Manager")

	if not frappe.db.exists("Memora Access Voucher", voucher_id):
		frappe.throw(f"Voucher {voucher_id} not found.", exc=frappe.DoesNotExistError)

	voucher = frappe.get_doc("Memora Access Voucher", voucher_id)

	if not voucher.is_active:
		frappe.throw("Voucher is already inactive.", exc=frappe.ValidationError)

	voucher.is_active = 0
	voucher.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"voucher_id": voucher.name,
		"status": "deactivated",
	}
