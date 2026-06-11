// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Topic", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Reorder Lessons"), () => open_lesson_reorder_dialog(frm));
		}

		MemoraAdminFilter.setup(frm, function (filter_doc) {
			if (filter_doc && filter_doc.unit) {
				frm.set_query("unit", () => ({
					filters: { name: filter_doc.unit },
				}));
			} else if (filter_doc && filter_doc.track) {
				frm.set_query("unit", () => ({
					filters: { track: filter_doc.track },
				}));
			} else if (filter_doc && filter_doc.subject) {
				frm.set_query("unit", () => ({
					filters: { subject: filter_doc.subject },
				}));
			} else {
				frm.set_query("unit", () => ({}));
			}
			frm.refresh_field("unit");
		});
	},

	unit(frm) {
		if (frm.doc.unit) {
			frappe.db.get_value("Memora Unit", frm.doc.unit, ["track", "subject"], (r) => {
				if (r) {
					frm.set_value("track", r.track);
					frm.set_value("subject", r.subject);
				}
			});
		} else {
			frm.set_value("track", null);
			frm.set_value("subject", null);
		}
	},
});

function open_lesson_reorder_dialog(frm) {
	frappe.call({
		method: "memora_admin.memora_admin.doctype.memora_topic.memora_topic.get_topic_lessons",
		args: { topic: frm.doc.name },
		freeze: true,
		freeze_message: __("Loading lessons..."),
		callback(r) {
			const lessons = r.message || [];
			if (!lessons.length) {
				frappe.msgprint(__("This topic has no lessons yet."));
				return;
			}
			render_reorder_dialog(frm, lessons);
		},
	});
}

function render_reorder_dialog(frm, lessons) {
	const dialog = new frappe.ui.Dialog({
		title: __("Reorder Lessons — {0}", [frm.doc.topic_title || frm.doc.name]),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "lessons_html" }],
		primary_action_label: __("Save Order"),
		primary_action() {
			save_order(frm, dialog);
		},
	});

	// Original order used to detect unsaved changes.
	dialog._original_order = lessons.map((l) => l.name).join(",");

	const wrapper = dialog.fields_dict.lessons_html.$wrapper;
	wrapper.html(build_reorder_html(lessons));

	const $alert = wrapper.find(".reorder-unsaved-alert");
	const $list = wrapper.find(".reorder-list");

	// "Add New Lesson" — open a new lesson pre-filled with this topic's hierarchy.
	wrapper.find(".reorder-add-lesson").on("click", () => add_new_lesson(frm, dialog));

	const refresh_alert = () => {
		const current = $list
			.children(".reorder-item")
			.map((i, el) => $(el).attr("data-lesson"))
			.get()
			.join(",");
		const changed = current !== dialog._original_order;
		$alert.toggleClass("hide", !changed);
		dialog._dirty = changed;
	};

	// "Open lesson" buttons — open in a new tab so the dialog/order is preserved.
	$list.on("click", ".reorder-open", function (e) {
		e.preventDefault();
		const name = $(this).closest(".reorder-item").attr("data-lesson");
		window.open(frappe.utils.get_form_link("Memora Lesson", name), "_blank");
	});

	new Sortable($list.get(0), {
		handle: ".reorder-handle",
		animation: 150,
		ghostClass: "reorder-ghost",
		onSort: refresh_alert,
	});

	dialog.show();
}

function build_reorder_html(lessons) {
	const rows = lessons
		.map((l) => {
			const published = l.is_published
				? ""
				: ` <span class="text-muted">(${frappe.utils.escape_html(__("Unpublished"))})</span>`;
			const title = frappe.utils.escape_html(l.lesson_title || l.name);
			return `
			<div class="reorder-item" data-lesson="${frappe.utils.escape_html(l.name)}">
				<span class="reorder-handle" title="${__("Drag to reorder")}">&#x2630;</span>
				<span class="reorder-title">${title}${published}</span>
				<button type="button" class="btn btn-xs btn-default reorder-open">${__("Open")}</button>
			</div>`;
		})
		.join("");

	return `
		<div class="reorder-toolbar">
			<button type="button" class="btn btn-xs btn-primary reorder-add-lesson">
				${frappe.utils.icon("add", "xs")} ${__("Add New Lesson")}
			</button>
		</div>
		<div class="reorder-unsaved-alert alert alert-warning hide" style="margin-bottom: 12px;">
			${__("You changed the order. Click \"Save Order\" to save your changes.")}
		</div>
		<div class="reorder-list">${rows}</div>
		<style>
			.reorder-toolbar { display: flex; justify-content: flex-end; margin-bottom: 10px; }
			.reorder-list { display: flex; flex-direction: column; gap: 6px; }
			.reorder-item {
				display: flex; align-items: center; gap: 10px;
				padding: 8px 10px; border: 1px solid var(--border-color);
				border-radius: var(--border-radius); background: var(--card-bg);
			}
			.reorder-handle { cursor: grab; color: var(--text-muted); display: flex; }
			.reorder-title { flex: 1; }
			.reorder-ghost { opacity: 0.4; }
		</style>`;
}

function add_new_lesson(frm, dialog) {
	// Pre-fill the hierarchy so the admin only types the lesson content.
	const open_new = () => {
		frappe.new_doc("Memora Lesson", {
			topic: frm.doc.name,
			unit: frm.doc.unit,
			track: frm.doc.track,
			subject: frm.doc.subject,
		});
	};

	// Navigating away closes the dialog — warn if there's an unsaved reorder.
	if (dialog._dirty) {
		frappe.confirm(
			__("You have unsaved order changes that will be lost. Continue to add a new lesson?"),
			open_new
		);
	} else {
		open_new();
	}
}

function save_order(frm, dialog) {
	const $list = dialog.fields_dict.lessons_html.$wrapper.find(".reorder-list");
	const ordered = $list
		.children(".reorder-item")
		.map((i, el) => $(el).attr("data-lesson"))
		.get();

	frappe.call({
		method: "memora_admin.memora_admin.doctype.memora_topic.memora_topic.save_lesson_order",
		args: { topic: frm.doc.name, ordered_lessons: JSON.stringify(ordered) },
		freeze: true,
		freeze_message: __("Saving order..."),
		callback() {
			frappe.show_alert({ message: __("Lesson order saved."), indicator: "green" });
			dialog._original_order = ordered.join(",");
			dialog._dirty = false;
			dialog.fields_dict.lessons_html.$wrapper.find(".reorder-unsaved-alert").addClass("hide");
		},
	});
}
