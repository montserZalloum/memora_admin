# Copyright (c) 2026, Conan Academy and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from memora_admin.memora_admin.services.voucher.commission import (
	calculate_commission,
	resolve_commission,
)


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	report_summary = get_report_summary(data)
	return columns, data, None, None, report_summary


def get_columns():
	return [
		{
			"fieldname": "library",
			"label": _("Library"),
			"fieldtype": "Link",
			"options": "Customer",
			"width": 200,
		},
		{
			"fieldname": "allocated_count",
			"label": _("Allocated Cards"),
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"fieldname": "redeemed_count",
			"label": _("Redeemed Cards"),
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"fieldname": "uninvoiced_count",
			"label": _("Uninvoiced"),
			"fieldtype": "Int",
			"width": 120,
		},
		{
			"fieldname": "face_value",
			"label": _("Face Value"),
			"fieldtype": "Currency",
			"width": 100,
		},
		{
			"fieldname": "total_redeemed_value",
			"label": _("Total Redeemed Value"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"fieldname": "commission_per_card",
			"label": _("Commission/Card"),
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"fieldname": "amount_due",
			"label": _("Amount Due"),
			"fieldtype": "Currency",
			"width": 130,
		},
	]


def get_data(filters):
	conditions = "WHERE c.sale_model = 'Consignment'"
	values = []

	if filters and filters.get("from_date"):
		conditions += " AND c.modified >= %s"
		values.append(filters.get("from_date"))

	if filters and filters.get("to_date"):
		conditions += " AND c.modified <= %s"
		values.append(str(filters.get("to_date")) + " 23:59:59")

	if filters and filters.get("library"):
		conditions += " AND c.library = %s"
		values.append(filters.get("library"))

	rows = frappe.db.sql(
		f"""
		SELECT
			c.library,
			c.batch,
			COUNT(*) as allocated_count,
			SUM(CASE WHEN c.status = 'Redeemed' THEN 1 ELSE 0 END) as redeemed_count,
			SUM(CASE WHEN c.status = 'Redeemed'
				AND (c.sales_invoice IS NULL OR c.sales_invoice = '')
				THEN 1 ELSE 0 END) as uninvoiced_count,
			b.face_value
		FROM `tabMemora Voucher Card` c
		JOIN `tabMemora Voucher Batch` b ON c.batch = b.name
		{conditions}
		GROUP BY c.library, c.batch, b.face_value
		ORDER BY c.library
		""",
		values,
		as_dict=True,
	)

	# Post-process: calculate commission using existing service
	for row in rows:
		if row.redeemed_count > 0:
			ct, cv = resolve_commission(row.batch, row.library)
			result = calculate_commission(
				face_value=str(row.face_value),
				quantity=row.redeemed_count,
				commission_type=ct,
				commission_value=cv,
			)
			row["total_redeemed_value"] = float(row.face_value) * row.redeemed_count
			row["commission_per_card"] = float(result["per_card_commission"])
			row["amount_due"] = float(result["net_total"])
		else:
			row["total_redeemed_value"] = 0
			row["commission_per_card"] = 0
			row["amount_due"] = 0

	return rows


def get_report_summary(data):
	total_allocated = sum(d.get("allocated_count", 0) for d in data)
	total_redeemed = sum(d.get("redeemed_count", 0) for d in data)
	total_uninvoiced = sum(d.get("uninvoiced_count", 0) for d in data)
	total_amount_due = sum(d.get("amount_due", 0) for d in data)

	return [
		{
			"value": total_allocated,
			"indicator": "Grey",
			"label": _("Total Allocated"),
			"datatype": "Data",
		},
		{
			"value": total_redeemed,
			"indicator": "Green",
			"label": _("Total Redeemed"),
			"datatype": "Int",
		},
		{
			"value": total_uninvoiced,
			"indicator": "Orange",
			"label": _("Total Uninvoiced"),
			"datatype": "Int",
		},
		{
			"value": total_amount_due,
			"indicator": "Blue",
			"label": _("Total Amount Due"),
			"datatype": "Currency",
			"currency": "JOD",
		},
	]
