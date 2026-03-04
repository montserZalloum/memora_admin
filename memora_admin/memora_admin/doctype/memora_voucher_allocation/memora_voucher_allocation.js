// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Voucher Allocation", {
	refresh(frm) {
		// Make fields read-only after Draft
		if (frm.doc.status !== "Draft") {
			frm.set_df_property("allocation_type", "read_only", 1);
			frm.set_df_property("batch", "read_only", 1);
			frm.set_df_property("customer", "read_only", 1);
			frm.set_df_property("sale_model", "read_only", 1);
			frm.set_df_property("allocation_cards", "read_only", 1);
		}

		// Fill Cards button: only on saved Draft allocations
		if (frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(
				__("Fill Cards"),
				function () {
					frappe.prompt(
						[
							{
								fieldname: "quantity",
								fieldtype: "Int",
								label: __("Number of Cards"),
								reqd: 1,
								description: __(
									"Enter the number of cards to fill. Leave 0 to fill all available."
								),
								default: 0,
							},
						],
						function (values) {
							frappe.call({
								method: "memora_admin.memora_admin.api.allocation.fill_cards",
								args: {
									allocation_name: frm.doc.name,
									quantity: values.quantity || 0,
								},
								freeze: true,
								freeze_message: __("Filling cards..."),
								callback: function (r) {
									if (r.message) {
										frappe.show_alert({
											message: __("{0} cards filled.", [
												r.message.filled_count,
											]),
											indicator: "green",
										});
										frm.reload_doc();
									}
								},
							});
						},
						__("Fill Cards"),
						__("Fill")
					);
				},
				__("Actions")
			);
		}

		// Submit Allocation button: only on saved Draft allocations with cards
		if (
			frm.doc.status === "Draft" &&
			!frm.is_new() &&
			frm.doc.allocation_cards &&
			frm.doc.allocation_cards.length > 0
		) {
			frm.add_custom_button(
				__("Submit Allocation"),
				function () {
					frappe.confirm(
						__(
							"Submit allocation of {0} cards to {1}? This will start the approval process.",
							[frm.doc.allocation_cards.length, frm.doc.customer]
						),
						function () {
							frappe.call({
								method: "memora_admin.memora_admin.api.allocation.submit_allocation",
								args: { allocation_name: frm.doc.name },
								freeze: true,
								freeze_message: __("Submitting allocation..."),
								callback: function (r) {
									if (r.message) {
										let indicator =
											r.message.status === "Completed" ? "green" : "blue";
										frappe.show_alert({
											message: __("Allocation {0}.", [r.message.status]),
											indicator: indicator,
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
			frm.change_custom_button_type(__("Submit Allocation"), __("Actions"), "primary");
		}

		// Approve button: only on Pending Approval allocations
		if (frm.doc.status === "Pending Approval") {
			frm.add_custom_button(
				__("Approve"),
				function () {
					frappe.confirm(
						__("Approve this allocation? {0} cards will be allocated to {1}.", [
							frm.doc.allocation_cards.length,
							frm.doc.customer,
						]),
						function () {
							frappe.call({
								method: "memora_admin.memora_admin.api.allocation.approve_allocation",
								args: { allocation_name: frm.doc.name },
								freeze: true,
								freeze_message: __("Approving allocation..."),
								callback: function (r) {
									if (r.message) {
										frappe.show_alert({
											message: __("Allocation approved and completed."),
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
			frm.change_custom_button_type(__("Approve"), __("Actions"), "primary");
		}

		// Reject button: only on Pending Approval allocations
		if (frm.doc.status === "Pending Approval") {
			frm.add_custom_button(
				__("Reject"),
				function () {
					frappe.prompt(
						[
							{
								fieldname: "reject_reason",
								fieldtype: "Small Text",
								label: __("Rejection Reason"),
								description: __(
									"Optionally provide a reason for rejecting this allocation."
								),
							},
						],
						function (values) {
							frappe.call({
								method: "memora_admin.memora_admin.api.allocation.reject_allocation",
								args: {
									allocation_name: frm.doc.name,
									reject_reason: values.reject_reason || "",
								},
								freeze: true,
								freeze_message: __("Rejecting allocation..."),
								callback: function (r) {
									if (r.message) {
										frappe.show_alert({
											message: __("Allocation rejected."),
											indicator: "orange",
										});
										frm.reload_doc();
									}
								},
							});
						},
						__("Reject Allocation"),
						__("Reject")
					);
				},
				__("Actions")
			);
			frm.change_custom_button_type(__("Reject"), __("Actions"), "danger");
		}
	},
});
