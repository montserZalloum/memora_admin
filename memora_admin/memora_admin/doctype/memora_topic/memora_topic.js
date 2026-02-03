// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Topic", {
	unit(frm) {
		if (frm.doc.unit) {
			frappe.db.get_value("Memora Unit", frm.doc.unit, ["track", "subject"], (r) => {
				if (r) {
					frm.set_value("track", r.track);
					frm.set_value("subject", r.subject);
				}
			});
		} else {
			frm.set_value("track", null);
			frm.set_value("subject", null);
		}
	},
});
