// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Unit", {
	refresh(frm) {
		MemoraAdminFilter.setup(frm, function (filter_doc) {
			if (filter_doc && filter_doc.track) {
				frm.set_query("track", () => ({
					filters: { name: filter_doc.track },
				}));
			} else if (filter_doc && filter_doc.subject) {
				frm.set_query("track", () => ({
					filters: { subject: filter_doc.subject },
				}));
			} else if (filter_doc && filter_doc.academic_plan) {
				frm.set_query("track", () => ({
					query: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.get_tracks_for_plan",
					filters: { plan: filter_doc.academic_plan },
				}));
			} else {
				frm.set_query("track", () => ({}));
			}
			frm.refresh_field("track");
		});
	},

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
