# Copyright (c) 2026, Conan Academy and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	report_summary = get_report_summary(data)
	return columns, data, None, None, report_summary


def get_columns():
	return [
		{
			"fieldname": "player",
			"label": _("Player"),
			"fieldtype": "Link",
			"options": "Memora Player Profile",
			"width": 150,
		},
		{
			"fieldname": "ip_address",
			"label": _("IP Address"),
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"fieldname": "failure_type",
			"label": _("Failure Type"),
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"fieldname": "attempt_count",
			"label": _("Attempts"),
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"fieldname": "first_attempt",
			"label": _("First Attempt"),
			"fieldtype": "Datetime",
			"width": 160,
		},
		{
			"fieldname": "last_attempt",
			"label": _("Last Attempt"),
			"fieldtype": "Datetime",
			"width": 160,
		},
	]


def get_data(filters):
	conditions = "WHERE rl.status != 'Success'"
	values = []

	if filters and filters.get("from_date"):
		conditions += " AND rl.timestamp >= %s"
		values.append(filters.get("from_date"))

	if filters and filters.get("to_date"):
		conditions += " AND rl.timestamp <= %s"
		values.append(str(filters.get("to_date")) + " 23:59:59")

	if filters and filters.get("player"):
		conditions += " AND rl.player = %s"
		values.append(filters.get("player"))

	if filters and filters.get("failure_type"):
		conditions += " AND rl.status = %s"
		values.append(filters.get("failure_type"))

	return frappe.db.sql(
		f"""
		SELECT
			rl.player,
			rl.ip_address,
			rl.status as failure_type,
			COUNT(*) as attempt_count,
			MIN(rl.timestamp) as first_attempt,
			MAX(rl.timestamp) as last_attempt
		FROM `tabMemora Voucher Redemption Log` rl
		{conditions}
		GROUP BY rl.player, rl.ip_address, rl.status
		ORDER BY attempt_count DESC
		""",
		values,
		as_dict=True,
	)


def get_report_summary(data):
	total_attempts = sum(d.get("attempt_count", 0) for d in data)
	unique_players = len({d.get("player") for d in data if d.get("player")})
	unique_ips = len({d.get("ip_address") for d in data if d.get("ip_address")})

	return [
		{
			"value": total_attempts,
			"indicator": "Red",
			"label": _("Total Failed Attempts"),
			"datatype": "Int",
		},
		{
			"value": unique_players,
			"indicator": "Orange",
			"label": _("Unique Players"),
			"datatype": "Int",
		},
		{
			"value": unique_ips,
			"indicator": "Grey",
			"label": _("Unique IPs"),
			"datatype": "Int",
		},
	]
