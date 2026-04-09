"""Frappe API for building product catalog payload."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_plan_catalog(plan_id: str) -> list[dict]:
	"""
	Build catalog payload for a plan. Called by FastAPI CatalogService on cache miss.

	Queries Product Grants (published) for the plan, enriches each with:
	- Title (bundle_name) from the Product Grant itself
	- Subject metadata from Grant Components + Plan Subject overrides

	Args:
		plan_id: Memora Plan document name (e.g., 'PLAN-00052')

	Returns:
		List of product dicts ready for CatalogProduct model validation
	"""
	grants = frappe.get_all(
		"Memora Product Grant",
		filters={"plan": plan_id, "is_published": 1},
		fields=["name", "title", "item_code"],
	)
	if not grants:
		return []

	grant_names = [g.name for g in grants]
	item_codes = list({g.item_code for g in grants if g.item_code})

	# Batch: Item prices (Standard Selling)
	prices = frappe.get_all(
		"Item Price",
		filters={"item_code": ["in", item_codes], "price_list": "Standard Selling"},
		fields=["item_code", "price_list_rate"],
	)
	price_map = {p.item_code: p.price_list_rate for p in prices}

	# Batch: All grant components
	all_components = frappe.get_all(
		"Memora Grant Component",
		filters={"parent": ["in", grant_names]},
		fields=["parent", "target_doctype", "target_name", "key_type"],
	)
	# Group components by grant
	comp_by_grant = {}
	for comp in all_components:
		comp_by_grant.setdefault(comp.parent, []).append(comp)

	# Collect all subject and track IDs from components
	subject_ids = list({c.target_name for c in all_components if c.target_doctype == "Memora Subject"})
	track_ids = list({c.target_name for c in all_components if c.target_doctype == "Memora Track"})

	# Batch: Plan Subject overrides for this plan
	ps_map = {}
	if subject_ids:
		plan_subjects = frappe.get_all(
			"Memora Plan Subject",
			filters={"parent": plan_id, "subject": ["in", subject_ids]},
			fields=["subject", "alias_title", "notes"],
		)
		ps_map = {ps.subject: ps for ps in plan_subjects}

	# Batch: Fallback subject titles (for subjects without plan-level override)
	fallback_subject_ids = [sid for sid in subject_ids if sid not in ps_map]
	subject_title_map = {}
	if fallback_subject_ids:
		subj_rows = frappe.get_all(
			"Memora Subject",
			filters={"name": ["in", fallback_subject_ids]},
			fields=["name", "subject_title"],
		)
		subject_title_map = {s.name: s.subject_title for s in subj_rows}

	# Batch: Track metadata
	track_map = {}
	if track_ids:
		track_rows = frappe.get_all(
			"Memora Track",
			filters={"name": ["in", track_ids]},
			fields=["name", "track_title", "subject", "description", "image"],
		)
		track_map = {t.name: t for t in track_rows}

	# Assemble products
	products = []
	for grant in grants:
		components = comp_by_grant.get(grant.name, [])

		subjects = []
		tracks = []
		for comp in components:
			if comp.target_doctype == "Memora Subject":
				ps = ps_map.get(comp.target_name)
				if ps:
					alias_title = ps.alias_title
					notes = ps.notes
				else:
					alias_title = subject_title_map.get(comp.target_name)
					notes = None

				subjects.append(
					{
						"subject_id": comp.target_name,
						"alias_title": alias_title,
						"notes": notes,
						"key_type": comp.key_type,
					}
				)

			elif comp.target_doctype == "Memora Track":
				track = track_map.get(comp.target_name)
				if track:
					tracks.append(
						{
							"track_id": comp.target_name,
							"track_title": track.track_title,
							"subject_id": track.subject,
							"description": track.description or None,
							"image": track.image or None,
							"key_type": comp.key_type,
						}
					)
				else:
					frappe.logger().warning(
						f"Track not found: {comp.target_name}, skipping component in grant {grant.name}"
					)

		products.append(
			{
				"product_grant_id": grant.name,
				"bundle_name": grant.title,
				"price": float(price_map.get(grant.item_code, 0.0)),
				"subjects": subjects,
				"tracks": tracks,
			}
		)

	return products
