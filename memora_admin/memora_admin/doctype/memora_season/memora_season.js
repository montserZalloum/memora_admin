// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Season", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.set_df_property("season_seq", "read_only", 1);
		}
	},
	setup(frm) {
		if (frm.is_new() && !frm.doc.season_seq) {
			frappe.call({
				method: "memora_admin.memora_admin.doctype.memora_season.memora_season.get_next_season_seq",
				callback(r) {
					if (r.message) {
						frm.set_value("season_seq", r.message);
					}
				},
			});
		}
	},
});
