// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Lesson", {
	topic(frm) {
		if (frm.doc.topic) {
			frappe.db.get_value(
				"Memora Topic",
				frm.doc.topic,
				["unit", "track", "subject"],
				(r) => {
					if (r) {
						frm.set_value("unit", r.unit);
						frm.set_value("track", r.track);
						frm.set_value("subject", r.subject);
					}
				}
			);
		} else {
			frm.set_value("unit", null);
			frm.set_value("track", null);
			frm.set_value("subject", null);
		}
	},
});
