// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Lesson", {
	refresh(frm) {
		MemoraAdminFilter.setup(frm, function (filter_doc) {
			if (filter_doc && filter_doc.unit) {
				frm.set_query("topic", () => ({
					filters: { unit: filter_doc.unit },
				}));
			} else if (filter_doc && filter_doc.track) {
				frm.set_query("topic", () => ({
					filters: { track: filter_doc.track },
				}));
			} else if (filter_doc && filter_doc.subject) {
				frm.set_query("topic", () => ({
					filters: { subject: filter_doc.subject },
				}));
			} else {
				frm.set_query("topic", () => ({}));
			}
			frm.refresh_field("topic");
		});
	},

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
