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
			"fieldname": "purpose",
			"label": _("Purpose"),
			"fieldtype": "Data",
			"width": 100,
		},
		{
			"fieldname": "product_grant",
			"label": _("Product Grant"),
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"fieldname": "total_cards",
			"label": _("Total Cards"),
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"fieldname": "activated",
			"label": _("Activated"),
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
			"fieldname": "remaining",
			"label": _("Remaining"),
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"fieldname": "created",
			"label": _("Created"),
			"fieldtype": "Date",
			"width": 110,
		},
	]


def get_data(filters):
	conditions = "WHERE b.batch_purpose != 'Sale'"
	values = []

	if filters and filters.get("batch_purpose"):
		conditions += " AND b.batch_purpose = %s"
		values.append(filters.get("batch_purpose"))

	if filters and filters.get("from_date"):
		conditions += " AND b.creation >= %s"
		values.append(filters.get("from_date"))

	if filters and filters.get("to_date"):
		conditions += " AND b.creation < %s + INTERVAL 1 DAY"
		values.append(filters.get("to_date"))

	if filters and filters.get("product_grant"):
		conditions += """ AND EXISTS (
			SELECT 1 FROM `tabMemora Voucher Batch Grant` bg
			WHERE bg.parent = b.name AND bg.product_grant = %s
		)"""
		values.append(filters.get("product_grant"))

	# Card status counts are computed in a subquery to avoid the cartesian
	# product that would result from joining both cards and grants on the
	# same batch.  Product grants are fetched via a scalar subquery.
	return frappe.db.sql(
		f"""
		SELECT
			b.name AS batch,
			b.batch_name,
			b.batch_purpose AS purpose,
			(
				SELECT GROUP_CONCAT(DISTINCT bg.product_grant SEPARATOR ', ')
				FROM `tabMemora Voucher Batch Grant` bg
				WHERE bg.parent = b.name
			) AS product_grant,
			COALESCE(cs.total_cards, 0) AS total_cards,
			COALESCE(cs.activated, 0) AS activated,
			COALESCE(cs.redeemed, 0) AS redeemed,
			COALESCE(cs.voided, 0) AS voided,
			COALESCE(cs.total_cards, 0)
				- COALESCE(cs.redeemed, 0)
				- COALESCE(cs.voided, 0) AS remaining,
			DATE(b.creation) AS created
		FROM `tabMemora Voucher Batch` b
		LEFT JOIN (
			SELECT
				c.batch,
				COUNT(*) AS total_cards,
				SUM(c.status = 'Allocated') AS activated,
				SUM(c.status = 'Redeemed') AS redeemed,
				SUM(c.status IN ('Void', 'Expired')) AS voided
			FROM `tabMemora Voucher Card` c
			GROUP BY c.batch
		) cs ON cs.batch = b.name
		{conditions}
		ORDER BY b.creation DESC
		""",
		values,
		as_dict=True,
	)


def get_report_summary(data):
	total_cards = sum(d.get("total_cards", 0) or 0 for d in data)
	total_redeemed = sum(d.get("redeemed", 0) or 0 for d in data)

	avg_rate = round(total_redeemed * 100.0 / total_cards, 1) if total_cards else 0

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
