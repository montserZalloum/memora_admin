// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Voucher Card", {
	refresh(frm) {
		// Double-ensure pin_hmac is never visible (defense-in-depth with JSON hidden:1)
		frm.set_df_property("pin_hmac", "hidden", 1);

		// Make status read-only in form (status changes happen via code, not manual edits)
		if (!frm.is_new()) {
			frm.set_df_property("status", "read_only", 1);
		}
	},
});
