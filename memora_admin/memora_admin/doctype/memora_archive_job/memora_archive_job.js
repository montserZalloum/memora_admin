// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Archive Job", {
	refresh(frm) {
		// All fields are read-only — no user editing
		frm.disable_form();
	},

	retry_btn(frm) {
		frappe.confirm(__("Are you sure you want to retry this failed archive job?"), function () {
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
		});
	},

	clear_pause_btn(frm) {
		frappe.confirm(__("Are you sure you want to clear the sync pause on this job?"), function () {
			frappe.call({
				method:
					"memora_admin.memora_admin.doctype.memora_archive_job.memora_archive_job.clear_sync_pause",
				args: { job_name: frm.doc.name },
				callback: function (r) {
					if (r.message && r.message.status === "success") {
						frappe.show_alert({
							message: __("Sync pause has been cleared."),
							indicator: "green",
						});
						frm.reload_doc();
					}
				},
			});
		});
	},
});
