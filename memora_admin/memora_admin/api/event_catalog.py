"""Frappe API for building paid event catalog payload."""

from datetime import datetime
from zoneinfo import ZoneInfo

import frappe

# Frappe stores Datetime fields in system timezone (Asia/Amman).
# MariaDB server runs in UTC, so NOW()/UTC_TIMESTAMP() won't match.
# Compute current Amman time in Python — same approach as FastAPI's _now_naive().
_SYSTEM_TZ = ZoneInfo("Asia/Amman")


@frappe.whitelist(allow_guest=False)
def get_paid_events_for_plan(plan_id: str) -> list[dict]:
	"""Paid, upcoming events eligible for the given plan.

	Returns events where:
	- is_paid = 1
	- scheduled_start > current Amman time (upcoming only)
	- plan is in the event's eligible plans child table

	Args:
		plan_id: Memora Academic Plan document name

	Returns:
		List of event dicts with name, event_name, description,
		scheduled_start, price, currency
	"""
	now_local = datetime.now(_SYSTEM_TZ).replace(tzinfo=None)

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
			AND e.status = 'Draft'
			AND e.scheduled_start > %(now_local)s
			AND ep.plan = %(plan_id)s
		ORDER BY e.scheduled_start ASC
		""",
		{"plan_id": plan_id, "now_local": now_local},
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
