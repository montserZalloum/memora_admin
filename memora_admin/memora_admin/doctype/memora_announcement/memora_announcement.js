// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Announcement", {
	refresh(frm) {
		toggle_fields(frm);
	},

	target_audience(frm) {
		toggle_fields(frm);
	},

	duration_type(frm) {
		toggle_fields(frm);
	},
});

function toggle_fields(frm) {
	// Target plans only visible for "Specific Plans"
	frm.toggle_display("target_plans", frm.doc.target_audience === "Specific Plans");
	frm.toggle_reqd("target_plans", frm.doc.target_audience === "Specific Plans");

	// Date Range fields
	const is_date_range = frm.doc.duration_type === "Date Range";
	frm.toggle_display("start_date", is_date_range);
	frm.toggle_display("end_date", is_date_range);
	frm.toggle_reqd("start_date", is_date_range);
	frm.toggle_reqd("end_date", is_date_range);

	// Fixed Duration fields
	const is_fixed = frm.doc.duration_type === "Fixed Duration";
	frm.toggle_display("duration_days", is_fixed);
	frm.toggle_reqd("duration_days", is_fixed);
}
