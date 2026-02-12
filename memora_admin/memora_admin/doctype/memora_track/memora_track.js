// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Track", {
	refresh(frm) {
		MemoraAdminFilter.setup(frm, function (filter_doc) {
			if (filter_doc && filter_doc.subject) {
				frm.set_query("subject", () => ({
					filters: { name: filter_doc.subject },
				}));
			} else if (filter_doc && filter_doc.academic_plan) {
				frm.set_query("subject", () => ({
					query: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.get_subjects_for_plan",
					filters: { plan: filter_doc.academic_plan },
				}));
			} else {
				frm.set_query("subject", () => ({}));
			}
			frm.refresh_field("subject");
		});
	},
});
