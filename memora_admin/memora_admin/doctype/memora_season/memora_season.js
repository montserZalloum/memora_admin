// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Season", {
	setup(frm) {
		if (frm.is_new() && !frm.doc.season_seq) {
			frappe.db.get_list("Memora Season", {
				fields: ["MAX(season_seq) as max_seq"],
				limit: 1,
			}).then((r) => {
				const next = (r.length && r[0].max_seq ? r[0].max_seq : 0) + 1;
				frm.set_value("season_seq", next);
			});
		}
	},
});
