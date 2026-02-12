// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

const DOWNSTREAM = {
	grade: ["major", "academic_plan", "subject", "track", "unit", "topic"],
	season: ["academic_plan", "subject", "track", "unit", "topic"],
	major: ["academic_plan", "subject", "track", "unit", "topic"],
	academic_plan: ["subject", "track", "unit", "topic"],
	subject: ["track", "unit", "topic"],
	track: ["unit", "topic"],
	unit: ["topic"],
};

function clear_downstream(frm, field) {
	(DOWNSTREAM[field] || []).forEach((f) => frm.set_value(f, ""));
}

function setup_cascading_filters(frm) {
	// Major: filtered by grade's child table
	frm.set_query("major", () => {
		if (frm.doc.grade) {
			return {
				query: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.get_majors_for_grade",
				filters: { grade: frm.doc.grade },
			};
		}
		return {};
	});

	// Academic Plan: filtered by season + grade + major
	frm.set_query("academic_plan", () => {
		let filters = {};
		if (frm.doc.season) filters.season = frm.doc.season;
		if (frm.doc.grade) filters.grade = frm.doc.grade;
		if (frm.doc.major) filters.major = frm.doc.major;
		return { filters };
	});

	// Subject: filtered by plan (custom query)
	frm.set_query("subject", () => {
		if (frm.doc.academic_plan) {
			return {
				query: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.get_subjects_for_plan",
				filters: { plan: frm.doc.academic_plan },
			};
		}
		return {};
	});

	// Track: filtered by subject
	frm.set_query("track", () => {
		if (frm.doc.subject) {
			return { filters: { subject: frm.doc.subject } };
		}
		return {};
	});

	// Unit: filtered by track
	frm.set_query("unit", () => {
		if (frm.doc.track) {
			return { filters: { track: frm.doc.track } };
		}
		return {};
	});

	// Topic: filtered by unit
	frm.set_query("topic", () => {
		if (frm.doc.unit) {
			return { filters: { unit: frm.doc.unit } };
		}
		return {};
	});
}

frappe.ui.form.on("Memora Admin Filter", {
	refresh(frm) {
		setup_cascading_filters(frm);
	},

	grade(frm) {
		clear_downstream(frm, "grade");
		setup_cascading_filters(frm);
	},

	season(frm) {
		clear_downstream(frm, "season");
		setup_cascading_filters(frm);
	},

	major(frm) {
		clear_downstream(frm, "major");
		setup_cascading_filters(frm);
	},

	academic_plan(frm) {
		clear_downstream(frm, "academic_plan");
		setup_cascading_filters(frm);
	},

	subject(frm) {
		clear_downstream(frm, "subject");
		setup_cascading_filters(frm);
	},

	track(frm) {
		clear_downstream(frm, "track");
		setup_cascading_filters(frm);
	},

	unit(frm) {
		clear_downstream(frm, "unit");
		setup_cascading_filters(frm);
	},

	test_filter_btn(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Please save the filter before testing."));
			return;
		}

		frappe.call({
			method: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.test_filter",
			args: {
				filter_name: frm.doc.name,
				level: frm.doc.test_level || "",
			},
			callback(r) {
				if (r.message) {
					frm.fields_dict.test_results_html.$wrapper.html(r.message);
				}
			},
		});
	},
});
