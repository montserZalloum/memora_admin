// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Academic Plan", {
	refresh(frm) {
		// Set up Major filter on form load
		frm.trigger("setup_major_filter");
		frm.trigger("setup_subject_filter");
	},

	grade(frm) {
		// When Grade changes, clear Major and refresh filter
		frm.set_value("major", null);
		frm.trigger("setup_major_filter");
		frm.trigger("setup_subject_filter");
	},

	major(frm) {
		// When Major changes, refresh subject filter
		frm.trigger("setup_subject_filter");
	},

	setup_subject_filter(frm) {
		// Filter Subject dropdown in plan_subjects based on grade/major applicability
		frm.fields_dict.plan_subjects.grid.get_field("subject").get_query = function () {
			if (!frm.doc.grade) {
				return { filters: { name: ["in", []] } };
			}
			return {
				query: "memora_admin.memora_admin.doctype.memora_subject.memora_subject.get_applicable_subjects",
				filters: {
					grade: frm.doc.grade,
					major: frm.doc.major || "",
				},
			};
		};
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
