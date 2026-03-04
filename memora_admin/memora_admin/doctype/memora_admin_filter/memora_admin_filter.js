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

function clear_test_results(frm) {
	let wrapper = frm.fields_dict.test_results_html?.$wrapper;
	if (wrapper) wrapper.html("");
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

	// Unit: filtered by most specific ancestor
	frm.set_query("unit", () => {
		if (frm.doc.track) {
			return { filters: { track: frm.doc.track } };
		} else if (frm.doc.subject) {
			return { filters: { subject: frm.doc.subject } };
		}
		return {};
	});

	// Topic: filtered by most specific ancestor
	frm.set_query("topic", () => {
		if (frm.doc.unit) {
			return { filters: { unit: frm.doc.unit } };
		} else if (frm.doc.track) {
			return { filters: { track: frm.doc.track } };
		} else if (frm.doc.subject) {
			return { filters: { subject: frm.doc.subject } };
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
		clear_test_results(frm);
		setup_cascading_filters(frm);
	},

	season(frm) {
		clear_downstream(frm, "season");
		clear_test_results(frm);
		setup_cascading_filters(frm);
	},

	major(frm) {
		clear_downstream(frm, "major");
		clear_test_results(frm);
		setup_cascading_filters(frm);
	},

	academic_plan(frm) {
		clear_downstream(frm, "academic_plan");
		clear_test_results(frm);
		setup_cascading_filters(frm);
	},

	subject(frm) {
		clear_downstream(frm, "subject");
		clear_test_results(frm);
		setup_cascading_filters(frm);
	},

	track(frm) {
		clear_downstream(frm, "track");
		clear_test_results(frm);
		setup_cascading_filters(frm);
	},

	unit(frm) {
		clear_downstream(frm, "unit");
		clear_test_results(frm);
		setup_cascading_filters(frm);
	},

	test_level(frm) {
		clear_test_results(frm);
	},

	test_filter_btn(frm) {
		let wrapper = frm.fields_dict.test_results_html.$wrapper;
		wrapper.html(
			'<div style="text-align:center;padding:20px;"><span class="loading-text">' +
				__("Loading...") +
				"</span></div>"
		);

		frappe.call({
			method: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.test_filter",
			args: {
				academic_plan: frm.doc.academic_plan || "",
				subject: frm.doc.subject || "",
				track: frm.doc.track || "",
				unit: frm.doc.unit || "",
				topic: frm.doc.topic || "",
				level: frm.doc.test_level || "",
			},
			callback(r) {
				wrapper.html(r.message || "");
			},
			error() {
				wrapper.html("");
			},
		});
	},
});
