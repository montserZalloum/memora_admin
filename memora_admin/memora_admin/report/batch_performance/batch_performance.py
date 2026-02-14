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
			"fieldname": "batch",
			"label": _("Batch"),
			"fieldtype": "Link",
			"options": "Memora Voucher Batch",
			"width": 150,
		},
		{
			"fieldname": "batch_name",
			"label": _("Batch Name"),
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"fieldname": "face_value",
			"label": _("Face Value"),
			"fieldtype": "Currency",
			"width": 100,
		},
		{
			"fieldname": "total_cards",
			"label": _("Total Cards"),
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"fieldname": "available",
			"label": _("Available"),
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"fieldname": "allocated",
			"label": _("Allocated"),
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"fieldname": "redeemed",
			"label": _("Redeemed"),
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"fieldname": "voided",
			"label": _("Voided"),
			"fieldtype": "Int",
			"width": 80,
		},
		{
			"fieldname": "expired",
			"label": _("Expired"),
			"fieldtype": "Int",
			"width": 80,
		},
		{
			"fieldname": "redemption_rate",
			"label": _("Redemption Rate"),
			"fieldtype": "Percent",
			"width": 110,
		},
		{
			"fieldname": "season_end",
			"label": _("Season End"),
			"fieldtype": "Date",
			"width": 110,
		},
		{
			"fieldname": "days_until_end",
			"label": _("Days Until End"),
			"fieldtype": "Int",
			"width": 110,
		},
	]


def get_data(filters):
	conditions = "WHERE b.status != 'Draft'"
	values = []

	if filters and filters.get("batch"):
		conditions += " AND b.name = %s"
		values.append(filters.get("batch"))

	if filters and filters.get("status"):
		conditions += " AND b.status = %s"
		values.append(filters.get("status"))

	return frappe.db.sql(
		f"""
		SELECT
			b.name as batch,
			b.batch_name,
			b.face_value,
			b.quantity as total_cards,
			SUM(CASE WHEN c.status = 'Available' THEN 1 ELSE 0 END) as available,
			SUM(CASE WHEN c.status = 'Allocated' THEN 1 ELSE 0 END) as allocated,
			SUM(CASE WHEN c.status = 'Redeemed' THEN 1 ELSE 0 END) as redeemed,
			SUM(CASE WHEN c.status = 'Void' THEN 1 ELSE 0 END) as voided,
			SUM(CASE WHEN c.status = 'Expired' THEN 1 ELSE 0 END) as expired,
			ROUND(
				SUM(CASE WHEN c.status = 'Redeemed' THEN 1 ELSE 0 END) * 100.0
				/ NULLIF(b.quantity, 0), 1
			) as redemption_rate,
			season_info.end_date as season_end,
			DATEDIFF(season_info.end_date, CURDATE()) as days_until_end
		FROM `tabMemora Voucher Batch` b
		LEFT JOIN `tabMemora Voucher Card` c ON c.batch = b.name
		LEFT JOIN (
			SELECT
				bg.parent as batch_name,
				MIN(s.end_date) as end_date
			FROM `tabMemora Voucher Batch Grant` bg
			JOIN `tabMemora Product Grant` pg ON bg.product_grant = pg.name
			JOIN `tabMemora Academic Plan` ap ON pg.plan = ap.name
			JOIN `tabMemora Season` s ON ap.season = s.name
			GROUP BY bg.parent
		) season_info ON season_info.batch_name = b.name
		{conditions}
		GROUP BY b.name, b.batch_name, b.face_value, b.quantity, season_info.end_date
		ORDER BY b.creation DESC
		""",
		values,
		as_dict=True,
	)


def get_report_summary(data):
	total_cards = sum(d.get("total_cards", 0) or 0 for d in data)
	total_redeemed = sum(d.get("redeemed", 0) or 0 for d in data)

	# Average redemption rate across batches (exclude NULL rates)
	rates = [d.get("redemption_rate", 0) for d in data if d.get("redemption_rate") is not None]
	avg_rate = round(sum(rates) / len(rates), 1) if rates else 0

	return [
		{
			"value": total_cards,
			"indicator": "Grey",
			"label": _("Total Cards"),
			"datatype": "Int",
		},
		{
			"value": total_redeemed,
			"indicator": "Green" if total_redeemed > 0 else "Grey",
			"label": _("Total Redeemed"),
			"datatype": "Int",
		},
		{
			"value": avg_rate,
			"indicator": "Blue",
			"label": _("Avg Redemption Rate"),
			"datatype": "Percent",
		},
	]
