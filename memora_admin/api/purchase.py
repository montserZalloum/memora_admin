"""Frappe API for purchase request operations.

Player identity is PLAYER-##### docname (not email). See Phase 32.
"""

import frappe


@frappe.whitelist(allow_guest=False)
def create_purchase_request(
	user_id: str,
	product_grant_id: str,
	payment_method: str,
	payment_proof_url: str | None = None,
	plan_id: str | None = None,
) -> dict:
	"""Create a Subscription Transaction with Pending Approval status.

	Args:
		user_id: Player docname (PLAYER-#####) from JWT sub claim
		product_grant_id: Memora Product Grant document name
		payment_method: Payment method (e.g. "Manual-Admin")
		payment_proof_url: Optional URL of uploaded payment proof image
		plan_id: Optional plan ID to validate product belongs to player's plan

	Returns:
		dict with transaction name and status

	Raises:
		frappe.DoesNotExistError: Product grant or player profile not found
		frappe.ValidationError: Product unpublished or wrong plan
		frappe.DuplicateEntryError: Pending transaction already exists
	"""
	# 1. Validate product grant exists and is published
	if not frappe.db.exists("Memora Product Grant", product_grant_id):
		frappe.throw("Product not found", frappe.DoesNotExistError)

	grant = frappe.get_doc("Memora Product Grant", product_grant_id)

	if not grant.is_published:
		frappe.throw("Product not found", frappe.DoesNotExistError)

	if plan_id and grant.plan != plan_id:
		frappe.throw("Product not available for your plan", frappe.ValidationError)

	# 2. Validate player profile exists (user_id IS the PLAYER-##### docname)
	if not frappe.db.exists("Memora Player Profile", user_id):
		frappe.throw("Player profile not found", frappe.DoesNotExistError)
	player_id = user_id

	# 3. Check for existing pending transaction (duplicate guard)
	existing = frappe.db.exists(
		"Memora Subscription Transaction",
		{"player": player_id, "related_grant": product_grant_id, "status": "Pending Approval"},
	)
	if existing:
		frappe.throw("Purchase request already pending for this product", frappe.DuplicateEntryError)

	# 4. Get price from Item Price list
	price = frappe.get_value(
		"Item Price",
		{"item_code": grant.item_code, "price_list": "Standard Selling"},
		"price_list_rate",
	)

	# 5. Create the transaction document
	trx = frappe.get_doc(
		{
			"doctype": "Memora Subscription Transaction",
			"player": player_id,
			"payment_method": payment_method,
			"status": "Pending Approval",
			"related_grant": product_grant_id,
			"amount_paid": float(price) if price else 0.0,
			"payment_proof": payment_proof_url,
		}
	)
	trx.insert(ignore_permissions=True)

	frappe.logger().info(f"Purchase request {trx.name} created for player {player_id}, grant {product_grant_id}")

	return {"name": trx.name, "status": "Pending Approval"}
