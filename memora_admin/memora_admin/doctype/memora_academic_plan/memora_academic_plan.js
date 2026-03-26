// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Academic Plan", {
	refresh(frm) {
		// Season, Grade, and Major are immutable after creation
		const locked = !frm.is_new();
		frm.set_df_property("season", "read_only", locked);
		frm.set_df_property("grade", "read_only", locked);
		frm.set_df_property("major", "read_only", locked);

		// Freeze form if the linked season has ended
		if (locked && frm.doc.season) {
			frappe.db.get_value("Memora Season", frm.doc.season, "end_date", (r) => {
				if (r && r.end_date) {
					const end_date = frappe.datetime.str_to_obj(r.end_date);
					const now = frappe.datetime.str_to_obj(frappe.datetime.nowdate());
					if (end_date < now) {
						frm.disable_save();
						frm.set_intro(
							__("This Academic Plan cannot be modified because its season has already ended."),
							"red"
						);
						frm.fields.forEach((field) => {
							if (field.df.fieldname) {
								frm.set_df_property(field.df.fieldname, "read_only", 1);
							}
						});
						// Lock child table: disable add/delete and make all row fields read-only
						if (frm.fields_dict.plan_subjects) {
							const grid = frm.fields_dict.plan_subjects.grid;
							grid.cannot_add_rows = true;
							grid.cannot_delete_rows = true;
							grid.wrapper
								.find(".grid-add-row, .grid-remove-rows, .row-check")
								.hide();
							["subject", "alias_title", "notes", "meta_data", "is_premium"].forEach((f) => {
								grid.toggle_enable(f, false);
							});
							grid.refresh();
						}
					}
				}
			});
		}

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
