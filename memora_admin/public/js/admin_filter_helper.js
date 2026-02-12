/**
 * MemoraAdminFilter — shared helper for content forms.
 *
 * Usage in any content DocType JS:
 *   MemoraAdminFilter.setup(frm, function (filter_doc) { ... });
 *
 * filter_doc is the full Memora Admin Filter document, or null when cleared.
 */
window.MemoraAdminFilter = (function () {
	const STORAGE_KEY = "memora_admin_filter";

	function _load_storage() {
		try {
			return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
		} catch {
			return {};
		}
	}

	function _save_storage(doctype, filter_name) {
		let data = _load_storage();
		if (filter_name) {
			data[doctype] = filter_name;
		} else {
			delete data[doctype];
		}
		localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
	}

	function _fetch_and_callback(filter_name, callback) {
		if (!filter_name) {
			callback(null);
			return;
		}
		frappe.db
			.get_doc("Memora Admin Filter", filter_name)
			.then((doc) => callback(doc))
			.catch(() => {
				// Filter was deleted or doesn't exist
				callback(null);
			});
	}

	function setup(frm, on_filter_change) {
		// Guard: skip if already set up
		if (frm._admin_filter_control) return;

		let wrapper = frm.fields_dict.admin_filter_html?.$wrapper;
		if (!wrapper) return;
		wrapper.empty();

		let control = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				fieldname: "_admin_filter",
				label: __("Admin Filter"),
				options: "Memora Admin Filter",
				description: __("Select a filter preset to narrow content dropdowns"),
				change() {
					let val = control.get_value();
					_save_storage(frm.doctype, val);
					_fetch_and_callback(val, on_filter_change);
				},
			},
			parent: wrapper,
			render_input: true,
		});

		frm._admin_filter_control = control;

		// Restore from localStorage
		let saved = _load_storage()[frm.doctype];
		if (saved) {
			control.set_value(saved);
			_fetch_and_callback(saved, on_filter_change);
		}
	}

	return { setup };
})();
