"""Frappe API for building product catalog payload."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_plan_catalog(plan_id: str) -> list[dict]:
	"""
	Build catalog payload for a plan. Called by FastAPI CatalogService on cache miss.

	Queries Product Grants (published) for the plan, enriches each with:
	- Item name (bundle_name) from ERPNext Item
	- Price from Item Price (Standard Selling price list)
	- Subject metadata from Grant Components + Plan Subject overrides

	Args:
		plan_id: Memora Plan document name (e.g., 'PLAN-00052')

	Returns:
		List of product dicts ready for CatalogProduct model validation
	"""
	grants = frappe.get_all(
		"Memora Product Grant",
		filters={"plan": plan_id, "is_published": 1},
		fields=["name", "item_code"],
	)

	products = []
	for grant in grants:
		# Get item display name (bundle name)
		item_name = frappe.get_value("Item", grant.item_code, "item_name")
		if not item_name:
			frappe.logger().warning(f"Item not found for item_code={grant.item_code}, skipping grant {grant.name}")
			continue

		# Get price from Standard Selling price list
		price = frappe.get_value(
			"Item Price",
			{"item_code": grant.item_code, "price_list": "Standard Selling"},
			"price_list_rate",
		)

		# Get grant components (subjects/tracks linked to this grant)
		components = frappe.get_all(
			"Memora Grant Component",
			filters={"parent": grant.name},
			fields=["target_doctype", "target_name"],
		)

		# Enrich subjects with plan-level metadata (alias_title, notes)
		subjects = []
		for comp in components:
			if comp.target_doctype == "Memora Subject":
				# Try plan-specific metadata first
				ps = frappe.get_value(
					"Memora Plan Subject",
					{"parent": plan_id, "subject": comp.target_name},
					["alias_title", "notes"],
					as_dict=True,
				)
				if ps:
					alias_title = ps.alias_title
					notes = ps.notes
				else:
					# Fall back to subject's own title
					alias_title = frappe.get_value("Memora Subject", comp.target_name, "title")
					notes = None

				subjects.append({
					"subject_id": comp.target_name,
					"alias_title": alias_title,
					"notes": notes,
				})

		products.append({
			"product_grant_id": grant.name,
			"bundle_name": item_name,
			"price": float(price) if price else 0.0,
			"subjects": subjects,
		})

	return products
