// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

const KEY_TYPE_OPTIONS = {
	"Memora Subject": "normal content\npractice",
	"Memora Academic Plan": "exam",
};

function _sync_key_type(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const options = KEY_TYPE_OPTIONS[row.target_doctype];

	if (!options) {
		// Memora Track: key_type is hidden via depends_on — nothing to do
		return;
	}

	// Update the rendered field instance inside the expanded grid row form
	const grid_row = frm.fields_dict["grant_components"]?.grid?.get_row(cdn);
	const key_type_field = grid_row?.grid_form?.fields_dict?.["key_type"];

	if (key_type_field) {
		key_type_field.df.options = options;
		key_type_field.refresh();
	}

	// Auto-correct value if it is no longer valid for the selected target
	const allowed = options.split("\n");
	if (!allowed.includes(row.key_type)) {
		frappe.model.set_value(cdt, cdn, "key_type", allowed[0]);
	}
}

frappe.ui.form.on("Memora Grant Component", {
	target_doctype(frm, cdt, cdn) {
		_sync_key_type(frm, cdt, cdn);
	},

	form_render(frm, cdt, cdn) {
		_sync_key_type(frm, cdt, cdn);
	},
});
