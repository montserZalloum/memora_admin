// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Product Grant", {
	setup(frm) {
		// Filter major by grade
		frm.set_query("major", function () {
			if (!frm.doc.grade) {
				return { filters: { name: ["in", []] } };
			}
			return {
				query: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.get_majors_for_grade",
				filters: { grade: frm.doc.grade },
			};
		});

		// Filter plan by grade + major
		frm.set_query("plan", function () {
			const filters = {};
			if (frm.doc.grade) filters.grade = frm.doc.grade;
			if (frm.doc.major) filters.major = frm.doc.major;
			return { filters };
		});

		// Filter target_name in grant_components by plan
		frm.set_query("target_name", "grant_components", function (doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			if (!doc.plan) return {};

			if (row.target_doctype === "Memora Subject") {
				return {
					query: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.get_subjects_for_plan",
					filters: { plan: doc.plan },
				};
			}
			if (row.target_doctype === "Memora Track") {
				return {
					query: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.get_tracks_for_plan",
					filters: { plan: doc.plan },
				};
			}
			return {};
		});
	},

	grade(frm) {
		frm.set_value("major", "");
		frm.set_value("plan", "");
	},

	major(frm) {
		frm.set_value("plan", "");
	},

	plan(frm) {
		(frm.doc.grant_components || []).forEach(function (row) {
			frappe.model.set_value(row.doctype, row.name, "target_name", "");
		});
		frm.refresh_field("grant_components");
	},
});

frappe.ui.form.on("Memora Grant Component", {
	target_doctype(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "target_name", "");
		const row = locals[cdt][cdn];
		if (row.target_doctype === "Memora Track") {
			frappe.model.set_value(cdt, cdn, "key_type", "full");
		}
	},
});
