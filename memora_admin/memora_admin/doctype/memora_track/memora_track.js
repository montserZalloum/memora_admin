// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

const STORAGE_KEY = "admin_filters";

function load_filters() {
	try {
		return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
	} catch {
		return {};
	}
}

function save_filter(doctype, field, value) {
	const filters = load_filters();
	if (!filters[doctype]) filters[doctype] = {};
	filters[doctype][field] = value;
	localStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
}

function apply_plan_filter(frm, plan) {
	save_filter("Memora Track", "academic_plan", plan || "");

	if (!plan && frm.doc.subject) {
		frm.set_value("subject", "");
	}

	frm.set_query("subject", function () {
		if (plan) {
			return {
				query: "memora_admin.memora_admin.doctype.memora_track.memora_track.get_subjects_for_plan",
				filters: { plan: plan },
			};
		}
		return {};
	});

	frm.refresh_field("subject");
}

frappe.ui.form.on("Memora Track", {
	refresh(frm) {
		if (frm._plan_control) return;

		const wrapper = frm.fields_dict.academic_plan_html.$wrapper;
		wrapper.empty();

		const control = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				fieldname: "academic_plan",
				label: __("Academic Plan"),
				options: "Memora Academic Plan",
				description: __("Pick a plan to filter the Subject dropdown below"),
				change() {
					apply_plan_filter(frm, control.get_value());
				},
			},
			parent: wrapper,
			render_input: true,
		});

		// Restore last used plan from localStorage
		const saved = load_filters()["Memora Track"];
		if (saved?.academic_plan) {
			control.set_value(saved.academic_plan);
			apply_plan_filter(frm, saved.academic_plan);
		}

		frm._plan_control = control;
	},
});
