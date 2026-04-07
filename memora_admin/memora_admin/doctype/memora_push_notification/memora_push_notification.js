// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Push Notification", {
	refresh(frm) {
		toggle_fields(frm);
		render_push_preview(frm);

		// Add Send button for draft notifications
		if (!frm.is_new() && frm.doc.status === "Draft") {
			frm.add_custom_button(__("Send Now"), () => {
				frappe.confirm(
					__("Are you sure you want to send this push notification? This cannot be undone."),
					() => {
						frm.call("send").then(() => {
							frm.reload_doc();
						});
					}
				);
			}, null).addClass("btn-primary");
		}

		// Lock form after sending
		if (frm.doc.status === "Sent") {
			frm.disable_form();
		}

		// Auto-reload when background job writes delivery stats
		if (!frm._push_delivery_bound) {
			frm._push_delivery_bound = true;
			frappe.realtime.on("push_delivery_complete", (data) => {
				if (cur_frm && cur_frm.doc.name === data.name) {
					cur_frm.reload_doc();
				}
			});
		}
	},

	target_audience(frm) {
		toggle_fields(frm);
	},

	title(frm) {
		render_push_preview(frm);
	},

	body(frm) {
		render_push_preview(frm);
	},
});

function toggle_fields(frm) {
	frm.toggle_display("target_plans", frm.doc.target_audience === "Specific Plans");
	frm.toggle_reqd("target_plans", frm.doc.target_audience === "Specific Plans");
}

function render_push_preview(frm) {
	const title = frm.doc.title || "Title";
	const body = frm.doc.body || "Body";
	const body_truncated = body.length > 100 ? body.substring(0, 100) + "\u2026" : body;
	const now = frappe.datetime.now_datetime().split(" ");
	const time = now[1] ? now[1].substring(0, 5) : "12:00";

	const html = `
		<div style="
			max-width: 380px;
			margin: 0 auto;
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
			direction: rtl;
		">
			<div style="
				background: #fff;
				border-radius: 16px;
				padding: 14px 16px;
				box-shadow: 0 2px 12px rgba(0,0,0,0.12);
				border: 1px solid var(--border-color);
			">
				<div style="
					display: flex;
					align-items: center;
					gap: 8px;
					margin-bottom: 8px;
					font-size: 12px;
					color: #8e8e93;
				">
					<img src="/assets/memora_admin/images/memora-logo.png"
						style="width: 20px; height: 20px; border-radius: 5px; object-fit: cover;"
						alt="Memora"
					/>
					<span>Memora</span>
					<span style="margin-right: auto; margin-left: 0;">${time}</span>
				</div>
				<div style="
					font-size: 15px;
					font-weight: 600;
					color: var(--text-color);
					margin-bottom: 4px;
					line-height: 1.3;
				">${frappe.utils.escape_html(title)}</div>
				<div style="
					font-size: 13px;
					color: var(--text-muted);
					line-height: 1.4;
				">${frappe.utils.escape_html(body_truncated)}</div>
			</div>
		</div>
	`;

	frm.fields_dict.push_preview.$wrapper.html(html);
}
