// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

const KEY_TYPE_OPTIONS = {
	"Memora Subject": "normal content\npractice",
	"Memora Academic Plan": "exam",
};

// Set filtered options on a rendered Select control.
// field.refresh() only updates visibility — it does NOT rebuild <option> elements.
// set_options() reads df.options and rebuilds the dropdown if the options changed.
function _apply_select_options(field, options) {
	if (!field) return;
	field.df.options = options;
	if (typeof field.set_options === "function") {
		field.set_options();
	}
}

function _sync_key_type(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const options = KEY_TYPE_OPTIONS[row.target_doctype];

	if (!options) {
		// Memora Track: key_type is hidden via depends_on — nothing to do
		return;
	}

	const grid = frm.fields_dict["grant_components"]?.grid;
	const grid_row = grid?.get_row(cdn);

	if (grid_row) {
		// Expanded row: grid.open_grid_row is the GridRowForm set in grid_row_form.js
		_apply_select_options(grid_row.grid_form?.fields_dict?.["key_type"], options);

		// Inline list-view: on_grid_fields_dict is populated after first inline edit
		_apply_select_options(grid_row.on_grid_fields_dict?.["key_type"], options);
	}

	// Auto-correct stored value if it is no longer valid for this target
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
