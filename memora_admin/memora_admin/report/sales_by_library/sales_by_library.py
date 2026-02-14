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
			"fieldname": "redeemed_count",
			"label": _("Redeemed Cards"),
			"fieldtype": "Int",
			"width": 120,
		},
		{
			"fieldname": "face_value",
			"label": _("Face Value"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "total_face_value",
			"label": _("Total Face Value"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"fieldname": "commission_per_card",
			"label": _("Commission/Card"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"fieldname": "total_commission",
			"label": _("Total Commission"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"fieldname": "net_revenue",
			"label": _("Net Revenue"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"fieldname": "sale_model",
			"label": _("Sale Model"),
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"fieldname": "invoice_status",
			"label": _("Invoice Status"),
			"fieldtype": "Data",
			"width": 120,
		},
	]


def get_data(filters):
	conditions = "WHERE c.status = 'Redeemed'"
	values = []

	if filters and filters.get("from_date"):
		conditions += " AND c.redeemed_at >= %s"
		values.append(filters.get("from_date"))

	if filters and filters.get("to_date"):
		conditions += " AND c.redeemed_at <= %s"
		values.append(str(filters.get("to_date")) + " 23:59:59")

	if filters and filters.get("library"):
		conditions += " AND c.library = %s"
		values.append(filters.get("library"))

	if filters and filters.get("sale_model"):
		conditions += " AND c.sale_model = %s"
		values.append(filters.get("sale_model"))

	rows = frappe.db.sql(
		f"""
		SELECT
			c.library,
			c.batch,
			b.batch_name,
			COUNT(*) as redeemed_count,
			b.face_value,
			c.sale_model,
			CASE
				WHEN c.sales_invoice IS NOT NULL AND c.sales_invoice != ''
				THEN 'Invoiced'
				ELSE 'Not Invoiced'
			END as invoice_status
		FROM `tabMemora Voucher Card` c
		JOIN `tabMemora Voucher Batch` b ON c.batch = b.name
		{conditions}
		GROUP BY c.library, c.batch, c.sale_model, invoice_status
		ORDER BY c.library, b.face_value
		""",
		values,
		as_dict=True,
	)

	# Post-process: calculate commission using existing service
	for row in rows:
		ct, cv = resolve_commission(row.batch, row.library)
		result = calculate_commission(
			face_value=str(row.face_value),
			quantity=row.redeemed_count,
			commission_type=ct,
			commission_value=cv,
		)

		row["total_face_value"] = float(row.face_value) * row.redeemed_count
		row["commission_per_card"] = float(result["per_card_commission"])
		row["total_commission"] = float(result["total_commission"])
		row["net_revenue"] = float(result["net_total"])

	return rows


def get_report_summary(data):
	total_redeemed = sum(d.get("redeemed_count", 0) for d in data)
	total_revenue = sum(d.get("net_revenue", 0) for d in data)
	total_commission = sum(d.get("total_commission", 0) for d in data)

	return [
		{
			"value": total_redeemed,
			"indicator": "Green" if total_redeemed > 0 else "Grey",
			"label": _("Total Redeemed"),
			"datatype": "Int",
		},
		{
			"value": total_revenue,
			"indicator": "Blue",
			"label": _("Total Net Revenue"),
			"datatype": "Currency",
			"currency": "JOD",
		},
		{
			"value": total_commission,
			"indicator": "Grey",
			"label": _("Total Commission"),
			"datatype": "Currency",
			"currency": "JOD",
		},
	]
