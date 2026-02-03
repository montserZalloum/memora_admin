// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Academic Plan", {
	refresh(frm) {
		// Set up Major filter on form load
		frm.trigger("setup_major_filter");
	},

	grade(frm) {
		// When Grade changes, clear Major and refresh filter
		frm.set_value("major", null);
		frm.trigger("setup_major_filter");
	},

	setup_major_filter(frm) {
		// Filter Major dropdown based on selected Grade's majors
		frm.set_query("major", function () {
			if (!frm.doc.grade) {
				// No grade selected - show no majors
				return {
					filters: {
						name: ["in", []],
					},
				};
			}

			// Get majors from the selected grade via server query
			return {
				query: "memora_admin.memora_admin.doctype.memora_academic_plan.memora_academic_plan.get_grade_majors",
				filters: {
					grade: frm.doc.grade,
				},
			};
		});

		// Refresh the major field to apply the new filter
		frm.refresh_field("major");
	},
});
