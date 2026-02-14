// Copyright (c) 2026, Conan Academy and contributors
// For license information, please see license.txt

frappe.query_reports["Sales by Library"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "library",
			label: __("Library"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "sale_model",
			label: __("Sale Model"),
			fieldtype: "Select",
			options: "\nPrepaid\nConsignment",
		},
	],
};
