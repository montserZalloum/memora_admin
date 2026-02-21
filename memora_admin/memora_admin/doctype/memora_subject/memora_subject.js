// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Subject", {
	refresh(frm) {
		// Only show Force Build button if doc is saved (not new)
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Force Build"),
				function () {
					frappe.call({
						method: "memora_admin.api.build.queue_manual_build",
						args: {
							subject_id: frm.doc.name,
						},
						callback: function (r) {
							if (r.message && r.message.success) {
								let count = (r.message.build_ids || []).length;
								frappe.show_alert(
									{
										message: count
											? __("{0} plan build(s) queued", [count])
											: __("No plans found for this subject"),
										indicator: count ? "green" : "orange",
									},
									5
								);
							}
						},
						error: function (r) {
							frappe.show_alert(
								{
									message: __("Failed to queue build"),
									indicator: "red",
								},
								5
							);
						},
					});
				},
				__("Actions")
			);
		}
	},
});
