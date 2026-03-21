"""Plan premium catalog API — returns premium pricing and voucher availability."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_premium_info_for_plan(plan_id: str) -> dict | None:
	"""Return premium pricing info for a plan, or None if not configured.

	Args:
		plan_id: Memora Academic Plan name

	Returns:
		Dict with plan_id, plan_name, price, currency or None
	"""
	plan = frappe.db.get_value(
		"Memora Academic Plan",
		plan_id,
		["name", "plan_name", "premium_price", "premium_currency", "premium_item_code"],
		as_dict=True,
	)

	if not plan:
		return None

	price = plan.get("premium_price") or 0
	if not price or float(price) <= 0:
		return None  # Premium not configured for this plan

	return {
		"plan_id": plan.name,
		"plan_name": plan.plan_name or plan.name,
		"price": float(price),
		"currency": plan.get("premium_currency") or "JOD",
	}


@frappe.whitelist(allow_guest=False)
def get_premium_voucher_for_plan(plan_id: str) -> dict | None:
	"""Check if there are active plan_premium voucher batches with allocated
	cards for a plan.

	Player-level premium checks are left to the frontend (it already knows).
	This is a plan-level query only, so it can be aggressively cached.

	Args:
		plan_id: Memora Academic Plan name

	Returns:
		Dict with plan_id, plan_name, face_value if available, else None
	"""
	batches = frappe.db.sql(
		"""
		SELECT vb.face_value
		FROM `tabMemora Voucher Batch` vb
		INNER JOIN `tabMemora Voucher Batch Eligible Plan` ep
			ON ep.parent = vb.name AND ep.plan = %(plan_id)s
		WHERE vb.grant_type = 'plan_premium'
		  AND vb.status = 'Active'
		  AND EXISTS (
			SELECT 1 FROM `tabMemora Voucher Card` vc
			WHERE vc.batch = vb.name AND vc.status = 'Allocated'
		  )
		LIMIT 1
		""",
		{"plan_id": plan_id},
		as_dict=True,
	)

	if not batches:
		return None

	plan_name = frappe.db.get_value("Memora Academic Plan", plan_id, "plan_name") or plan_id

	return {
		"plan_id": plan_id,
		"plan_name": plan_name,
		"face_value": str(batches[0].face_value or "0"),
	}
