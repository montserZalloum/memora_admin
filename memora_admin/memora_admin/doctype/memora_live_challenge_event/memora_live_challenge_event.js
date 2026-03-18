// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Live Challenge Event", {
	refresh(frm) {
		// Status indicator colors
		if (frm.doc.status === "Draft") {
			frm.page.set_indicator("Draft", "orange");
		} else if (frm.doc.status === "Waiting") {
			frm.page.set_indicator("Waiting", "blue");
		} else if (frm.doc.status === "Active") {
			frm.page.set_indicator("Active", "green");
		} else if (frm.doc.status === "Ended") {
			frm.page.set_indicator("Ended", "darkgrey");
		}

		// Freeze form when not in Draft
		if (frm.doc.status !== "Draft" && !frm.is_new()) {
			frm.disable_save();
			frm.set_read_only();
		}

		// Read-only indicators for computed fields
		frm.set_df_property("exam_start_ts", "read_only", 1);
		frm.set_df_property("exam_end_ts", "read_only", 1);

		// Apply question timer logic on refresh
		_apply_question_timer(frm);

		// Import Review Items button (only in Draft)
		if (frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(__("Import Review Items"), function () {
				frappe.prompt(
					{
						label: "Review Item IDs",
						fieldname: "review_item_ids",
						fieldtype: "Small Text",
						description: "Enter Review Item IDs (one per line)",
						reqd: 1,
					},
					function (values) {
						let ids = values.review_item_ids
							.split("\n")
							.map((s) => s.trim())
							.filter((s) => s);
						frappe.call({
							method: "memora_admin.memora_admin.api.live_challenge.import_review_items",
							args: {
								event_id: frm.doc.name,
								review_item_ids: ids,
							},
							callback: function (r) {
								if (r.message) {
									frappe.msgprint(
										__("Imported {0} questions.", [r.message.imported_count])
									);
									frm.reload_doc();
								}
							},
						});
					},
					__("Import Review Items"),
					__("Import")
				);
			});
		}
	},

	enable_question_timer(frm) {
		_apply_question_timer(frm);
	},

	question_time_limit(frm) {
		_calc_exam_duration(frm);
	},
});

frappe.ui.form.on("Memora Live Challenge Question", {
	questions_add(frm) {
		_calc_exam_duration(frm);
	},
	questions_remove(frm) {
		_calc_exam_duration(frm);
	},
});

function _apply_question_timer(frm) {
	frm.toggle_display("question_time_limit", frm.doc.enable_question_timer);
	frm.toggle_display("exam_duration", !frm.doc.enable_question_timer);
	if (frm.doc.enable_question_timer) {
		_calc_exam_duration(frm);
	}
}

function _calc_exam_duration(frm) {
	if (!frm.doc.enable_question_timer) return;
	let count = (frm.doc.questions || []).length;
	let limit = cint(frm.doc.question_time_limit) || 30;
	let minutes = Math.ceil((limit * count) / 60);
	frm.set_value("exam_duration", Math.max(minutes, 1));
}
