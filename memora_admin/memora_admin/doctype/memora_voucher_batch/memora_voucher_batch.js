// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Voucher Batch", {
	refresh(frm) {
		// Make configuration fields read-only after generation
		if (frm.doc.status !== "Draft") {
			frm.set_df_property("quantity", "read_only", 1);
			frm.set_df_property("pin_length", "read_only", 1);
			frm.set_df_property("face_value", "read_only", 1);
			frm.set_df_property("batch_grants", "read_only", 1);
		}
	},
});
