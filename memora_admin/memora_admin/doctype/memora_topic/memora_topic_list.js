// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

const ACADEMIC_PLAN_FILTER_FIELD = "academic_plan_filter";

frappe.listview_settings["Memora Topic"] = {
	onload(listview) {
		// Holds the subjects of the currently selected Academic Plan. Empty = no plan,
		// which means the Subject/Track/Unit dropdowns stay unconstrained.
		listview._plan_subjects = [];

		// "Academic Plan" is not a field on Topic. We add it as a custom control that
		// resolves the plan to its subjects and applies a `subject IN (...)` filter.
		const plan_field = listview.page.add_field({
			fieldtype: "Link",
			label: __("Academic Plan"),
			fieldname: ACADEMIC_PLAN_FILTER_FIELD,
			options: "Memora Academic Plan",
			change() {
				apply_academic_plan_filter(listview, plan_field.get_value());
			},
		});

		// FilterArea.get_standard_filters() iterates every page field (incl. ours) and
		// pushes it into the query. Our control is virtual (no such column on Topic),
		// so strip it out here — otherwise Frappe raises "Field not permitted in query".
		// This also removes it from filter_area.get() (used by "new doc from filter").
		const filter_area = listview.filter_area;
		const original_get_standard_filters = filter_area.get_standard_filters.bind(filter_area);
		filter_area.get_standard_filters = function () {
			return original_get_standard_filters().filter(
				(filter) => filter[1] !== ACADEMIC_PLAN_FILTER_FIELD
			);
		};

		setup_cascading_filters(listview);
	},
};

// Make the Subject/Track/Unit standard-filter dropdowns cascade:
//   - Subject is limited to the selected Academic Plan's subjects (if any).
//   - Track is limited to the selected Subject (else the plan's subjects).
//   - Unit is limited to the selected Track and/or Subject (else the plan's subjects).
// get_query is evaluated each time a dropdown opens, so it always reads the latest
// sibling values without needing to be rebuilt.
function setup_cascading_filters(listview) {
	const fields_dict = listview.page.fields_dict || {};
	const value_of = (fieldname) =>
		fields_dict[fieldname] ? fields_dict[fieldname].get_value() : null;
	const plan_subjects = () => listview._plan_subjects || [];

	// Resolve the subject constraint shared by Track and Unit: a picked Subject wins,
	// otherwise fall back to the plan's subjects, otherwise no constraint.
	const subject_constraint = () => {
		const subject = value_of("subject");
		if (subject) return subject;
		if (plan_subjects().length) return ["in", plan_subjects()];
		return null;
	};

	if (fields_dict.subject) {
		fields_dict.subject.get_query = () => {
			const subjects = plan_subjects();
			return subjects.length ? { filters: { name: ["in", subjects] } } : {};
		};
	}

	if (fields_dict.track) {
		fields_dict.track.get_query = () => {
			const subject = subject_constraint();
			return subject ? { filters: { subject } } : {};
		};
	}

	if (fields_dict.unit) {
		fields_dict.unit.get_query = () => {
			const filters = {};
			const track = value_of("track");
			if (track) filters.track = track;
			const subject = subject_constraint();
			if (subject) filters.subject = subject;
			return Object.keys(filters).length ? { filters } : {};
		};
	}
}

function apply_academic_plan_filter(listview, plan) {
	const filter_area = listview.filter_area;

	// Drop any subject filter this control set previously before applying a new one.
	filter_area.remove("subject");

	if (!plan) {
		listview._plan_subjects = [];
		listview.refresh();
		return;
	}

	frappe.call({
		method: "memora_admin.memora_admin.doctype.memora_topic.memora_topic.get_plan_subjects",
		args: { plan },
		callback(r) {
			const subjects = r.message || [];
			listview._plan_subjects = subjects;
			// Sentinel keeps the list empty when a plan has no subjects (rather than
			// silently dropping the filter and showing everything).
			const value = subjects.length ? subjects : ["__no_subject__"];
			filter_area.add([["Memora Topic", "subject", "in", value]]);
		},
	});
}
