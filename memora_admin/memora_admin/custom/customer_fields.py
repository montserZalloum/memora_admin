# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Custom fields for ERPNext Customer DocType (voucher settings)."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def add_customer_voucher_fields():
	"""Add voucher settings to Customer DocType. Idempotent -- safe to call repeatedly."""
	custom_fields = {
		"Customer": [
			{
				"fieldname": "voucher_settings_section",
				"fieldtype": "Section Break",
				"label": "Voucher Settings",
				"insert_after": "default_currency",
			},
			{
				"fieldname": "voucher_requires_approval",
				"fieldtype": "Check",
				"label": "Voucher Requires Approval",
				"insert_after": "voucher_settings_section",
				"default": "0",
				"description": "If checked, voucher allocations to this library require admin approval before cards are activated",
			},
			{
				"fieldname": "voucher_commission_cb",
				"fieldtype": "Column Break",
				"insert_after": "voucher_requires_approval",
			},
			{
				"fieldname": "voucher_commission_type",
				"fieldtype": "Select",
				"label": "Commission Type",
				"insert_after": "voucher_commission_cb",
				"options": "\nPercentage\nFixed Amount",
				"description": "How commission is calculated for this library",
			},
			{
				"fieldname": "voucher_commission_value",
				"fieldtype": "Data",
				"label": "Commission Value",
				"insert_after": "voucher_commission_type",
				"description": "Percentage rate or fixed amount per card. Stored as string, parsed as Decimal in Python for precision.",
			},
		]
	}
	create_custom_fields(custom_fields)
