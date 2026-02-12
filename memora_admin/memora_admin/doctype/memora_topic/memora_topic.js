// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Topic", {
	refresh(frm) {
		MemoraAdminFilter.setup(frm, function (filter_doc) {
			if (filter_doc && filter_doc.unit) {
				frm.set_query("unit", () => ({
					filters: { name: filter_doc.unit },
				}));
			} else if (filter_doc && filter_doc.track) {
				frm.set_query("unit", () => ({
					filters: { track: filter_doc.track },
				}));
			} else if (filter_doc && filter_doc.subject) {
				frm.set_query("unit", () => ({
					filters: { subject: filter_doc.subject },
				}));
			} else {
				frm.set_query("unit", () => ({}));
			}
			frm.refresh_field("unit");
		});
	},

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
