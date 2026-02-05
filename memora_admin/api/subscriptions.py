"""Frappe API for subscription-related operations."""

import frappe
from frappe.utils import getdate


@frappe.whitelist(allow_guest=False)
def create_subscription(
    player_id: str,
    access_key: str,
    expires_at: str,
    transaction_id: str | None = None,
) -> dict:
    """
    Create Memora Player Subscription record.

    This triggers doc_events hook which syncs grant to Redis.

    Args:
        player_id: Memora Player Profile name
        access_key: Access key (e.g., "SUB-MATH")
        expires_at: Expiration date in ISO format
        transaction_id: Optional transaction reference

    Returns:
        dict with subscription name and created status
    """
    # Check if subscription already exists
    existing = frappe.db.exists(
        "Memora Player Subscription", {"player": player_id, "access_key": access_key}
    )

    if existing:
        frappe.logger().info(f"Subscription exists: {player_id} -> {access_key}")
        return {
            "name": existing,
            "created": False,
            "message": "Subscription already exists",
        }

    # Create new subscription
    doc = frappe.get_doc(
        {
            "doctype": "Memora Player Subscription",
            "player": player_id,
            "access_key": access_key,
            "expires_at": getdate(expires_at),
            "is_active": 1,
        }
    )
    doc.insert()

    frappe.logger().info(
        f"Subscription created: {doc.name} ({player_id} -> {access_key})"
    )

    return {
        "name": doc.name,
        "created": True,
        "message": "Subscription created successfully",
    }
