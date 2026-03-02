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
			frm.set_df_property("batch_purpose", "read_only", 1);
		}

		// Lock face_value for non-Sale batches (mirrors batch_purpose change handler)
		if (frm.doc.batch_purpose && frm.doc.batch_purpose !== "Sale") {
			frm.set_df_property("face_value", "read_only", 1);
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

		// Export for Print button: Generated or Active batches with an export file
		if (["Generated", "Active"].includes(frm.doc.status) && frm.doc.encrypted_file_url) {
			frm.add_custom_button(
				__("Export for Print"),
				function () {
					window.open(
						frappe.request.url +
							"?cmd=memora_admin.memora_admin.api.voucher.export_for_print&batch_name=" +
							encodeURIComponent(frm.doc.name)
					);
					// Reload form after brief delay to show new export_log entry
					setTimeout(function () {
						frm.reload_doc();
					}, 2000);
				},
				__("Actions")
			);
		}

		// Void Batch button: Generated or Active batches
		if (["Generated", "Active"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Void Batch"),
				function () {
					frappe.prompt(
						[
							{
								fieldname: "void_reason",
								fieldtype: "Small Text",
								label: __("Void Reason"),
								reqd: 1,
								description: __(
									"This will void ALL non-final cards and close the batch. This cannot be undone."
								),
							},
						],
						function (values) {
							frappe.call({
								method: "memora_admin.memora_admin.api.voucher.void_batch",
								args: {
									batch_name: frm.doc.name,
									void_reason: values.void_reason,
								},
								callback: function (r) {
									if (r.message) {
										frappe.show_alert({
											message: __(
												"{0} cards voided. Batch closed.",
												[r.message.voided_count]
											),
											indicator: "orange",
										});
										frm.reload_doc();
									}
								},
							});
						},
						__("Void Batch"),
						__("Void")
					);
				},
				__("Actions")
			);
			// Make the Void Batch button red
			frm.change_custom_button_type(__("Void Batch"), __("Actions"), "danger");
		}

		// Direct Activate button: non-Sale batches in Generated status
		if (frm.doc.batch_purpose && frm.doc.batch_purpose !== "Sale" && frm.doc.status === "Generated") {
			frm.add_custom_button(
				__("Direct Activate"),
				function () {
					frappe.confirm(
						__(
							"Directly activate all {0} cards? This will set them to Allocated with library 'Admin-Direct'. This cannot be undone.",
							[frm.doc.generated_count || frm.doc.quantity]
						),
						function () {
							frappe.call({
								method: "memora_admin.memora_admin.api.voucher.direct_activate",
								args: { batch_name: frm.doc.name },
								callback: function (r) {
									if (r.message) {
										frappe.show_alert({
											message: __("{0} cards activated.", [r.message.activated_count]),
											indicator: "green",
										});
										frm.reload_doc();
									}
								},
							});
						}
					);
				},
				__("Actions")
			);
			frm.change_custom_button_type(__("Direct Activate"), __("Actions"), "primary");
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

	batch_purpose(frm) {
		if (frm.doc.batch_purpose && frm.doc.batch_purpose !== "Sale") {
			frm.set_value("face_value", 0);
			frm.set_df_property("face_value", "read_only", 1);
		} else {
			frm.set_df_property("face_value", "read_only", frm.doc.status !== "Draft" ? 1 : 0);
		}
	},
});
