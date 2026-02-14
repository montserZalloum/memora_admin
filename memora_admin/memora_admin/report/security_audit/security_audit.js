// Copyright (c) 2026, Conan Academy and contributors
// For license information, please see license.txt

frappe.query_reports["Security Audit"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -7),
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
			fieldname: "player",
			label: __("Player"),
			fieldtype: "Link",
			options: "Memora Player Profile",
		},
		{
			fieldname: "failure_type",
			label: __("Failure Type"),
			fieldtype: "Select",
			options: "\nInvalid PIN\nAlready Redeemed\nExpired\nVoid\nNot Allocated\nBatch Inactive\nSeason Inactive\nRate Limited",
		},
	],
};
