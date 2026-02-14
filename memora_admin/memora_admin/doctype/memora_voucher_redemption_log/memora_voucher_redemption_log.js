// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Voucher Redemption Log", {
	refresh(frm) {
		// Redemption logs are immutable -- disable save button
		if (!frm.is_new()) {
			frm.disable_save();
		}
	},
});
