// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Player Profile", {
    refresh: function(frm) {
        // Only show button for saved documents
        if (!frm.is_new()) {
            frm.add_custom_button(__("Grant Access"), function() {
                show_grant_dialog(frm);
            }, __("Actions"));
        }
    }
});

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
                          <p class="text-muted">${__("Access key format: SUB-{subject} or TRK-{track}")}</p>`
            },
            {
                fieldname: "access_key",
                fieldtype: "Data",
                label: __("Access Key"),
                reqd: 1,
                description: __("e.g., SUB-MATH, TRK-MATH-01")
            },
            {
                fieldname: "expires_at",
                fieldtype: "Date",
                label: __("Expires At"),
                reqd: 1,
                default: default_expires,
                description: __("Access expiration date (defaults to season end)")
            }
        ],
        primary_action_label: __("Grant Access"),
        primary_action: function(values) {
            create_subscription(frm, values, dialog);
        }
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
                is_active: 1
            }
        },
        freeze: true,
        freeze_message: __("Granting access..."),
        callback: function(r) {
            if (r.message) {
                dialog.hide();
                frappe.show_alert({
                    message: __("Access granted: {0}", [r.message.name]),
                    indicator: "green"
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
                    indicator: "orange"
                });
            } else {
                frappe.show_alert({
                    message: __("Failed to grant access"),
                    indicator: "red"
                });
            }
        }
    });
}
