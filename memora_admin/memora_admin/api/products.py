"""Frappe API for product-related operations."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_grant_keys(product_grant_id: str) -> list[str]:
    """
    Get access keys from Memora Product Grant.

    Args:
        product_grant_id: Name of Memora Product Grant document

    Returns:
        List of access keys (e.g., ["SUB-MATH", "TRK-MATH-01"])

    Raises:
        frappe.DoesNotExistError: If product_grant_id not found
    """
    doc = frappe.get_doc("Memora Product Grant", product_grant_id)

    grant_keys = []
    for component in doc.grant_components:
        if component.target_doctype == "Memora Subject":
            grant_keys.append(f"SUB-{component.target_name}")
        elif component.target_doctype == "Memora Track":
            grant_keys.append(f"TRK-{component.target_name}")
        else:
            # Log warning for unknown doctype but continue
            frappe.logger().warning(
                f"Unknown grant component doctype: {component.target_doctype}"
            )

    frappe.logger().info(f"Product Grant {product_grant_id}: {len(grant_keys)} keys")
    return grant_keys
