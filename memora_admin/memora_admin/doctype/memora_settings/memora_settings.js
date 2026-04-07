// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Settings", {
	refresh(frm) {
		if (frm.doc.cdn_enabled) {
			frm.add_custom_button("Purge CDN Cache", () => {
				frappe.call({
					method: "memora_admin.memora_admin.doctype.memora_settings.memora_settings.purge_all_cdn_cache",
					freeze: true,
					freeze_message: "Purging CDN cache...",
				});
			});
		}

		if (!frm.doc.vapid_public_key) {
			frm.add_custom_button("Generate VAPID Keys", () => {
				frappe.call({
					method: "memora_admin.memora_admin.doctype.memora_settings.memora_settings.generate_vapid_keys",
					freeze: true,
					freeze_message: "Generating VAPID keys...",
					callback: () => frm.reload_doc(),
				});
			});
		}
	},
});
