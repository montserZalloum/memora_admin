# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Admin API for Plan Premium and Live Event Access operations.

Whitelisted methods:
- grant_plan_premium: admin grants plan premium to a player (US4)
- revoke_plan_premium: admin revokes a plan premium (US4)
- grant_event_access: admin grants live event access to a player (US4)
- revoke_event_access: admin revokes live event access (US4)
- refund_plan_premium_purchase: atomic refund for plan premium purchase (US5)
- refund_event_purchase: atomic refund for live event purchase (US5)
"""

import frappe

from memora_admin.memora_admin.services.premium.access_check import is_plan_premium_usable


@frappe.whitelist(methods=["POST"])
def grant_plan_premium(player: str, plan: str) -> dict:
	"""Admin grants Plan Premium to a player.

	Creates a Plan Premium with source_type=admin and granted_by set
	to the current admin user. Rejects if player already has a usable
	premium for the target plan.

	Args:
		player: Memora Player Profile name
		plan: Memora Academic Plan name

	Returns:
		dict with premium_id, player, plan, source_type
	"""
	frappe.only_for("System Manager")

	# Validate player exists
	if not frappe.db.exists("Memora Player Profile", player):
		frappe.throw(f"Player {player} not found.", exc=frappe.DoesNotExistError)

	# Validate plan exists
	if not frappe.db.exists("Memora Academic Plan", plan):
		frappe.throw(f"Plan {plan} not found.", exc=frappe.DoesNotExistError)

	# Reject if player already has usable premium for this plan
	check = is_plan_premium_usable(player, plan)
	if check.usable:
		frappe.throw(
			f"Player {player} already has a usable premium for plan {plan}.",
			exc=frappe.DuplicateEntryError,
		)

	# Get current season from player profile
	season = frappe.db.get_value("Memora Player Profile", player, "current_season")
	if not season:
		frappe.throw("Player has no active season.", exc=frappe.ValidationError)

	# Create Plan Premium entitlement
	premium = frappe.get_doc({
		"doctype": "Memora Plan Premium",
		"player": player,
		"plan": plan,
		"season": season,
		"status": "active",
		"source_type": "admin",
		"granted_by": frappe.session.user,
	})
	premium.insert(ignore_permissions=True)
	frappe.db.commit()

	return {
		"premium_id": premium.name,
		"player": player,
		"plan": plan,
		"source_type": "admin",
	}


@frappe.whitelist(methods=["POST"])
def revoke_plan_premium(premium_id: str) -> dict:
	"""Admin revokes a Plan Premium.

	Sets status to revoked, records revoked_at and revoked_by.
	Invalidates Redis cache. Does NOT affect linked purchase status.

	Args:
		premium_id: Memora Plan Premium name

	Returns:
		dict with premium_id and status
	"""
	frappe.only_for("System Manager")

	if not frappe.db.exists("Memora Plan Premium", premium_id):
		frappe.throw(f"Premium {premium_id} not found.", exc=frappe.DoesNotExistError)

	premium = frappe.get_doc("Memora Plan Premium", premium_id)

	if premium.status == "revoked":
		frappe.throw("Premium is already revoked.", exc=frappe.ValidationError)

	premium.status = "revoked"
	premium.revoked_at = frappe.utils.now_datetime()
	premium.revoked_by = frappe.session.user
	premium.save(ignore_permissions=True)
	frappe.db.commit()

	# Invalidate Redis cache (event handler also fires, but explicit invalidation
	# ensures cache is cleared even if event processing is delayed)
	_invalidate_premium_cache(premium.player, premium.plan)

	return {
		"premium_id": premium.name,
		"status": "revoked",
	}


@frappe.whitelist(methods=["POST"])
def grant_event_access(player: str, event: str) -> dict:
	"""Admin grants Live Event Access to a player.

	Creates a Live Event Access with access_type=admin.
	Rejects if player already has active access for this event.

	Args:
		player: Memora Player Profile name
		event: Memora Live Challenge Event name

	Returns:
		dict with access_id, player, event, access_type
	"""
	frappe.only_for("System Manager")

	# Validate player exists
	if not frappe.db.exists("Memora Player Profile", player):
		frappe.throw(f"Player {player} not found.", exc=frappe.DoesNotExistError)

	# Validate event exists
	if not frappe.db.exists("Memora Live Challenge Event", event):
		frappe.throw(f"Event {event} not found.", exc=frappe.DoesNotExistError)

	# Reject if player already has active access for this event
	existing_access = frappe.db.exists(
		"Memora Live Event Access",
		{"player": player, "event": event, "status": "active"},
	)
	if existing_access:
		frappe.throw(
			f"Player {player} already has active access for event {event}.",
			exc=frappe.DuplicateEntryError,
		)

	# Create Live Event Access entitlement
	access = frappe.get_doc({
		"doctype": "Memora Live Event Access",
		"player": player,
		"event": event,
		"status": "active",
		"access_type": "admin",
		"granted_by": frappe.session.user,
	})
	access.insert(ignore_permissions=True)
	frappe.db.commit()

	return {
		"access_id": access.name,
		"player": player,
		"event": event,
		"access_type": "admin",
	}


@frappe.whitelist(methods=["POST"])
def revoke_event_access(access_id: str) -> dict:
	"""Admin revokes Live Event Access.

	Sets status to revoked, records revoked_at and revoked_by.
	Invalidates Redis cache.

	Args:
		access_id: Memora Live Event Access name

	Returns:
		dict with access_id and status
	"""
	frappe.only_for("System Manager")

	if not frappe.db.exists("Memora Live Event Access", access_id):
		frappe.throw(f"Access {access_id} not found.", exc=frappe.DoesNotExistError)

	access = frappe.get_doc("Memora Live Event Access", access_id)

	if access.status in ("revoked", "refunded"):
		frappe.throw(
			f"Access is already {access.status}.",
			exc=frappe.ValidationError,
		)

	access.status = "revoked"
	access.revoked_at = frappe.utils.now_datetime()
	access.revoked_by = frappe.session.user
	access.save(ignore_permissions=True)
	frappe.db.commit()

	# Invalidate Redis cache
	_invalidate_event_access_cache(access.player, access.event)

	return {
		"access_id": access.name,
		"status": "revoked",
	}


@frappe.whitelist(methods=["POST"])
def refund_plan_premium_purchase(purchase_id: str) -> dict:
	"""Process refund for a plan premium purchase (FR-012 atomicity).

	Atomically within a single transaction:
	1. Marks purchase status → refunded, sets refunded_at
	2. Marks linked premium status → revoked, sets revoked_at
	3. Invalidates Redis cache

	Args:
		purchase_id: Memora Plan Premium Purchase name

	Returns:
		dict with purchase_id, premium_id, status
	"""
	frappe.only_for("System Manager")

	from memora_admin.memora_admin.services.premium.refund import (
		refund_plan_premium_purchase as _refund,
	)

	return _refund(purchase_id)


@frappe.whitelist(methods=["POST"])
def refund_event_purchase(purchase_id: str) -> dict:
	"""Process refund for a live event purchase (FR-012 atomicity).

	Atomically marks purchase as refunded and linked access as refunded.

	Args:
		purchase_id: Memora Live Event Purchase name

	Returns:
		dict with purchase_id, access_id, status
	"""
	frappe.only_for("System Manager")

	from memora_admin.memora_admin.services.premium.refund import (
		refund_event_purchase as _refund,
	)

	return _refund(purchase_id)


def _invalidate_premium_cache(player: str, plan: str):
	"""Invalidate premium Redis cache via event module."""
	try:
		from memora_admin.memora_admin.events.premium_sync import invalidate_premium_cache
		invalidate_premium_cache(player, plan)
	except Exception:
		frappe.log_error("Failed to invalidate premium cache from admin API")


def _invalidate_event_access_cache(player: str, event: str):
	"""Invalidate event access Redis cache via event module."""
	try:
		from memora_admin.memora_admin.events.event_access_sync import invalidate_event_access_cache
		invalidate_event_access_cache(player, event)
	except Exception:
		frappe.log_error("Failed to invalidate event access cache from admin API")
