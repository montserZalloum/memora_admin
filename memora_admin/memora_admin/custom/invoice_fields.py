# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Custom fields for voucher invoice tracking on Voucher Card and Allocation."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def add_voucher_invoice_fields():
	"""Add sales_invoice Link field to Voucher Card and Allocation. Idempotent."""
	custom_fields = {
		"Memora Voucher Card": [
			{
				"fieldname": "sales_invoice",
				"fieldtype": "Link",
				"label": "Sales Invoice",
				"options": "Sales Invoice",
				"insert_after": "void_reason",
				"read_only": 1,
			},
		],
		"Memora Voucher Allocation": [
			{
				"fieldname": "sales_invoice",
				"fieldtype": "Link",
				"label": "Sales Invoice",
				"options": "Sales Invoice",
				"insert_after": "return_reason",
				"read_only": 1,
			},
		],
	}
	create_custom_fields(custom_fields)
