function generateItemUUID() {
	if (typeof crypto !== "undefined" && crypto.randomUUID) {
		return crypto.randomUUID();
	}
	return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
		var r = (Math.random() * 16) | 0;
		var v = c === "x" ? r : (r & 0x3) | 0x8;
		return v.toString(16);
	});
}

async function isEffectivelySkippable(row) {
	// Per-stage override takes priority
	if (row.is_skippable) {
		return true;
	}
	// Fall back to global setting from Memora Lesson Stage Settings
	if (!row.stage_type) {
		return false;
	}
	let settings = await frappe.db.get_value(
		"Memora Lesson Stage Settings",
		row.stage_type,
		"is_skippable"
	);
	return !!(settings && settings.message && settings.message.is_skippable);
}

frappe.ui.form.on("Memora Lesson", {
	refresh: function (frm) {
		//
	},
});

frappe.ui.form.on("Memora Lesson Stage", {
	edit_content_btn: async function (frm, cdt, cdn) {
		let row = locals[cdt][cdn];

		if (!row.stage_type) {
			frappe.msgprint("الرجاء اختيار نوع المرحلة أولاً");
			return;
		}

		let config_json = {};
		if (row.config_json) {
			try {
				config_json = JSON.parse(row.config_json);
			} catch (e) {
				console.error("Invalid JSON", e);
			}
		}

		// Resolve effective is_skippable (per-stage override then global fallback)
		let skipItemIds = await isEffectivelySkippable(row);

		if (row.stage_type === "MATCHING") {
			open_matching_dialog(frm, cdt, cdn, row, config_json, skipItemIds);
		} else if (row.stage_type === "REVEAL") {
			open_reveal_dialog(frm, cdt, cdn, row, config_json, skipItemIds);
		} else if (row.stage_type === "SENTENCE_BUILDER") {
			open_sentence_builder_dialog(frm, cdt, cdn, row, config_json, skipItemIds);
		} else if (row.stage_type === "MINDMAP") {
			open_mindmap_dialog(frm, cdt, cdn, row, config_json, skipItemIds);
		} else {
			frappe.msgprint("لا يوجد محرر لهذا النوع بعد");
		}
	},
});

// =================================================
// 🧩 1. نافذة إعدادات التوصيل (Matching)
// =================================================
function open_matching_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	let existing_data = (data.pairs || []).map((p) => ({
		item_1: p.right,
		item_2: p.left,
		item_id: p.item_id || null,
	}));

	let d = new frappe.ui.Dialog({
		title: "إعدادات التوصيل (Matching)",
		fields: [
			{
				label: "التعليمات",
				fieldname: "instruction",
				fieldtype: "Data",
				default: data.instruction || "طابق العناصر",
			},
			{
				label: "الأزواج",
				fieldname: "pairs_table",
				fieldtype: "Table",
				cannot_add_rows: false,
				fields: [
					{
						label: "اليمين (Right)",
						fieldname: "item_1",
						fieldtype: "Data",
						in_list_view: 1,
						reqd: 1,
					},
					{
						label: "اليسار (Left)",
						fieldname: "item_2",
						fieldtype: "Data",
						in_list_view: 1,
						reqd: 1,
					},
					{
						fieldname: "item_id",
						fieldtype: "Data",
						hidden: 1,
					},
				],
				data: existing_data,
				get_data: () => existing_data,
			},
		],
		size: "large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			let config_payload = {
				instruction: values.instruction,
				pairs: values.pairs_table.map((p, index) => {
					let pair = {
						id: String(index + 1),
						right: p.item_1,
						left: p.item_2,
					};
					if (!skipItemIds) {
						pair.item_id = p.item_id || generateItemUUID();
					}
					return pair;
				}),
			};
			frappe.model.set_value(
				cdt,
				cdn,
				"config_json",
				JSON.stringify(config_payload, null, 2)
			);
			d.hide();
			frappe.show_alert({ message: "تم الحفظ", indicator: "green" });
		},
	});

	d.show();
}

// =================================================
// 🔍 2. نافذة إعدادات الكشف (Reveal)
// =================================================
function open_reveal_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	let existing_data = (data.highlights || []).map((h) => ({
		item_1: h.word,
		item_2: h.explanation,
		item_id: h.item_id || null,
	}));

	let d = new frappe.ui.Dialog({
		title: "إعدادات الكشف (Reveal)",
		fields: [
			{
				label: "الأيقونة (Emoji)",
				fieldname: "image",
				fieldtype: "Data",
				default: data.image,
			},
			{
				label: "الجملة",
				fieldname: "sentence",
				fieldtype: "Small Text",
				reqd: 1,
				default: data.sentence,
			},
			{
				label: "الكلمات",
				fieldname: "highlights_table",
				fieldtype: "Table",
				cannot_add_rows: false,
				fields: [
					{
						label: "الكلمة (Word)",
						fieldname: "item_1",
						fieldtype: "Data",
						in_list_view: 1,
						reqd: 1,
					},
					{
						label: "الشرح (Explanation)",
						fieldname: "item_2",
						fieldtype: "Data",
						in_list_view: 1,
					},
					{
						fieldname: "item_id",
						fieldtype: "Data",
						hidden: 1,
					},
				],
				data: existing_data,
				get_data: () => existing_data,
			},
		],
		size: "large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			let config_payload = {
				image: values.image,
				sentence: values.sentence,
				highlights: values.highlights_table.map((h) => {
					let highlight = {
						word: h.item_1,
						explanation: h.item_2,
					};
					if (!skipItemIds) {
						highlight.item_id = h.item_id || generateItemUUID();
					}
					return highlight;
				}),
			};
			frappe.model.set_value(
				cdt,
				cdn,
				"config_json",
				JSON.stringify(config_payload, null, 2)
			);
			d.hide();
			frappe.show_alert({ message: "تم الحفظ", indicator: "green" });
		},
	});

	d.show();
}

// =================================================
// 🏗️ 3. نافذة بناء الجملة (Sentence Builder)
// =================================================
function open_sentence_builder_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	// Backward compat: old format is ["word1", "word2"] (string array)
	// New format is [{item_id: "uuid", text: "word1"}, ...] (object array)
	let existing_data = (data.words || []).map((w) => {
		if (typeof w === "string") {
			return { item_1: w, item_id: null };
		}
		return { item_1: w.text, item_id: w.item_id || null };
	});

	let d = new frappe.ui.Dialog({
		title: "إعدادات بناء الجملة (Sentence Builder)",
		fields: [
			{
				label: "التعليمات",
				fieldname: "instruction",
				fieldtype: "Data",
				default: data.instruction || "رتب الكلمات لتكوين جملة صحيحة",
				description: "مثال: رتب الكلمات التالية",
			},
			{
				fieldtype: "Section Break",
				label: "محتوى الجملة",
			},
			{
				label: "الجملة الكاملة (للمراجعة)",
				fieldname: "sentence",
				fieldtype: "Small Text",
				default: data.sentence,
				description: "اكتب الجملة كاملة هنا كمرجع",
			},
			{
				label: "الكلمات/المقاطع مرتبة (Words Tokens)",
				fieldname: "words_table",
				fieldtype: "Table",
				cannot_add_rows: false,
				description:
					"أضف الكلمات بالترتيب الصحيح. ملاحظة: يمكنك إضافة عبارة كاملة في سطر واحد لتظهر كزر واحد (مثل: حق إصدار العملة)",
				fields: [
					{
						label: "الكلمة / العبارة",
						fieldname: "item_1",
						fieldtype: "Data",
						in_list_view: 1,
						reqd: 1,
					},
					{
						fieldname: "item_id",
						fieldtype: "Data",
						hidden: 1,
					},
				],
				data: existing_data,
			},
		],
		size: "large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			let config_payload = {
				instruction: values.instruction,
				sentence: values.sentence,
				words: values.words_table.map((r) => {
					let word = { text: r.item_1 };
					if (!skipItemIds) {
						word.item_id = r.item_id || generateItemUUID();
					}
					return word;
				}),
			};

			frappe.model.set_value(
				cdt,
				cdn,
				"config_json",
				JSON.stringify(config_payload, null, 2)
			);

			d.hide();
			frappe.show_alert({ message: "تم حفظ إعدادات الجملة", indicator: "green" });
		},
	});

	d.show();
}

// =================================================
// 🧠 4. نافذة الخريطة الذهنية (Mind Map)
// =================================================
function open_mindmap_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	let root_label = data.label || "";
	let root_description = data.description || "";

	// Convert saved tree to flat ordered list, preserving item_id
	let existing_data = [];
	if (data.children && Array.isArray(data.children)) {
		data.children.forEach((branch) => {
			existing_data.push({
				node_type: "فرع",
				label: branch.label,
				description: branch.description || "",
				item_id: branch.item_id || null,
			});
			if (branch.children && Array.isArray(branch.children)) {
				branch.children.forEach((item) => {
					existing_data.push({
						node_type: "عنصر",
						label: item.label,
						description: item.description || "",
						item_id: item.item_id || null,
					});
				});
			}
		});
	}

	let d = new frappe.ui.Dialog({
		title: "إعدادات الخريطة الذهنية (Mind Map)",
		fields: [
			{
				label: "عنوان الخريطة (العنوان الرئيسي)",
				fieldname: "root_label",
				fieldtype: "Data",
				reqd: 1,
				default: root_label,
			},
			{
				label: "وصف الخريطة",
				fieldname: "root_description",
				fieldtype: "Small Text",
				default: root_description,
			},
			{
				fieldtype: "Section Break",
				label: "محتوى الخريطة",
			},
			{
				label: "",
				fieldname: "nodes_table",
				fieldtype: "Table",
				cannot_add_rows: false,
				description:
					'أضف "فرع" للفروع الرئيسية، و"عنصر" للتفاصيل تحت كل فرع. كل عنصر ينتمي للفرع الذي يسبقه في القائمة.',
				fields: [
					{
						label: "النوع",
						fieldname: "node_type",
						fieldtype: "Select",
						options: "فرع\nعنصر",
						in_list_view: 1,
						reqd: 1,
						columns: 2,
					},
					{
						label: "العنوان",
						fieldname: "label",
						fieldtype: "Data",
						in_list_view: 1,
						reqd: 1,
						columns: 4,
					},
					{
						label: "الوصف",
						fieldname: "description",
						fieldtype: "Data",
						in_list_view: 1,
						columns: 4,
					},
					{
						fieldname: "item_id",
						fieldtype: "Data",
						hidden: 1,
					},
				],
				data: existing_data,
				get_data: () => existing_data,
			},
		],
		size: "extra-large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			let children = [];
			let current_branch = null;
			let used_ids = new Set();

			if (values.nodes_table.length > 0 && values.nodes_table[0].node_type !== "فرع") {
				frappe.msgprint(
					'يجب أن يكون الصف الأول من نوع "فرع". لا يمكن إضافة عنصر بدون فرع يسبقه.'
				);
				return;
			}

			for (let node of values.nodes_table) {
				let id = _generate_mindmap_id(used_ids);
				used_ids.add(id);

				if (node.node_type === "فرع") {
					current_branch = {
						id: id,
						label: node.label,
						children: [],
					};
					if (!skipItemIds) {
						current_branch.item_id = node.item_id || generateItemUUID();
					}
					if (node.description) current_branch.description = node.description;
					children.push(current_branch);
				} else {
					if (!current_branch) {
						frappe.msgprint("لا يمكن إضافة عنصر بدون فرع يسبقه.");
						return;
					}
					let item = {
						id: id,
						label: node.label,
					};
					if (!skipItemIds) {
						item.item_id = node.item_id || generateItemUUID();
					}
					if (node.description) item.description = node.description;
					current_branch.children.push(item);
				}
			}

			if (children.length === 0) {
				frappe.msgprint("يجب إضافة فرع واحد على الأقل.");
				return;
			}

			let config_payload = {
				label: values.root_label,
				children: children,
			};
			if (values.root_description) {
				config_payload.description = values.root_description;
			}

			frappe.model.set_value(
				cdt,
				cdn,
				"config_json",
				JSON.stringify(config_payload, null, 2)
			);
			d.hide();
			frappe.show_alert({ message: "تم حفظ الخريطة الذهنية", indicator: "green" });
		},
	});

	d.show();
}

function _generate_mindmap_id(used_ids) {
	const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
	let id;
	do {
		id = "";
		for (let i = 0; i < 3; i++) {
			id += chars.charAt(Math.floor(Math.random() * chars.length));
		}
	} while (used_ids.has(id));
	return id;
}
