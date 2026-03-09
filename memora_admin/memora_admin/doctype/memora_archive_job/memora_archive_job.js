// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Archive Job", {
	refresh(frm) {
		// All fields are read-only — no user editing
		frm.disable_form();

		// Show retry button only for Failed jobs
		if (frm.doc.status === "Failed") {
			frm.add_custom_button(__("Retry"), function () {
				frappe.confirm(
					__("Are you sure you want to retry this failed archive job?"),
					function () {
						frappe.call({
							method:
								"memora_admin.memora_admin.doctype.memora_archive_job.memora_archive_job.retry_archive_job",
							args: { job_name: frm.doc.name },
							callback: function (r) {
								if (r.message && r.message.status === "success") {
									frappe.show_alert({
										message: __("Job has been reset to Pending for re-processing."),
										indicator: "green",
									});
									frm.reload_doc();
								}
							},
						});
					}
				);
			});
		}
	},
});
