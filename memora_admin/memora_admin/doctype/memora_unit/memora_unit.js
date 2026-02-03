// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Unit", {
	track(frm) {
		if (frm.doc.track) {
			frappe.db.get_value("Memora Track", frm.doc.track, ["subject"], (r) => {
				if (r) {
					frm.set_value("subject", r.subject);
				}
			});
		} else {
			frm.set_value("subject", null);
		}
	},
});
