// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Voucher Card", {
	refresh(frm) {
		// Double-ensure pin_hmac is never visible (defense-in-depth with JSON hidden:1)
		frm.set_df_property("pin_hmac", "hidden", 1);

		// Make status read-only in form (status changes happen via code, not manual edits)
		if (!frm.is_new()) {
			frm.set_df_property("status", "read_only", 1);
		}

		// Void Card button: only on Available or Allocated cards
		if (["Available", "Allocated"].includes(frm.doc.status) && !frm.is_new()) {
			frm.add_custom_button(
				__("Void Card"),
				function () {
					frappe.prompt(
						[
							{
								fieldname: "void_reason",
								fieldtype: "Small Text",
								label: __("Void Reason"),
								reqd: 1,
								description: __(
									"This will permanently void this card. This cannot be undone."
								),
							},
						],
						function (values) {
							frappe.call({
								method: "memora_admin.memora_admin.api.voucher.void_card",
								args: {
									card_name: frm.doc.name,
									void_reason: values.void_reason,
								},
								callback: function (r) {
									if (r.message) {
										frappe.show_alert({
											message: __("Card voided."),
											indicator: "orange",
										});
										frm.reload_doc();
									}
								},
							});
						},
						__("Void Card"),
						__("Void")
					);
				}
			);
			// Make the Void Card button red
			frm.change_custom_button_type(__("Void Card"), null, "danger");
		}
	},
});
