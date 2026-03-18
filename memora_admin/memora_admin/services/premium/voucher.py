# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Access Voucher code generation, verification, and atomic redemption (R-009).

Security model (Constitution Principle V — NON-NEGOTIABLE):
- Code generation: secrets.choice() from 30-char unambiguous alphabet
- Code storage: HMAC-SHA256 hash only. Plaintext NEVER persisted.
- Code verification: hmac.compare_digest() (timing-safe)
- HMAC secret: voucher_hmac_secret from site_config.json
"""

import hashlib
import hmac as hmac_module
import secrets

import frappe
from frappe.utils import nowdate, now_datetime, getdate

from memora_admin.memora_admin.services.premium.access_check import is_plan_premium_usable

# Reuse the same 30-char unambiguous alphabet from the B2B voucher system
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 12


def generate_code(length: int = CODE_LENGTH) -> str:
	"""Generate a cryptographically secure random voucher code.

	Uses secrets.choice() per character for uniform CSPRNG distribution.
	"""
	return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def compute_code_hmac(code: str) -> str:
	"""Compute HMAC-SHA256 hex digest of a voucher code.

	Uses voucher_hmac_secret from site_config.json.
	"""
	secret = frappe.conf.get("voucher_hmac_secret")
	if not secret:
		frappe.throw("voucher_hmac_secret not configured in site_config.json.")
	return hmac_module.new(
		secret.encode("utf-8"),
		code.encode("utf-8"),
		hashlib.sha256,
	).hexdigest()


def verify_code(code: str, stored_hash: str) -> bool:
	"""Timing-safe verification of a voucher code against stored HMAC hash."""
	computed = compute_code_hmac(code)
	return hmac_module.compare_digest(computed, stored_hash)


def _find_voucher_by_code(code: str):
	"""Look up a voucher by computing the HMAC and querying code_hash.

	Returns the voucher doc dict or None.
	"""
	code_hash = compute_code_hmac(code)

	voucher_name = frappe.db.get_value(
		"Memora Access Voucher",
		{"code_hash": code_hash},
		"name",
	)
	if not voucher_name:
		return None

	voucher = frappe.get_doc("Memora Access Voucher", voucher_name)

	return voucher


def _validate_voucher(voucher, player: str) -> str | None:
	"""Validate a voucher is redeemable. Returns error string or None if valid."""
	if not voucher.is_active:
		return "VOUCHER_INACTIVE"

	if voucher.valid_until and getdate(voucher.valid_until) < getdate(nowdate()):
		return "VOUCHER_EXPIRED"

	if voucher.total_redemptions >= voucher.max_redemptions:
		return "VOUCHER_EXHAUSTED"

	# Check player hasn't already redeemed this voucher
	already_redeemed = frappe.db.exists(
		"Memora Access Voucher Redemption",
		{"voucher": voucher.name, "player": player, "status": "success"},
	)
	if already_redeemed:
		return "ALREADY_REDEEMED"

	return None


def _validate_player_ownership(player: str):
	"""Ensure the caller is the player owner or has System Manager role."""
	user = frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return
	player_user = frappe.db.get_value("Memora Player Profile", player, "user")
	if player_user != user:
		frappe.throw("Not authorized for this player.", exc=frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def redeem_plan_premium_voucher(player: str, code: str) -> dict:
	"""Redeem a voucher code to grant Plan Premium (FR-011).

	Atomic flow:
	1. Verify caller owns the player profile
	2. Verify code via HMAC
	3. Check voucher active + not expired + not exhausted
	4. Check player hasn't already redeemed
	5. Check no existing usable premium for target plan
	6. Create Redemption + Premium entitlement atomically
	7. Atomically increment total_redemptions

	Args:
		player: Memora Player Profile name
		code: Plaintext voucher code

	Returns:
		dict with premium_id, plan_id, season_end

	Raises:
		frappe.ValidationError on any validation failure
	"""
	_validate_player_ownership(player)

	voucher = _find_voucher_by_code(code)
	if not voucher:
		frappe.throw("Invalid voucher code.", exc=frappe.ValidationError)

	if voucher.voucher_type != "plan_premium":
		frappe.throw("This voucher is not for plan premium.", exc=frappe.ValidationError)

	# Validate voucher state
	error = _validate_voucher(voucher, player)
	if error:
		frappe.throw(error, exc=frappe.ValidationError)

	plan = voucher.target_plan

	# Check player doesn't already have usable premium for this plan
	check = is_plan_premium_usable(player, plan)
	if check.usable:
		frappe.throw("ALREADY_PREMIUM", exc=frappe.ValidationError)

	# Get current season
	season = frappe.db.get_value("Memora Player Profile", player, "current_season")
	if not season:
		frappe.throw("Player has no active season.", exc=frappe.ValidationError)

	# Get current plan for snapshot
	current_plan = frappe.db.get_value("Memora Player Profile", player, "current_plan")

	# Atomic: create entitlement + redemption + increment counter
	premium = frappe.get_doc({
		"doctype": "Memora Plan Premium",
		"player": player,
		"plan": plan,
		"season": season,
		"status": "active",
		"source_type": "voucher",
		"voucher_ref": voucher.name,
	})
	premium.insert(ignore_permissions=True)

	redemption = frappe.get_doc({
		"doctype": "Memora Access Voucher Redemption",
		"voucher": voucher.name,
		"player": player,
		"status": "success",
		"redeemed_at": now_datetime(),
		"redeemed_plan": current_plan,
		"premium_ref": premium.name,
	})
	redemption.insert(ignore_permissions=True)

	# Atomically increment redemption counter (SQL prevents read-modify-write race)
	frappe.db.sql(
		"UPDATE `tabMemora Access Voucher` SET total_redemptions = total_redemptions + 1 WHERE name = %s",
		(voucher.name,),
	)
	if not frappe.db._cursor.rowcount:
		frappe.throw(f"Voucher {voucher.name} was deleted during redemption.")

	frappe.db.commit()

	# Get season end for response
	season_end = frappe.db.get_value("Memora Season", season, "end_date")

	return {
		"premium_id": premium.name,
		"plan_id": plan,
		"season_end": str(season_end) if season_end else None,
	}


@frappe.whitelist(methods=["POST"])
def redeem_event_access_voucher(player: str, event: str, code: str) -> dict:
	"""Redeem a voucher code to grant Live Event Access (FR-011).

	Atomic flow mirrors plan premium redemption.

	Args:
		player: Memora Player Profile name
		event: Memora Live Challenge Event name
		code: Plaintext voucher code

	Returns:
		dict with access_id, event_id

	Raises:
		frappe.ValidationError on any validation failure
	"""
	_validate_player_ownership(player)

	voucher = _find_voucher_by_code(code)
	if not voucher:
		frappe.throw("Invalid voucher code.", exc=frappe.ValidationError)

	if voucher.voucher_type != "live_event_access":
		frappe.throw("This voucher is not for live event access.", exc=frappe.ValidationError)

	# Verify voucher targets this specific event
	if voucher.target_event != event:
		frappe.throw("This voucher is not valid for this event.", exc=frappe.ValidationError)

	# Validate voucher state
	error = _validate_voucher(voucher, player)
	if error:
		frappe.throw(error, exc=frappe.ValidationError)

	# Check player doesn't already have active access
	existing_access = frappe.db.exists(
		"Memora Live Event Access",
		{"player": player, "event": event, "status": "active"},
	)
	if existing_access:
		frappe.throw("ALREADY_HAS_ACCESS", exc=frappe.ValidationError)

	# Get current plan for snapshot
	current_plan = frappe.db.get_value("Memora Player Profile", player, "current_plan")

	# Atomic: create entitlement + redemption + increment counter
	access = frappe.get_doc({
		"doctype": "Memora Live Event Access",
		"player": player,
		"event": event,
		"status": "active",
		"access_type": "voucher",
		"voucher_ref": voucher.name,
	})
	access.insert(ignore_permissions=True)

	redemption = frappe.get_doc({
		"doctype": "Memora Access Voucher Redemption",
		"voucher": voucher.name,
		"player": player,
		"status": "success",
		"redeemed_at": now_datetime(),
		"redeemed_plan": current_plan,
		"event_access_ref": access.name,
	})
	redemption.insert(ignore_permissions=True)

	# Atomically increment redemption counter (SQL prevents read-modify-write race)
	frappe.db.sql(
		"UPDATE `tabMemora Access Voucher` SET total_redemptions = total_redemptions + 1 WHERE name = %s",
		(voucher.name,),
	)
	if not frappe.db._cursor.rowcount:
		frappe.throw(f"Voucher {voucher.name} was deleted during redemption.")

	frappe.db.commit()

	return {
		"access_id": access.name,
		"event_id": event,
	}
