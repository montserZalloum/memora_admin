// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Voucher Batch", {
	refresh(frm) {
		// Make configuration fields read-only after generation
		if (frm.doc.status !== "Draft") {
			frm.set_df_property("quantity", "read_only", 1);
			frm.set_df_property("pin_length", "read_only", 1);
			frm.set_df_property("face_value", "read_only", 1);
			frm.set_df_property("batch_grants", "read_only", 1);
		}

		// Export log is always read-only
		frm.set_df_property("export_log", "read_only", 1);

		// Generate Cards button: only on saved Draft batches
		if (frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(
				__("Generate Cards"),
				function () {
					frappe.confirm(
						__("Generate {0} cards for this batch? This cannot be undone.", [frm.doc.quantity]),
						function () {
							frappe.call({
								method: "memora_admin.memora_admin.api.voucher.generate_batch",
								args: { batch_name: frm.doc.name },
								callback: function (r) {
									if (r.message && r.message.status === "enqueued") {
										frappe.show_alert({
											message: __("Card generation has been queued. You will be notified when complete."),
											indicator: "blue",
										});
									}
								},
							});
						}
					);
				},
				__("Actions")
			);
		}

		// Real-time event listeners
		frappe.realtime.on("batch_generation_complete", function (data) {
			if (data.batch_name === frm.doc.name) {
				frappe.show_alert({
					message: __("{0} cards generated successfully!", [data.count]),
					indicator: "green",
				});
				frm.reload_doc();
			}
		});

		frappe.realtime.on("batch_generation_failed", function (data) {
			if (data.batch_name === frm.doc.name) {
				frappe.msgprint({
					title: __("Generation Failed"),
					message: __("Card generation failed for this batch. Check Error Log for details."),
					indicator: "red",
				});
			}
		});
	},
});
