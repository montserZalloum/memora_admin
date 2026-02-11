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

frappe.ui.form.on("Memora Track", {
	refresh(frm) {
		if (frm.is_new()) {
			// Restore last used plan from localStorage
			const saved = load_filters()["Memora Track"];
			if (saved?.academic_plan) {
				frm.set_value("academic_plan", saved.academic_plan);
			}
		}
	},

	academic_plan(frm) {
		const plan = frm.doc.academic_plan;

		// Save to localStorage
		save_filter("Memora Track", "academic_plan", plan || "");

		// Clear subject when plan changes (to avoid stale selection)
		if (frm.doc.subject) {
			frm.set_value("subject", "");
		}

		// Apply filter on subject field
		frm.set_query("subject", function () {
			if (plan) {
				return {
					query: "memora_admin.memora_admin.doctype.memora_track.memora_track.get_subjects_for_plan",
					filters: { plan: plan },
				};
			}
			// No plan selected — show all subjects (no filter)
			return {};
		});

		// Re-trigger the subject field so the dropdown refreshes
		frm.refresh_field("subject");
	},
});
