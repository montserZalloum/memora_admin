// Copyright (c) 2026, Conan Academy and contributors
// For license information, please see license.txt

frappe.query_reports["Batch Performance"] = {
	filters: [
		{
			fieldname: "batch",
			label: __("Batch"),
			fieldtype: "Link",
			options: "Memora Voucher Batch",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nGenerated\nActive\nClosed",
		},
	],
};
