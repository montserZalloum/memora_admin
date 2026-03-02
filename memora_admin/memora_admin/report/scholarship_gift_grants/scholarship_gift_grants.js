// Copyright (c) 2026, Conan Academy and contributors
// For license information, please see license.txt

frappe.query_reports["Scholarship Gift Grants"] = {
	filters: [
		{
			fieldname: "batch_purpose",
			label: __("Purpose"),
			fieldtype: "Select",
			options: "\nScholarship\nGift\nPromotion",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "product_grant",
			label: __("Product Grant"),
			fieldtype: "Link",
			options: "Memora Product Grant",
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "redeemed" && data && data.redeemed > 0) {
			var url =
				"/app/memora-voucher-card?batch=" +
				encodeURIComponent(data.batch) +
				"&status=Redeemed";
			value = '<a href="' + url + '">' + data.redeemed + "</a>";
		}
		return value;
	},
};
