// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Player Profile", {
	onload: function(frm) {
		// Sync devices from Redis once per form open
		// onload fires once (not on reload_doc), preventing infinite loop
		if (!frm.is_new()) {
			sync_devices(frm);
		}
	},
	refresh: function(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Grant Access"), function() {
				show_grant_dialog(frm);
			}, __("Actions"));

			// Make device child table read-only (data comes from Redis sync only)
			frm.set_df_property("authorized_devices", "read_only", 1);

			// Add remove buttons to existing rows
			add_remove_buttons(frm);
		}
	},
});

function sync_devices(frm) {
	frappe.call({
		method: "memora_admin.api.devices.sync_devices_from_redis",
		args: {
			player_name: frm.doc.name,
		},
		callback: function(r) {
			// Populate child table client-side from API response
			// No reload_doc() — avoids infinite getdoc loop
			let devices = r.message || [];
			frm.doc.authorized_devices = [];
			devices.forEach(function(device, idx) {
				let child = frappe.model.add_child(frm.doc, "Memora Player Device", "authorized_devices");
				child.device_id = device.device_id || "";
				child.device_name = device.device_name || "";
				child.platform = device.platform || "Web";
				child.last_login = device.last_login || "";
				child.user_agent = device.user_agent || "";
				child.push_token = device.push_token || "";
			});
			frm.refresh_field("authorized_devices");
			// Clear dirty indicator (child table modification marks form unsaved)
			frm.doc.__unsaved = 0;
			frm.page.clear_indicator();
			add_remove_buttons(frm);
		},
		error: function() {
			frappe.msgprint({
				title: __("Device Sync Failed"),
				message: __("Could not fetch live device data. Redis may be unavailable."),
				indicator: "red",
			});
		},
	});
}

function add_remove_buttons(frm) {
	let grid = frm.fields_dict.authorized_devices.grid;

	// Remove existing buttons to prevent duplicates on re-render
	grid.wrapper.find(".btn-remove-device").remove();

	grid.grid_rows.forEach(function(grid_row) {
		if (!grid_row.doc || !grid_row.doc.device_id) {
			return;
		}

		let device_display = grid_row.doc.device_name || grid_row.doc.device_id;
		let device_id = grid_row.doc.device_id;

		let btn = $(
			'<button class="btn btn-xs btn-danger btn-remove-device" style="margin: 2px 4px;">'
		).text(__("Remove"));

		btn.on("click", function(e) {
			e.stopPropagation();
			frappe.confirm(
				__("Remove {0}? Player will be logged out immediately.", [device_display]),
				function() {
					frappe.call({
						method: "memora_admin.api.devices.remove_device",
						args: {
							player_name: frm.doc.name,
							device_id: device_id,
						},
						freeze: true,
						freeze_message: __("Removing device..."),
						callback: function(r) {
							if (r.message && r.message.success) {
								frappe.show_alert({
									message: __("Device removed successfully"),
									indicator: "green",
								});
								// Re-sync child table from Redis (device is now gone)
								sync_devices(frm);
							} else {
								frappe.show_alert({
									message: __("Failed to remove device"),
									indicator: "red",
								});
							}
						},
						error: function() {
							frappe.show_alert({
								message: __("Failed to remove device"),
								indicator: "red",
							});
						},
					});
				}
			);
		});

		// Append button to the row-index cell of the data row
		let row_index_cell = grid_row.wrapper.find(".rows .data-row .row-index");
		if (row_index_cell.length) {
			row_index_cell.append(btn);
		}
	});
}

function show_grant_dialog(frm) {
	// Get default expiration from season
	if (frm.doc.season) {
		frappe.db.get_value("Memora Season", frm.doc.season, "end_date")
			.then(r => {
				let default_expires = null;
				if (r.message && r.message.end_date) {
					default_expires = r.message.end_date;
				}
				open_dialog(frm, default_expires);
			});
	} else {
		open_dialog(frm, null);
	}
}

function open_dialog(frm, default_expires) {
	let dialog = new frappe.ui.Dialog({
		title: __("Grant Content Access"),
		fields: [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: `<p>${__("Grant access to content for player")} <strong>${frm.doc.display_name || frm.doc.name}</strong></p>
				          <p class="text-muted">${__("Access key format: SUB-{subject} or TRK-{track}")}</p>`,
			},
			{
				fieldname: "access_key",
				fieldtype: "Data",
				label: __("Access Key"),
				reqd: 1,
				description: __("e.g., SUB-MATH, TRK-MATH-01"),
			},
			{
				fieldname: "expires_at",
				fieldtype: "Date",
				label: __("Expires At"),
				reqd: 1,
				default: default_expires,
				description: __("Access expiration date (defaults to season end)"),
			},
		],
		primary_action_label: __("Grant Access"),
		primary_action: function(values) {
			create_subscription(frm, values, dialog);
		},
	});

	dialog.show();
}

function create_subscription(frm, values, dialog) {
	frappe.call({
		method: "frappe.client.insert",
		args: {
			doc: {
				doctype: "Memora Player Subscription",
				player: frm.doc.name,
				access_key: values.access_key,
				expires_at: values.expires_at,
				is_active: 1,
			},
		},
		freeze: true,
		freeze_message: __("Granting access..."),
		callback: function(r) {
			if (r.message) {
				dialog.hide();
				frappe.show_alert({
					message: __("Access granted: {0}", [r.message.name]),
					indicator: "green",
				});
				// Refresh to show updated state
				frm.reload_doc();
			}
		},
		error: function(r) {
			// Handle duplicate subscription error gracefully
			if (r.exc_type === "DuplicateEntryError" ||
				(r._server_messages && r._server_messages.includes("already exists"))) {
				frappe.show_alert({
					message: __("Player already has this access"),
					indicator: "orange",
				});
			} else {
				frappe.show_alert({
					message: __("Failed to grant access"),
					indicator: "red",
				});
			}
		},
	});
}
