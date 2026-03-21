"""Frappe API for building paid event catalog payload."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_paid_events_for_plan(plan_id: str) -> list[dict]:
	"""Paid, upcoming events eligible for the given plan.

	Returns events where:
	- is_paid = 1
	- scheduled_start > NOW() (upcoming only)
	- plan is in the event's eligible plans child table

	Args:
		plan_id: Memora Academic Plan document name

	Returns:
		List of event dicts with name, event_name, description,
		scheduled_start, price, currency
	"""
	events = frappe.db.sql(
		"""
		SELECT DISTINCT
			e.name,
			e.event_name,
			e.description,
			e.scheduled_start,
			e.price,
			e.currency
		FROM `tabMemora Live Challenge Event` e
		INNER JOIN `tabMemora Live Challenge Eligible Plan` ep
			ON ep.parent = e.name AND ep.parenttype = 'Memora Live Challenge Event'
		WHERE
			e.is_paid = 1
			AND e.scheduled_start > UTC_TIMESTAMP()
			AND ep.plan = %(plan_id)s
		ORDER BY e.scheduled_start ASC
		""",
		{"plan_id": plan_id},
		as_dict=True,
	)

	result = []
	for ev in events:
		result.append({
			"name": ev.name,
			"event_name": ev.event_name,
			"description": ev.description or None,
			"scheduled_start": str(ev.scheduled_start) if ev.scheduled_start else None,
			"price": float(ev.price) if ev.price else 0.0,
			"currency": ev.currency or "",
		})

	return result
