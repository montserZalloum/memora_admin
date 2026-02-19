// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Season", {
	setup(frm) {
		// Preview the next season_seq for new records (field is read_only in schema)
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
