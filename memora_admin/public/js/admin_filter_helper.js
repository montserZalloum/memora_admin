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

	/* ── Picker configuration ── */
	const PICKER_CONFIG = {
		"Memora Track": { levels: ["subject"], target_field: "subject" },
		"Memora Unit": { levels: ["subject", "track"], target_field: "track" },
		"Memora Topic": { levels: ["subject", "track", "unit"], target_field: "unit" },
		"Memora Lesson": { levels: ["subject", "track", "unit", "topic"], target_field: "topic" },
	};

	const LEVEL_DEFS = {
		subject: { label: "Subject", color: "#2490ef" },
		track: { label: "Track", color: "#ed8e1b" },
		unit: { label: "Unit", color: "#29cd42" },
		topic: { label: "Topic", color: "#7c5de4" },
	};

	const PARENT_FIELD = {
		track: "subject",
		unit: "track",
		topic: "unit",
	};

	let _css_injected = false;

	/* ── Original helpers (unchanged) ── */

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
				callback(null);
			});
	}

	/* ── CSS injection ── */

	function _inject_css() {
		if (_css_injected) return;
		_css_injected = true;

		const css = `
			.memora-picker-container {
				margin-top: 10px;
				padding: 10px 0;
			}
			.memora-picker-level {
				display: flex;
				align-items: flex-start;
				gap: 8px;
				margin-bottom: 8px;
				flex-wrap: wrap;
			}
			.memora-level-label {
				display: inline-block;
				padding: 5px 14px;
				border-radius: 16px;
				font-size: 12px;
				color: #fff;
				font-weight: 600;
				white-space: nowrap;
				line-height: 1.4;
				flex-shrink: 0;
			}
			.memora-picker-buttons {
				display: flex;
				flex-wrap: wrap;
				gap: 6px;
				align-items: center;
			}
			.memora-picker-btn {
				display: inline-block;
				padding: 5px 14px;
				border-radius: 16px;
				font-size: 12px;
				font-weight: 500;
				cursor: pointer;
				border: 1.5px solid;
				background: #fff;
				transition: all 0.15s ease;
				line-height: 1.4;
			}
			.memora-picker-btn:hover {
				opacity: 0.85;
				transform: translateY(-1px);
			}
			.memora-picker-btn.active {
				color: #fff !important;
			}
			.memora-picker-empty {
				font-style: italic;
				color: #999;
				font-size: 12px;
				padding: 5px 0;
			}
			.memora-picker-loading {
				padding: 5px 0;
			}
			.memora-picker-breadcrumb {
				margin-top: 6px;
				padding: 6px 12px;
				background: var(--bg-light-gray, #f5f5f5);
				border-radius: 8px;
				font-size: 12px;
				color: #666;
				font-weight: 500;
			}
		`;

		const style = document.createElement("style");
		style.textContent = css;
		document.head.appendChild(style);
	}

	/* ── Picker state management ── */

	function _init_picker(frm, filter_doc) {
		const config = PICKER_CONFIG[frm.doctype];
		if (!config) {
			_destroy_picker(frm);
			return;
		}

		if (!filter_doc || !filter_doc.academic_plan) {
			_destroy_picker(frm);
			return;
		}

		_inject_css();

		// Initialize state
		const gen = (frm._picker_state?.generation || 0) + 1;
		frm._picker_state = {
			filter_doc: filter_doc,
			selections: {},
			selection_titles: {},
			items_cache: {},
			generation: gen,
			config: config,
		};

		// Ensure container exists
		let wrapper = frm.fields_dict.admin_filter_html?.$wrapper;
		if (!wrapper) return;

		let container = wrapper.find(".memora-picker-container");
		if (!container.length) {
			container = $('<div class="memora-picker-container"></div>');
			wrapper.append(container);
		}
		container.empty();

		// Pre-select from filter doc, then load first needed level
		_preselect_from_filter(frm, gen);
	}

	function _preselect_from_filter(frm, gen) {
		const state = frm._picker_state;
		if (!state || state.generation !== gen) return;

		const config = state.config;
		const filter_doc = state.filter_doc;

		// Walk levels and pre-select from filter doc
		for (let i = 0; i < config.levels.length; i++) {
			const level = config.levels[i];
			const val = filter_doc[level];
			if (val) {
				state.selections[level] = val;
				// We'll get titles from the fetched items
			} else {
				break;
			}
		}

		// Load the first level (subject always needs plan, not parent_value)
		_fetch_level_items(frm, config.levels[0], gen);
	}

	function _fetch_level_items(frm, level, gen) {
		const state = frm._picker_state;
		if (!state || state.generation !== gen) return;

		const config = state.config;
		const filter_doc = state.filter_doc;

		// Build cache key
		const parent_level = PARENT_FIELD[level];
		const parent_value = parent_level ? (state.selections[parent_level] || "") : "";
		const cache_key = level + ":" + parent_value;

		// Check cache
		if (state.items_cache[cache_key]) {
			_render_level(frm, level, state.items_cache[cache_key], gen);
			return;
		}

		// Show loading
		_render_level_loading(frm, level);

		// Build call args
		const args = { level: level };
		if (parent_value) {
			args.parent_value = parent_value;
		}
		if (filter_doc.academic_plan) {
			args.plan = filter_doc.academic_plan;
		}

		frappe.call({
			method: "memora_admin.memora_admin.doctype.memora_admin_filter.memora_admin_filter.get_picker_items",
			args: args,
			async: true,
			callback: function (r) {
				if (!frm._picker_state || frm._picker_state.generation !== gen) return;
				const items = r.message || [];
				state.items_cache[cache_key] = items;
				_render_level(frm, level, items, gen);
			},
		});
	}

	function _render_level_loading(frm, level) {
		const def = LEVEL_DEFS[level];
		const wrapper = frm.fields_dict.admin_filter_html?.$wrapper;
		if (!wrapper) return;
		const container = wrapper.find(".memora-picker-container");

		// Remove existing row for this level and all downstream
		const config = frm._picker_state.config;
		const level_idx = config.levels.indexOf(level);
		for (let i = level_idx; i < config.levels.length; i++) {
			container.find(`.memora-picker-level[data-level="${config.levels[i]}"]`).remove();
		}
		container.find(".memora-picker-breadcrumb").remove();

		const row = $(`
			<div class="memora-picker-level" data-level="${level}">
				<span class="memora-level-label" style="background:${def.color}">${__(def.label)}</span>
				<div class="memora-picker-loading">
					<span class="spinner-border spinner-border-sm text-muted" role="status"></span>
				</div>
			</div>
		`);
		container.append(row);
	}

	function _render_level(frm, level, items, gen) {
		const state = frm._picker_state;
		if (!state || state.generation !== gen) return;

		const def = LEVEL_DEFS[level];
		const config = state.config;
		const wrapper = frm.fields_dict.admin_filter_html?.$wrapper;
		if (!wrapper) return;
		const container = wrapper.find(".memora-picker-container");

		// Remove existing row for this level and downstream
		const level_idx = config.levels.indexOf(level);
		for (let i = level_idx; i < config.levels.length; i++) {
			container.find(`.memora-picker-level[data-level="${config.levels[i]}"]`).remove();
		}
		container.find(".memora-picker-breadcrumb").remove();

		const row = $(`
			<div class="memora-picker-level" data-level="${level}">
				<span class="memora-level-label" style="background:${def.color}">${__(def.label)}</span>
				<div class="memora-picker-buttons"></div>
			</div>
		`);

		const buttons_div = row.find(".memora-picker-buttons");

		if (!items.length) {
			buttons_div.append(`<span class="memora-picker-empty">${__("No items found")}</span>`);
		} else {
			items.forEach(function (item) {
				const is_active = state.selections[level] === item.name;
				const btn = $(`<button class="memora-picker-btn${is_active ? " active" : ""}"
					data-name="${frappe.utils.escape_html(item.name)}"
					style="border-color:${def.color};color:${def.color};${is_active ? "background:" + def.color : ""}"
				>${frappe.utils.escape_html(item.title || item.name)}</button>`);

				btn.on("click", function () {
					_on_button_click(frm, level, item.name, item.title || item.name, gen);
				});

				buttons_div.append(btn);

				// Capture title for pre-selected items
				if (is_active) {
					state.selection_titles[level] = item.title || item.name;
				}
			});
		}

		container.append(row);

		// If this level has a pre-selection, load the next level
		if (state.selections[level]) {
			const next_idx = level_idx + 1;
			if (next_idx < config.levels.length) {
				_fetch_level_items(frm, config.levels[next_idx], gen);
			} else {
				// All levels rendered, show breadcrumb
				_render_breadcrumb(frm);
			}
		} else {
			_render_breadcrumb(frm);
		}
	}

	function _on_button_click(frm, level, item_name, item_title, gen) {
		const state = frm._picker_state;
		if (!state || state.generation !== gen) return;

		const config = state.config;
		const level_idx = config.levels.indexOf(level);

		// Set selection
		state.selections[level] = item_name;
		state.selection_titles[level] = item_title;

		// Clear downstream selections
		for (let i = level_idx + 1; i < config.levels.length; i++) {
			delete state.selections[config.levels[i]];
			delete state.selection_titles[config.levels[i]];
		}

		// Update button styles for this level
		const wrapper = frm.fields_dict.admin_filter_html?.$wrapper;
		if (!wrapper) return;
		const container = wrapper.find(".memora-picker-container");
		const level_row = container.find(`.memora-picker-level[data-level="${level}"]`);
		const def = LEVEL_DEFS[level];

		level_row.find(".memora-picker-btn").each(function () {
			const $btn = $(this);
			if ($btn.data("name") === item_name) {
				$btn.addClass("active").css("background", def.color);
			} else {
				$btn.removeClass("active").css("background", "#fff");
			}
		});

		// Remove downstream level rows
		for (let i = level_idx + 1; i < config.levels.length; i++) {
			container.find(`.memora-picker-level[data-level="${config.levels[i]}"]`).remove();
		}
		container.find(".memora-picker-breadcrumb").remove();

		// If this is the target level, set the form field
		if (level === config.target_field || level_idx === config.levels.length - 1) {
			frm.set_value(config.target_field, item_name);
			_render_breadcrumb(frm);
		} else {
			// Load next level
			_fetch_level_items(frm, config.levels[level_idx + 1], gen);
		}
	}

	function _render_breadcrumb(frm) {
		const state = frm._picker_state;
		if (!state) return;

		const config = state.config;
		const wrapper = frm.fields_dict.admin_filter_html?.$wrapper;
		if (!wrapper) return;
		const container = wrapper.find(".memora-picker-container");

		container.find(".memora-picker-breadcrumb").remove();

		const parts = [];
		for (let i = 0; i < config.levels.length; i++) {
			const level = config.levels[i];
			if (state.selection_titles[level]) {
				parts.push(state.selection_titles[level]);
			} else {
				break;
			}
		}

		if (parts.length > 0) {
			container.append(
				`<div class="memora-picker-breadcrumb">${parts.map(frappe.utils.escape_html).join(" &rsaquo; ")}</div>`
			);
		}
	}

	function _destroy_picker(frm) {
		const wrapper = frm.fields_dict.admin_filter_html?.$wrapper;
		if (wrapper) {
			wrapper.find(".memora-picker-container").remove();
		}
		frm._picker_state = null;
	}

	/* ── Public setup (enhanced) ── */

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
					_fetch_and_callback(val, function (filter_doc) {
						on_filter_change(filter_doc);
						_init_picker(frm, filter_doc);
					});
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
			_fetch_and_callback(saved, function (filter_doc) {
				on_filter_change(filter_doc);
				_init_picker(frm, filter_doc);
			});
		}
	}

	return { setup };
})();
