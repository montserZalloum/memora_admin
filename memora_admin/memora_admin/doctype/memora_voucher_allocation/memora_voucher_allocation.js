// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Voucher Allocation", {
	refresh(frm) {
		// Make type and batch read-only after Draft
		if (frm.doc.status !== "Draft") {
			frm.set_df_property("allocation_type", "read_only", 1);
			frm.set_df_property("batch", "read_only", 1);
			frm.set_df_property("customer", "read_only", 1);
			frm.set_df_property("sale_model", "read_only", 1);
			frm.set_df_property("allocation_cards", "read_only", 1);
		}
	},
});
