// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

// Cache templates so we don't re-fetch every refresh
let _templates_cache = null;

function _get_templates(callback) {
	if (_templates_cache) {
		callback(_templates_cache);
		return;
	}
	frappe.call({
		method: "memora_admin.memora_admin.api.runbook.get_templates",
		callback: function (r) {
			_templates_cache = r.message || [];
			callback(_templates_cache);
		},
	});
}

frappe.ui.form.on("Memora Runbook", {
	setup(frm) {},

	refresh(frm) {
		// Always populate workflow options (DOM is rebuilt on SPA navigation)
		_get_templates(function (templates) {
			let options = templates.map((t) => t.workflow_id);
			frm.set_df_property("workflow_id", "options", [""].concat(options));
		});

		if (frm.is_new()) {
			return;
		}

		// ── Resume wizard if returning from a form navigation ──
		let ws_key = "memora_wizard_" + frm.doc.name;
		let ws_json = sessionStorage.getItem(ws_key);
		if (ws_json) {
			sessionStorage.removeItem(ws_key);
			// Small delay so the form finishes rendering before the dialog opens
			setTimeout(function () {
				_wizard_resume(frm, JSON.parse(ws_json));
			}, 300);
		}

		// Lock workflow_id after creation, keep context editable until set
		frm.set_df_property("workflow_id", "read_only", 1);
		if (!frm.doc.context_doctype) {
			frm.set_df_property("context_name", "hidden", 1);
		} else if (frm.doc.context_name) {
			frm.set_df_property("context_name", "read_only", 1);
		}

		// ── Start Wizard button ──
		if (["Not Started", "In Progress"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Start Wizard"),
				function () {
					_launch_wizard(frm);
				},
				__("Actions")
			);
			frm.change_custom_button_type(__("Start Wizard"), __("Actions"), "primary");
		}

		// Validate All Steps button
		if (["Not Started", "In Progress"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Validate All Steps"),
				function () {
					frappe.call({
						method: "memora_admin.memora_admin.api.runbook.validate_steps",
						args: { runbook_name: frm.doc.name },
						callback: function (r) {
							if (r.message) {
								let done = r.message.summary.done;
								let total = r.message.summary.total;
								frappe.show_alert({
									message: __("{0} of {1} steps passed", [done, total]),
									indicator: done === total ? "green" : "blue",
								});
								frm.reload_doc();
							}
						},
					});
				},
				__("Actions")
			);
		}

		// Per-step action buttons (only for active runbooks)
		if (["Not Started", "In Progress"].includes(frm.doc.status)) {
			(frm.doc.steps || []).forEach(function (step) {
				if (step.status === "Pending") {
					frm.add_custom_button(
						__("Mark Done: {0}", [step.label]),
						function () {
							frappe.call({
								method: "memora_admin.memora_admin.api.runbook.complete_step",
								args: {
									runbook_name: frm.doc.name,
									step_key: step.step_key,
								},
								callback: function () {
									frm.reload_doc();
								},
							});
						},
						__("Steps")
					);
				}
			});
		}

		// Cancel button
		if (["Not Started", "In Progress"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Cancel Runbook"),
				function () {
					frappe.confirm(__("Cancel this runbook?"), function () {
						frappe.call({
							method: "memora_admin.memora_admin.api.runbook.cancel_runbook",
							args: { runbook_name: frm.doc.name },
							callback: function () {
								frm.reload_doc();
							},
						});
					});
				},
				__("Actions")
			);
			frm.change_custom_button_type(__("Cancel Runbook"), __("Actions"), "danger");
		}

		// Navigate to context document
		if (frm.doc.context_doctype && frm.doc.context_name) {
			frm.add_custom_button(__("Open {0}", [frm.doc.context_name]), function () {
				frappe.set_route("Form", frm.doc.context_doctype, frm.doc.context_name);
			});
		}

		// Color-code step rows and render action buttons in the grid
		_colorize_steps(frm);
		_render_action_buttons(frm);
	},

	workflow_id(frm) {
		if (!frm.doc.workflow_id) {
			frm.set_value("context_doctype", "");
			frm.set_value("context_name", "");
			frm.set_value("workflow_description", "");
			return;
		}
		// Auto-set context_doctype and description from template
		_get_templates(function (templates) {
			let tmpl = templates.find((t) => t.workflow_id === frm.doc.workflow_id);
			if (tmpl) {
				frm.set_value("context_doctype", tmpl.context_doctype || "");
				frm.set_value("context_name", "");
				frm.set_value("workflow_description", tmpl.description || "");
				frm.set_df_property("context_name", "hidden", !tmpl.context_doctype);
			}
		});
	},
});

function _colorize_steps(frm) {
	const color_map = {
		Done: "var(--green-100)",
		Skipped: "var(--yellow-100)",
		Blocked: "var(--red-100)",
		Pending: "",
	};
	frm.fields_dict.steps.grid.grid_rows.forEach(function (row) {
		let status = row.doc.status;
		let color = color_map[status] || "";
		row.row.css("background-color", color);
	});
}

function _render_action_buttons(frm) {
	frm.fields_dict.steps.grid.grid_rows.forEach(function (row) {
		let col = row.columns && row.columns["action_url"];
		if (!col) return;
		let url = row.doc.action_url;
		if (url && col.static_area) {
			col.static_area.html(
				`<a href="${url}" target="_blank" class="btn btn-xs btn-primary aaction-btn">Open</a>`
			);
			col.static_area.find(".aaction-btn").on("click", function (e) {
				e.stopPropagation();
			});
		}
	});
}

// ═══════════════════════════════════════════════════════════════════════════
// Wizard controller
// ═══════════════════════════════════════════════════════════════════════════

function _launch_wizard(frm) {
	frappe.call({
		method: "memora_admin.memora_admin.api.runbook.get_wizard_config",
		args: { workflow_id: frm.doc.workflow_id, runbook_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Loading wizard..."),
		callback: function (r) {
			if (!r.message) return;
			let config = r.message;
			// Track what the wizard created for the summary
			let results = [];
			_wizard_step(frm, config.steps, 0, results);
		},
	});
}

/**
 * Resume the wizard after returning from a real-form navigation.
 * Reads the saved state, sets context if needed, marks step done, then continues.
 */
function _wizard_resume(frm, state) {
	let results = state.results || [];
	let step_index = state.step_index;
	let step_key = state.step_key;
	let sets_context = state.sets_context;
	let last_saved = state.last_saved;

	if (!last_saved) {
		// Admin navigated away without saving — re-launch wizard from scratch
		_launch_wizard(frm);
		return;
	}

	function _continue() {
		frappe.call({
			method: "memora_admin.memora_admin.api.runbook.complete_step",
			args: { runbook_name: frm.doc.name, step_key: step_key },
			callback: function () {
				frappe.call({
					method: "memora_admin.memora_admin.api.runbook.get_wizard_config",
					args: { workflow_id: frm.doc.workflow_id, runbook_name: frm.doc.name },
					callback: function (r) {
						let fresh = r.message.steps;
						let action = sets_context ? "created" : "updated";
						let name = last_saved.title || last_saved.name;
						results.push({ label: fresh[step_index].label, action: action, name: name });
						_wizard_step(frm, fresh, step_index + 1, results);
					},
				});
			},
		});
	}

	if (sets_context) {
		frappe.call({
			method: "memora_admin.memora_admin.api.runbook.wizard_set_context",
			args: { runbook_name: frm.doc.name, context_name: last_saved.name },
			callback: function () {
				// Update local form data for subsequent steps
				frm.doc.context_name = last_saved.name;
				_continue();
			},
		});
	} else {
		_continue();
	}
}

/**
 * Show a wizard dialog that navigates to the real Frappe form.
 * Used for steps without wizard_fields — opens the native form so all
 * form JS (filters, validations, hide/show) runs unmodified.
 */
function _wizard_show_form_step(frm, steps, index, results, progress_html, step) {
	let is_create = step.create_doctype && !step.update_context;
	let doctype_label = (step.create_doctype || frm.doc.context_doctype || "").replace("Memora ", "");

	let fields = [
		{ fieldtype: "HTML", fieldname: "progress", options: progress_html },
		{
			fieldtype: "HTML",
			fieldname: "info",
			options: `
				<div class="wizard-step-status mb-3"
				     style="background: var(--blue-50); border-radius: 8px; padding: 12px 16px;">
					<strong>${step.description}</strong>
					${step.hint ? `<br><span class="text-muted" style="font-size: 12px;"><b>Hint:</b> ${step.hint}</span>` : ""}
				</div>
				<p class="text-muted" style="font-size: 13px;">
					The full document form will open so all validations and filters work correctly.
					<br>You will return to the wizard automatically after saving.
				</p>`,
		},
	];

	let btn_label = is_create
		? __("Open {0} Form \u2192", [doctype_label])
		: __("Open {0} \u2192", [doctype_label]);

	let d = new frappe.ui.Dialog({
		title: __("Step {0} of {1}: {2}", [index + 1, steps.length, step.label]),
		fields: fields,
		size: "large",
		primary_action_label: btn_label,
		primary_action: function () {
			d.hide();
			_wizard_navigate_to_form(frm, steps, index, results, step);
		},
	});

	if (step.optional) {
		d.set_secondary_action_label(__("Skip"));
		d.set_secondary_action(function () {
			d.hide();
			results.push({ label: step.label, action: "skipped", name: null });
			_mark_step(frm, step, "skip", function () {
				_wizard_step(frm, steps, index + 1, results);
			});
		});
	}

	d.show();
	d.$wrapper.on("hidden.bs.modal", function () {
		d.$wrapper.remove();
	});
}

/**
 * Save wizard state to sessionStorage, set a one-shot after_save redirect hook,
 * then navigate to the real Frappe form.
 */
function _wizard_navigate_to_form(frm, steps, index, results, step) {
	let ws_key = "memora_wizard_" + frm.doc.name;
	let state = {
		step_index: index,
		results: results,
		step_key: step.key,
		sets_context: step.sets_context || false,
	};
	sessionStorage.setItem(ws_key, JSON.stringify(state));

	// One-shot hook: after the admin saves the form, capture the doc and redirect back.
	// NOTE: route_hooks.after_save fires BEFORE frm.refresh(), so frm.doc.name may
	// still be the temporary "new-…" name. Use frappe.model.new_names to resolve it.
	let return_to = frm.doc.name;
	frappe.route_hooks.after_save = function (saved_frm) {
		let temp_name = saved_frm.doc.name;
		let real_name = frappe.model.new_names[temp_name] || temp_name;
		let title_field = saved_frm.meta.title_field;
		let title = (title_field && saved_frm.doc[title_field]) || real_name;

		let stored = JSON.parse(sessionStorage.getItem(ws_key) || "{}");
		stored.last_saved = {
			doctype: saved_frm.doc.doctype,
			name: real_name,
			title: title,
		};
		sessionStorage.setItem(ws_key, JSON.stringify(stored));
		frappe.set_route("Form", "Memora Runbook", return_to);
	};

	// Navigate to the real form
	if (step.create_doctype && !step.update_context) {
		frappe.new_doc(step.create_doctype);
	} else if (frm.doc.context_doctype && frm.doc.context_name) {
		frappe.set_route("Form", frm.doc.context_doctype, frm.doc.context_name);
	}
}

/**
 * Recursively show a wizard dialog for each step.
 * @param {Object} frm        - Current Frappe form
 * @param {Array}  steps       - Step configs from get_wizard_config
 * @param {Number} index       - Current step index
 * @param {Array}  results     - Accumulator of {label, action, name} for summary
 */
function _wizard_step(frm, steps, index, results) {
	if (index >= steps.length) {
		_wizard_complete(frm, results);
		return;
	}

	let step = steps[index];
	let progress_html = _build_progress_html(steps, index, results);

	// Auto-advance only pure check steps (no create/update action available)
	let is_check_only = !step.wizard_fields.length && !step.create_doctype && !step.update_context;
	if (step.passed && is_check_only) {
		results.push({ label: step.label, action: "auto", name: null });
		_mark_step(frm, step, "complete", function () {
			_wizard_step(frm, steps, index + 1, results);
		});
		return;
	}

	if (step.passed) {
		_wizard_show_passed(frm, steps, index, results, progress_html, step);
	} else {
		_wizard_show_create(frm, steps, index, results, progress_html, step);
	}
}

/**
 * Show dialog for a step that already passes its check.
 * Admin can skip forward or optionally create/update anyway.
 */
function _wizard_show_passed(frm, steps, index, results, progress_html, step) {
	let has_dialog_action = step.wizard_fields.length && (step.create_doctype || step.update_context);
	let has_form_action = !step.wizard_fields.length && (step.create_doctype || step.update_context);
	let has_any_action = has_dialog_action || has_form_action;
	let action_label = step.update_context ? "update it" : "create a new one";

	let fields = [
		{ fieldtype: "HTML", fieldname: "progress", options: progress_html },
		{
			fieldtype: "HTML",
			fieldname: "info",
			options: `
				<div class="wizard-step-status d-flex align-items-start mb-3"
				     style="background: var(--green-50); border-radius: 8px; padding: 12px 16px;">
					<span style="font-size: 20px; margin-right: 10px;">&#10004;</span>
					<div>
						<strong>${step.description}</strong><br>
						<span class="text-muted" style="font-size: 12px;">
							This step is already satisfied.${has_any_action ? " You can continue or " + action_label + "." : ""}
						</span>
					</div>
				</div>`,
		},
	];

	// Include wizard_fields so they can optionally act (dialog-based steps)
	if (has_dialog_action) {
		let target = step.create_doctype || "context";
		fields.push({
			fieldtype: "HTML",
			fieldname: "create_header",
			options: `<p class="text-muted" style="font-size: 12px; margin-bottom: 4px;">
				Or ${action_label} <strong>${target}</strong>:
			</p>`,
		});
		fields = fields.concat(step.wizard_fields);
	}

	let d = new frappe.ui.Dialog({
		title: __("Step {0} of {1}: {2}", [index + 1, steps.length, step.label]),
		fields: fields,
		size: "large",
		primary_action_label: __("Continue \u2192"),
		primary_action: function () {
			d.hide();
			results.push({ label: step.label, action: "skipped", name: null });
			_mark_step(frm, step, "complete", function () {
				_wizard_step(frm, steps, index + 1, results);
			});
		},
	});

	// "Act anyway" button — either opens real form or uses dialog fields
	if (has_form_action) {
		let doctype_label = (step.create_doctype || frm.doc.context_doctype || "").replace("Memora ", "");
		let is_create = step.create_doctype && !step.update_context;
		let btn_label = is_create
			? __("Create New {0} \u2192", [doctype_label])
			: __("Open {0} \u2192", [doctype_label]);
		d.$wrapper.find(".modal-footer").prepend(
			`<button class="btn btn-sm btn-success wizard-act-btn" style="margin-right: auto;">
				${btn_label}
			</button>`
		);
		d.$wrapper.find(".wizard-act-btn").on("click", function () {
			d.hide();
			_wizard_navigate_to_form(frm, steps, index, results, step);
		});
	} else if (has_dialog_action) {
		let btn_label = step.update_context
			? __("Update & Continue \u2192")
			: __("Create & Continue \u2192");
		d.$wrapper.find(".modal-footer").prepend(
			`<button class="btn btn-sm btn-success wizard-act-btn" style="margin-right: auto;">
				${btn_label}
			</button>`
		);
		d.$wrapper.find(".wizard-act-btn").on("click", function () {
			let values = d.get_values();
			if (!values) return;
			_strip_layout_fields(values);
			if (step.update_context) {
				_wizard_do_update(d, frm, steps, index, results, step, values);
			} else {
				_wizard_do_create(d, frm, steps, index, results, step, values);
			}
		});
	}

	if (step.optional) {
		d.set_secondary_action_label(__("Skip"));
		d.set_secondary_action(function () {
			d.hide();
			results.push({ label: step.label, action: "skipped", name: null });
			_mark_step(frm, step, "skip", function () {
				_wizard_step(frm, steps, index + 1, results);
			});
		});
	}

	d.show();
	d.$wrapper.on("hidden.bs.modal", function () {
		d.$wrapper.remove();
	});
}

/**
 * Show dialog for a step that needs the admin to act.
 * Handles both create (new doc) and update (modify context doc) steps.
 * For steps without wizard_fields, delegates to the real-form navigation flow.
 */
function _wizard_show_create(frm, steps, index, results, progress_html, step) {
	// If step needs form interaction but has no wizard_fields → open real form
	if ((step.create_doctype || step.update_context) && !step.wizard_fields.length) {
		_wizard_show_form_step(frm, steps, index, results, progress_html, step);
		return;
	}

	let fields = [
		{ fieldtype: "HTML", fieldname: "progress", options: progress_html },
		{
			fieldtype: "HTML",
			fieldname: "info",
			options: `
				<div class="wizard-step-status mb-3"
				     style="background: var(--blue-50); border-radius: 8px; padding: 12px 16px;">
					<strong>${step.description}</strong>
					${step.hint ? `<br><span class="text-muted" style="font-size: 12px;"><b>Hint:</b> ${step.hint}</span>` : ""}
				</div>`,
		},
	];

	if (step.wizard_fields.length) {
		fields = fields.concat(step.wizard_fields);
	}

	let has_action = step.wizard_fields.length && (step.create_doctype || step.update_context);
	let btn_label;
	if (step.update_context) {
		btn_label = __("Save & Continue \u2192");
	} else if (step.create_doctype) {
		btn_label = __("Create & Continue \u2192");
	} else {
		btn_label = __("Continue \u2192");
	}

	let d = new frappe.ui.Dialog({
		title: __("Step {0} of {1}: {2}", [index + 1, steps.length, step.label]),
		fields: fields,
		size: "large",
		primary_action_label: btn_label,
		primary_action: function () {
			if (!has_action) {
				d.hide();
				results.push({ label: step.label, action: "continued", name: null });
				_mark_step(frm, step, "complete", function () {
					_wizard_step(frm, steps, index + 1, results);
				});
				return;
			}
			let values = d.get_values();
			if (!values) return;
			_strip_layout_fields(values);
			if (step.update_context) {
				_wizard_do_update(d, frm, steps, index, results, step, values);
			} else {
				_wizard_do_create(d, frm, steps, index, results, step, values);
			}
		},
	});

	if (step.optional) {
		d.set_secondary_action_label(__("Skip"));
		d.set_secondary_action(function () {
			d.hide();
			results.push({ label: step.label, action: "skipped", name: null });
			_mark_step(frm, step, "skip", function () {
				_wizard_step(frm, steps, index + 1, results);
			});
		});
	}

	d.show();
	d.$wrapper.on("hidden.bs.modal", function () {
		d.$wrapper.remove();
	});
}

/**
 * Strip HTML/layout-only fields from dialog values.
 */
function _strip_layout_fields(values) {
	delete values.progress;
	delete values.info;
	delete values.create_header;
}

/**
 * Create a new document. If sets_context, update the runbook context and
 * re-fetch wizard config so subsequent checks use the new context.
 */
function _wizard_do_create(dialog, frm, steps, index, results, step, values) {
	frappe.call({
		method: "memora_admin.memora_admin.api.runbook.wizard_create_doc",
		args: { doctype: step.create_doctype, values: values },
		freeze: true,
		freeze_message: __("Creating {0}...", [step.create_doctype]),
		callback: function (r) {
			dialog.hide();
			let created = r.message;
			frappe.show_alert({
				message: __("Created {0}: {1}", [step.create_doctype, created.title || created.name]),
				indicator: "green",
			});
			results.push({ label: step.label, action: "created", name: created.title || created.name });

			if (step.sets_context) {
				// Set context on the runbook, then re-fetch config for remaining steps
				frappe.call({
					method: "memora_admin.memora_admin.api.runbook.wizard_set_context",
					args: { runbook_name: frm.doc.name, context_name: created.name },
					callback: function () {
						_mark_step(frm, step, "complete", function () {
							// Re-fetch config so subsequent checks run against the new context
							frappe.call({
								method: "memora_admin.memora_admin.api.runbook.get_wizard_config",
								args: {
									workflow_id: frm.doc.workflow_id,
									runbook_name: frm.doc.name,
								},
								callback: function (r2) {
									let fresh_steps = r2.message.steps;
									_wizard_step(frm, fresh_steps, index + 1, results);
								},
							});
						});
					},
				});
			} else {
				_mark_step(frm, step, "complete", function () {
					_wizard_step(frm, steps, index + 1, results);
				});
			}
		},
		error: function () {
			// Don't close dialog on error — let the admin fix and retry
		},
	});
}

/**
 * Update the runbook's context document with the wizard field values.
 */
function _wizard_do_update(dialog, frm, steps, index, results, step, values) {
	frappe.call({
		method: "memora_admin.memora_admin.api.runbook.wizard_update_doc",
		args: { runbook_name: frm.doc.name, values: values },
		freeze: true,
		freeze_message: __("Saving..."),
		callback: function (r) {
			dialog.hide();
			let updated = r.message;
			frappe.show_alert({
				message: __("Updated {0}", [updated.title || updated.name]),
				indicator: "green",
			});
			results.push({ label: step.label, action: "updated", name: updated.title || updated.name });
			_mark_step(frm, step, "complete", function () {
				_wizard_step(frm, steps, index + 1, results);
			});
		},
		error: function () {
			// Don't close dialog on error — let the admin fix and retry
		},
	});
}

/**
 * Mark a runbook step as done or skipped via the API.
 */
function _mark_step(frm, step, action, callback) {
	let method =
		action === "skip"
			? "memora_admin.memora_admin.api.runbook.skip_step"
			: "memora_admin.memora_admin.api.runbook.complete_step";

	frappe.call({
		method: method,
		args: { runbook_name: frm.doc.name, step_key: step.key },
		callback: function () {
			if (callback) callback();
		},
	});
}

/**
 * Final summary dialog when all wizard steps are done.
 */
function _wizard_complete(frm, results) {
	let rows = results
		.map(function (r) {
			let icon, color;
			if (r.action === "created" || r.action === "updated") {
				icon = "&#10004;";
				color = "var(--green-600)";
			} else if (r.action === "skipped") {
				icon = "&#10140;";
				color = "var(--yellow-600)";
			} else {
				icon = "&#10004;";
				color = "var(--blue-600)";
			}
			let detail = r.name ? ` &mdash; <strong>${r.name}</strong>` : "";
			return `<tr>
				<td style="color: ${color}; font-size: 16px; padding: 6px 10px;">${icon}</td>
				<td style="padding: 6px 10px;">${r.label}${detail}</td>
				<td style="padding: 6px 10px; color: var(--text-muted); font-size: 12px;">${r.action}</td>
			</tr>`;
		})
		.join("");

	let d = new frappe.ui.Dialog({
		title: __("Wizard Complete"),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "summary",
				options: `
					<div style="text-align: center; margin-bottom: 16px;">
						<span style="font-size: 48px;">&#127881;</span>
						<h4>${__("All steps done!")}</h4>
					</div>
					<table class="table table-sm" style="margin-bottom: 0;">
						<tbody>${rows}</tbody>
					</table>`,
			},
		],
		size: "small",
		primary_action_label: __("Done"),
		primary_action: function () {
			d.hide();
			frm.reload_doc();
		},
	});
	d.show();
	d.$wrapper.on("hidden.bs.modal", function () {
		d.$wrapper.remove();
		frm.reload_doc();
	});
}

/**
 * Build the progress stepper HTML shown at the top of every wizard dialog.
 */
function _build_progress_html(steps, current_index, results) {
	let items = steps
		.map(function (step, i) {
			let status, bg, fg, border;
			if (i < current_index) {
				let r = results.find((x) => x.label === step.label);
				if (r && r.action === "skipped") {
					status = "skipped";
					bg = "var(--yellow-100)";
					fg = "var(--yellow-700)";
					border = "var(--yellow-300)";
				} else {
					status = "done";
					bg = "var(--green-100)";
					fg = "var(--green-700)";
					border = "var(--green-300)";
				}
			} else if (i === current_index) {
				status = "active";
				bg = "var(--blue-500)";
				fg = "#fff";
				border = "var(--blue-500)";
			} else {
				status = "upcoming";
				bg = "var(--gray-100)";
				fg = "var(--gray-500)";
				border = "var(--gray-300)";
			}

			let icon;
			if (status === "done") icon = "&#10004;";
			else if (status === "skipped") icon = "&#8211;";
			else icon = i + 1;

			return `
				<div class="d-flex flex-column align-items-center" style="flex: 1; min-width: 0;">
					<div style="width: 28px; height: 28px; border-radius: 50%;
						background: ${bg}; color: ${fg}; border: 2px solid ${border};
						display: flex; align-items: center; justify-content: center;
						font-size: 13px; font-weight: 600;">${icon}</div>
					<span style="font-size: 11px; margin-top: 4px; color: ${fg};
						white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
						max-width: 100%; text-align: center;">${step.label}</span>
				</div>`;
		})
		.join("");

	// Connecting line
	let pct = steps.length > 1 ? Math.round((current_index / (steps.length - 1)) * 100) : 100;

	return `
		<div class="wizard-progress-bar mb-4">
			<div class="d-flex align-items-start" style="position: relative;">
				${items}
			</div>
			<div style="height: 3px; background: var(--gray-200); border-radius: 2px;
				margin: -18px 32px 16px 32px; position: relative; z-index: 0;">
				<div style="height: 100%; width: ${pct}%; background: var(--blue-500);
					border-radius: 2px; transition: width 0.3s;"></div>
			</div>
		</div>`;
}
