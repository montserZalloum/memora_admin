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
		} else if (row.stage_type === "QUESTION") {
			open_question_dialog(frm, cdt, cdn, row, config_json, skipItemIds);
		} else if (row.stage_type === "INFORMATION") {
			open_information_dialog(frm, cdt, cdn, row, config_json, skipItemIds);
		} else if (row.stage_type === "FILL_BLANK") {
			open_fill_blank_dialog(frm, cdt, cdn, row, config_json, skipItemIds);
		} else if (row.stage_type === "STORY") {
			open_story_dialog(frm, cdt, cdn, row, config_json, skipItemIds);
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
	let sentence = data.sentence || "";

	// Convert old word-based format → from/to by locating each word in the sentence
	let reveals = [];
	for (let h of data.highlights || []) {
		if (!h.word) continue;
		let idx = sentence.indexOf(h.word);
		if (idx !== -1) {
			reveals.push({
				from: idx,
				to: idx + h.word.length,
				explanation: h.explanation || "",
				item_id: h.item_id || null,
			});
		}
	}

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
				label: "التعليمات",
				fieldname: "instruction",
				fieldtype: "Data",
				default: data.instruction || "اكتشف معاني الكلمات",
			},
			{
				fieldtype: "Section Break",
				label: "المحتوى",
			},
			{
				label: "الجملة",
				fieldname: "sentence",
				fieldtype: "Small Text",
				reqd: 1,
				default: sentence,
				description: "تعديل الجملة قد يُبطل الكشوفات الحالية إذا تغيّر موضع الكلمات",
			},
			{
				fieldtype: "Section Break",
				label: "المعاينة — حدد نصاً لإضافة كشف، واضغط على كلمة مكشوفة لإزالتها",
			},
			{
				fieldname: "preview_html",
				fieldtype: "HTML",
			},
		],
		size: "extra-large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			if (!values.sentence) {
				frappe.msgprint("يجب إدخال الجملة.");
				return;
			}
			let text = values.sentence;
			let config_payload = {
				image: values.image,
				instruction: values.instruction,
				sentence: text,
				highlights: reveals.map((h) => {
					let hl = { word: text.slice(h.from, h.to), explanation: h.explanation };
					if (!skipItemIds) {
						hl.item_id = h.item_id || generateItemUUID();
					}
					return hl;
				}),
			};
			frappe.model.set_value(cdt, cdn, "config_json", JSON.stringify(config_payload, null, 2));
			d.hide();
			frappe.show_alert({ message: "تم الحفظ", indicator: "green" });
		},
	});

	// --- helpers ---

	function _escapeHtml(str) {
		return str
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}

	function _getTextOffset(container, targetNode, targetOffset) {
		let walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
		let offset = 0;
		while (walker.nextNode()) {
			if (walker.currentNode === targetNode) {
				return offset + targetOffset;
			}
			offset += walker.currentNode.textContent.length;
		}
		return offset;
	}

	// --- preview renderer ---

	function renderPreview() {
		let text = d.get_value("sentence") || "";

		// Drop reveals that fell out of bounds after a text edit
		reveals = reveals.filter((h) => h.from >= 0 && h.to <= text.length && h.from < h.to);

		let sorted = [...reveals].sort((a, b) => a.from - b.from);

		// Build highlighted sentence HTML
		let html = "";
		let lastEnd = 0;
		for (let h of sorted) {
			if (h.from < lastEnd) continue;
			if (h.from > lastEnd) html += _escapeHtml(text.slice(lastEnd, h.from));
			html +=
				'<mark class="reveal-hl" data-from="' +
				h.from +
				'" data-to="' +
				h.to +
				'" style="background:#cff4fc;padding:2px 6px;border-radius:4px;cursor:pointer;' +
				'border-bottom:2px solid #0dcaf0;" title="' +
				_escapeHtml(h.explanation || "(بدون شرح)") +
				' — اضغط لإزالة">' +
				_escapeHtml(text.slice(h.from, h.to)) +
				"</mark>";
			lastEnd = h.to;
		}
		if (lastEnd < text.length) html += _escapeHtml(text.slice(lastEnd));

		// Build reveals list
		let listHtml = "";
		if (sorted.length > 0) {
			listHtml =
				'<div style="margin-top:14px;border-top:1px solid #eee;padding-top:10px;">' +
				'<div style="font-weight:600;font-size:12px;color:#6c757d;margin-bottom:8px;">الكشوفات المضافة</div>';
			for (let h of sorted) {
				let word = _escapeHtml(text.slice(h.from, h.to));
				let expl = _escapeHtml(h.explanation || "");
				listHtml +=
					'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;' +
					'background:#f8f9fa;border-radius:4px;padding:6px 10px;">' +
					'<span style="font-weight:600;color:#0c7c8c;min-width:80px;direction:rtl;">' +
					word +
					"</span>" +
					'<span style="color:#adb5bd;font-size:12px;">←</span>' +
					'<span style="flex:1;direction:rtl;color:#212529;">' +
					(expl || '<em style="color:#adb5bd;">بدون شرح</em>') +
					"</span>" +
					'<button class="reveal-edit-btn btn btn-xs btn-default" data-from="' +
					h.from +
					'" data-to="' +
					h.to +
					'" title="تعديل الشرح">✏️</button>' +
					'<button class="reveal-remove-btn btn btn-xs btn-danger" data-from="' +
					h.from +
					'" data-to="' +
					h.to +
					'" title="إزالة">×</button>' +
					"</div>";
			}
			listHtml += "</div>";
		}

		let wrapper =
			'<div style="position:relative;">' +
			'<div class="reveal-preview-text" style="padding:15px;border:1px solid #d1d8dd;' +
			"border-radius:4px;min-height:80px;line-height:2.5;font-size:15px;" +
			'direction:rtl;white-space:pre-wrap;user-select:text;">' +
			(html || '<span style="color:#8d99a6;">اكتب الجملة أعلاه لتظهر المعاينة</span>') +
			"</div>" +
			'<div class="reveal-add-tip" style="display:none;position:absolute;' +
			"background:#171717;color:#fff;padding:6px 14px;border-radius:6px;" +
			"cursor:pointer;font-size:13px;z-index:10;" +
			'box-shadow:0 2px 8px rgba(0,0,0,.15);white-space:nowrap;">إضافة كشف</div>' +
			listHtml +
			"</div>";

		let $wrapper = d.fields_dict.preview_html.$wrapper;
		$wrapper.html(wrapper);

		let $preview = $wrapper.find(".reveal-preview-text");
		let $tip = $wrapper.find(".reveal-add-tip");

		// --- text selection → "إضافة كشف" tooltip ---
		$preview.on("mouseup", function () {
			let sel = window.getSelection();
			if (!sel || sel.isCollapsed || !sel.rangeCount) {
				$tip.hide();
				return;
			}
			let range = sel.getRangeAt(0);
			let el = $preview[0];
			if (!el.contains(range.startContainer) || !el.contains(range.endContainer)) {
				$tip.hide();
				return;
			}
			let from = _getTextOffset(el, range.startContainer, range.startOffset);
			let to = _getTextOffset(el, range.endContainer, range.endOffset);
			if (from === to) {
				$tip.hide();
				return;
			}
			if (from > to) [from, to] = [to, from];

			let rect = range.getBoundingClientRect();
			let cRect = $wrapper.find("> div")[0].getBoundingClientRect();
			$tip.css({
				top: rect.top - cRect.top - 36,
				left: rect.left - cRect.left + rect.width / 2 - 50,
				display: "block",
			});

			$tip.off("click").on("click", function () {
				if (reveals.some((h) => from < h.to && to > h.from)) {
					frappe.msgprint("هذا التحديد يتداخل مع كشف موجود.");
					$tip.hide();
					sel.removeAllRanges();
					return;
				}
				$tip.hide();
				sel.removeAllRanges();
				let selectedWord = (d.get_value("sentence") || "").slice(from, to);
				frappe.prompt(
					{
						label: "الشرح",
						fieldname: "explanation",
						fieldtype: "Small Text",
						description: 'شرح الكلمة: "' + selectedWord + '"',
					},
					function (vals) {
						reveals.push({ from, to, explanation: vals.explanation || "", item_id: null });
						renderPreview();
					},
					"إضافة كشف",
					"إضافة"
				);
			});
		});

		// --- click highlight in preview → remove ---
		$preview.on("click", ".reveal-hl", function (e) {
			e.stopPropagation();
			let f = parseInt($(this).data("from"));
			let t = parseInt($(this).data("to"));
			reveals = reveals.filter((h) => !(h.from === f && h.to === t));
			renderPreview();
		});

	}

	// Hide tooltip on outside click
	d.$wrapper.on("mousedown.reveal_hl", function (e) {
		if (!$(e.target).closest(".reveal-add-tip").length) {
			d.fields_dict.preview_html.$wrapper.find(".reveal-add-tip").hide();
		}
	});

	// Re-render preview when sentence changes (debounced)
	d.$wrapper.on(
		"input",
		'[data-fieldname="sentence"] textarea',
		frappe.utils.debounce(renderPreview, 400)
	);

	d.onhide = function () {
		d.$wrapper.off("mousedown.reveal_hl");
	};

	d.show();
	renderPreview();

	// Bound ONCE after show — delegate on the stable $wrapper, not re-bound inside renderPreview
	let $hlWrapper = d.fields_dict.preview_html.$wrapper;

	$hlWrapper.on("click", ".reveal-edit-btn", function () {
		let f = parseInt($(this).data("from"));
		let t = parseInt($(this).data("to"));
		let reveal = reveals.find((h) => h.from === f && h.to === t);
		if (!reveal) return;
		let word = (d.get_value("sentence") || "").slice(f, t);
		frappe.prompt(
			{
				label: "الشرح",
				fieldname: "explanation",
				fieldtype: "Small Text",
				description: 'تعديل شرح: "' + word + '"',
				default: reveal.explanation,
			},
			function (vals) {
				reveal.explanation = vals.explanation || "";
				renderPreview();
			},
			"تعديل الشرح",
			"حفظ"
		);
	});

	$hlWrapper.on("click", ".reveal-remove-btn", function () {
		let f = parseInt($(this).data("from"));
		let t = parseInt($(this).data("to"));
		reveals = reveals.filter((h) => !(h.from === f && h.to === t));
		renderPreview();
	});
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

// =================================================
// ❓ 5. نافذة السؤال متعدد الخيارات (Question)
// =================================================
function open_question_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	let existing_answers = (data.answers || []).map((a) => ({
		answer_text: a.text,
		is_correct: a.is_correct ? 1 : 0,
		item_id: a.item_id || null,
	}));

	let d = new frappe.ui.Dialog({
		title: "إعدادات السؤال (Question)",
		fields: [
			{
				label: "التعليمات",
				fieldname: "instruction",
				fieldtype: "Data",
				default: data.instruction || "اختر الإجابة الصحيحة",
			},
			{
				label: "نص السؤال",
				fieldname: "question",
				fieldtype: "Small Text",
				reqd: 1,
				default: data.question || "",
			},
			{
				fieldtype: "Section Break",
				label: "الإجابات",
			},
			{
				label: "",
				fieldname: "answers_table",
				fieldtype: "Table",
				cannot_add_rows: false,
				description: "أضف إجابتين على الأقل وحدد الإجابة الصحيحة",
				fields: [
					{
						label: "نص الإجابة",
						fieldname: "answer_text",
						fieldtype: "Data",
						in_list_view: 1,
						reqd: 1,
						columns: 7,
					},
					{
						label: "صحيحة؟",
						fieldname: "is_correct",
						fieldtype: "Check",
						in_list_view: 1,
						columns: 2,
					},
					{
						fieldname: "item_id",
						fieldtype: "Data",
						hidden: 1,
					},
				],
				data: existing_answers,
				get_data: () => existing_answers,
			},
		],
		size: "large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			if (!values.answers_table || values.answers_table.length < 2) {
				frappe.msgprint("يجب إضافة إجابتين على الأقل.");
				return;
			}

			let correct_count = values.answers_table.filter((a) => a.is_correct).length;
			if (correct_count === 0) {
				frappe.msgprint("يجب تحديد إجابة صحيحة واحدة على الأقل.");
				return;
			}
			if (correct_count > 1) {
				frappe.msgprint("يجب تحديد إجابة صحيحة واحدة فقط.");
				return;
			}

			let config_payload = {
				question: values.question,
				answers: values.answers_table.map((a) => {
					let answer = {
						text: a.answer_text,
						is_correct: !!a.is_correct,
					};
					if (!skipItemIds) {
						answer.item_id = a.item_id || generateItemUUID();
					}
					return answer;
				}),
			};

			frappe.model.set_value(
				cdt,
				cdn,
				"config_json",
				JSON.stringify(config_payload, null, 2)
			);
			d.hide();
			frappe.show_alert({ message: "تم حفظ السؤال", indicator: "green" });
		},
	});

	d.show();
}

// =================================================
// ℹ️ 6. نافذة المعلومات (Information)
// =================================================
function open_information_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	let highlights = (data.highlights || []).map((h) => ({
		from: h.from,
		to: h.to,
		item_id: h.item_id || null,
	}));

	let d = new frappe.ui.Dialog({
		title: "إعدادات المعلومات (Information)",
		fields: [
			{
				label: "التعليمات",
				fieldname: "instruction",
				fieldtype: "Data",
				default: data.instruction || "اقرأ المعلومات التالية",
			},
			{
				fieldtype: "Section Break",
				label: "المحتوى",
			},
			{
				label: "النص",
				fieldname: "info_text",
				fieldtype: "Small Text",
				reqd: 1,
				default: data.text || "",
				description: "تعديل النص قد يُبطل التمييزات الحالية إذا تغيّر موضع الكلمات",
			},
			{
				fieldtype: "Section Break",
				label: "المعاينة — حدد نصاً لإضافة تمييز، واضغط على تمييز موجود لإزالته",
			},
			{
				fieldname: "preview_html",
				fieldtype: "HTML",
			},
		],
		size: "extra-large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			if (!values.info_text) {
				frappe.msgprint("يجب إدخال النص.");
				return;
			}

			let config_payload = {
				instruction: values.instruction,
				text: values.info_text,
				highlights: highlights.map((h) => {
					let hl = { from: h.from, to: h.to };
					if (!skipItemIds) {
						hl.item_id = h.item_id || generateItemUUID();
					}
					return hl;
				}),
			};

			frappe.model.set_value(
				cdt,
				cdn,
				"config_json",
				JSON.stringify(config_payload, null, 2)
			);
			d.hide();
			frappe.show_alert({ message: "تم حفظ المعلومات", indicator: "green" });
		},
	});

	// --- helpers ---

	function _escapeHtml(str) {
		return str
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}

	function _getTextOffset(container, targetNode, targetOffset) {
		let walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
		let offset = 0;
		while (walker.nextNode()) {
			if (walker.currentNode === targetNode) {
				return offset + targetOffset;
			}
			offset += walker.currentNode.textContent.length;
		}
		return offset;
	}

	// --- preview renderer ---

	function renderPreview() {
		let text = d.get_value("info_text") || "";

		// Drop highlights that fell out of bounds after a text edit
		highlights = highlights.filter((h) => h.from >= 0 && h.to <= text.length && h.from < h.to);

		// Sort by start position
		let sorted = [...highlights].sort((a, b) => a.from - b.from);

		// Build HTML with <mark> segments
		let html = "";
		let lastEnd = 0;
		for (let h of sorted) {
			if (h.from < lastEnd) continue; // skip overlapping
			if (h.from > lastEnd) {
				html += _escapeHtml(text.slice(lastEnd, h.from));
			}
			html +=
				'<mark class="info-hl" data-from="' +
				h.from +
				'" data-to="' +
				h.to +
				'" ' +
				'style="background:#fff3cd;padding:1px 4px;border-radius:3px;cursor:pointer;" ' +
				'title="اضغط لإزالة التمييز">' +
				_escapeHtml(text.slice(h.from, h.to)) +
				"</mark>";
			lastEnd = h.to;
		}
		if (lastEnd < text.length) {
			html += _escapeHtml(text.slice(lastEnd));
		}

		let wrapper =
			'<div style="position:relative;">' +
			'<div class="info-preview-text" style="padding:15px;border:1px solid #d1d8dd;' +
			"border-radius:4px;min-height:80px;line-height:2.2;font-size:15px;" +
			'direction:rtl;white-space:pre-wrap;user-select:text;">' +
			(html || '<span style="color:#8d99a6;">اكتب النص أعلاه لتظهر المعاينة</span>') +
			"</div>" +
			'<div class="info-add-hl-tip" style="display:none;position:absolute;' +
			"background:#171717;color:#fff;padding:6px 14px;border-radius:6px;" +
			"cursor:pointer;font-size:13px;z-index:10;" +
			'box-shadow:0 2px 8px rgba(0,0,0,.15);white-space:nowrap;">' +
			"إضافة تمييز" +
			"</div>" +
			"</div>";

		let $wrapper = d.fields_dict.preview_html.$wrapper;
		$wrapper.html(wrapper);

		let $preview = $wrapper.find(".info-preview-text");
		let $tip = $wrapper.find(".info-add-hl-tip");

		// --- selection → tooltip ---
		$preview.on("mouseup", function () {
			let sel = window.getSelection();
			if (!sel || sel.isCollapsed || !sel.rangeCount) {
				$tip.hide();
				return;
			}

			let range = sel.getRangeAt(0);
			let el = $preview[0];

			if (!el.contains(range.startContainer) || !el.contains(range.endContainer)) {
				$tip.hide();
				return;
			}

			let from = _getTextOffset(el, range.startContainer, range.startOffset);
			let to = _getTextOffset(el, range.endContainer, range.endOffset);

			if (from === to) {
				$tip.hide();
				return;
			}
			if (from > to) [from, to] = [to, from];

			// Position tooltip above selection
			let rect = range.getBoundingClientRect();
			let cRect = $wrapper.find("> div")[0].getBoundingClientRect();

			$tip.css({
				top: rect.top - cRect.top - 36,
				left: rect.left - cRect.left + rect.width / 2 - 50,
				display: "block",
			});

			$tip.off("click").on("click", function () {
				// Prevent overlapping highlights
				if (highlights.some((h) => from < h.to && to > h.from)) {
					frappe.msgprint("هذا التحديد يتداخل مع تمييز موجود.");
					$tip.hide();
					sel.removeAllRanges();
					return;
				}
				highlights.push({ from: from, to: to, item_id: null });
				$tip.hide();
				sel.removeAllRanges();
				renderPreview();
			});
		});

		// --- click existing highlight → remove ---
		$preview.on("click", ".info-hl", function () {
			let f = parseInt($(this).data("from"));
			let t = parseInt($(this).data("to"));
			highlights = highlights.filter((h) => !(h.from === f && h.to === t));
			renderPreview();
		});
	}

	// Hide tooltip on outside click
	d.$wrapper.on("mousedown.info_hl", function (e) {
		if (!$(e.target).closest(".info-add-hl-tip").length) {
			d.fields_dict.preview_html.$wrapper.find(".info-add-hl-tip").hide();
		}
	});

	// Re-render preview when text changes (debounced)
	d.$wrapper.on(
		"input",
		'[data-fieldname="info_text"] textarea',
		frappe.utils.debounce(renderPreview, 400)
	);

	// Cleanup on close
	d.onhide = function () {
		d.$wrapper.off("mousedown.info_hl");
	};

	d.show();
	renderPreview();
}

// =================================================
// ✏️ 7. نافذة أكمل الفراغات (Fill in the Blank)
// =================================================
function open_fill_blank_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	let blanks = (data.blanks || []).map((b) => ({
		from: b.from,
		to: b.to,
		item_id: b.item_id || null,
	}));

	let d = new frappe.ui.Dialog({
		title: "إعدادات أكمل الفراغات (Fill in the Blank)",
		fields: [
			{
				label: "التعليمات",
				fieldname: "instruction",
				fieldtype: "Data",
				default: data.instruction || "أكمل الفراغات التالية",
			},
			{
				fieldtype: "Section Break",
				label: "المحتوى",
			},
			{
				label: "النص",
				fieldname: "fill_text",
				fieldtype: "Small Text",
				reqd: 1,
				default: data.text || "",
				description: "تعديل النص قد يُبطل الفراغات الحالية إذا تغيّر موضع الكلمات",
			},
			{
				fieldtype: "Section Break",
				label: "المعاينة — حدد نصاً لتحويله إلى فراغ، واضغط على فراغ موجود لإزالته",
			},
			{
				fieldname: "preview_html",
				fieldtype: "HTML",
			},
			{
				fieldtype: "Section Break",
				label: "المشتتات (كلمات خاطئة لإرباك الطالب)",
			},
			{
				label: "",
				fieldname: "distractors_table",
				fieldtype: "Table",
				cannot_add_rows: false,
				description: "أضف كلمات خاطئة تظهر مع الإجابات الصحيحة لإرباك الطالب",
				fields: [
					{
						label: "الكلمة المشتتة",
						fieldname: "text",
						fieldtype: "Data",
						in_list_view: 1,
						reqd: 1,
						columns: 10,
					},
				],
				data: (data.distractors || []).map((d) => ({
					text: typeof d === "string" ? d : d.text,
				})),
			},
		],
		size: "extra-large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			if (!values.fill_text) {
				frappe.msgprint("يجب إدخال النص.");
				return;
			}
			if (blanks.length === 0) {
				frappe.msgprint("يجب تحديد فراغ واحد على الأقل.");
				return;
			}

			let distractors = (values.distractors_table || [])
				.map((r) => r.text)
				.filter((t) => t && t.trim());

			let config_payload = {
				instruction: values.instruction,
				text: values.fill_text,
				blanks: blanks.map((b) => {
					let bl = { from: b.from, to: b.to };
					if (!skipItemIds) {
						bl.item_id = b.item_id || generateItemUUID();
					}
					return bl;
				}),
				distractors: distractors,
			};

			frappe.model.set_value(
				cdt,
				cdn,
				"config_json",
				JSON.stringify(config_payload, null, 2)
			);
			d.hide();
			frappe.show_alert({ message: "تم حفظ أكمل الفراغات", indicator: "green" });
		},
	});

	// --- helpers ---

	function _escapeHtml(str) {
		return str
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}

	function _getTextOffset(container, targetNode, targetOffset) {
		let walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
		let offset = 0;
		while (walker.nextNode()) {
			if (walker.currentNode === targetNode) {
				return offset + targetOffset;
			}
			offset += walker.currentNode.textContent.length;
		}
		return offset;
	}

	// --- preview renderer ---

	function renderPreview() {
		let text = d.get_value("fill_text") || "";

		// Drop blanks that fell out of bounds after a text edit
		blanks = blanks.filter((b) => b.from >= 0 && b.to <= text.length && b.from < b.to);

		// Sort by start position
		let sorted = [...blanks].sort((a, b) => a.from - b.from);

		// Build HTML with blank segments
		let html = "";
		let lastEnd = 0;
		for (let b of sorted) {
			if (b.from < lastEnd) continue; // skip overlapping
			if (b.from > lastEnd) {
				html += _escapeHtml(text.slice(lastEnd, b.from));
			}
			html +=
				'<mark class="fill-bl" data-from="' +
				b.from +
				'" data-to="' +
				b.to +
				'" ' +
				'style="background:#d4edda;padding:1px 4px;border-radius:3px;' +
				'border-bottom:2px dashed #28a745;cursor:pointer;" ' +
				'title="اضغط لإزالة الفراغ">' +
				_escapeHtml(text.slice(b.from, b.to)) +
				"</mark>";
			lastEnd = b.to;
		}
		if (lastEnd < text.length) {
			html += _escapeHtml(text.slice(lastEnd));
		}

		let wrapper =
			'<div style="position:relative;">' +
			'<div class="fill-preview-text" style="padding:15px;border:1px solid #d1d8dd;' +
			"border-radius:4px;min-height:80px;line-height:2.2;font-size:15px;" +
			'direction:rtl;white-space:pre-wrap;user-select:text;">' +
			(html || '<span style="color:#8d99a6;">اكتب النص أعلاه لتظهر المعاينة</span>') +
			"</div>" +
			'<div class="fill-add-bl-tip" style="display:none;position:absolute;' +
			"background:#171717;color:#fff;padding:6px 14px;border-radius:6px;" +
			"cursor:pointer;font-size:13px;z-index:10;" +
			'box-shadow:0 2px 8px rgba(0,0,0,.15);white-space:nowrap;">' +
			"إضافة فراغ" +
			"</div>" +
			"</div>";

		let $wrapper = d.fields_dict.preview_html.$wrapper;
		$wrapper.html(wrapper);

		let $preview = $wrapper.find(".fill-preview-text");
		let $tip = $wrapper.find(".fill-add-bl-tip");

		// --- selection → tooltip ---
		$preview.on("mouseup", function () {
			let sel = window.getSelection();
			if (!sel || sel.isCollapsed || !sel.rangeCount) {
				$tip.hide();
				return;
			}

			let range = sel.getRangeAt(0);
			let el = $preview[0];

			if (!el.contains(range.startContainer) || !el.contains(range.endContainer)) {
				$tip.hide();
				return;
			}

			let from = _getTextOffset(el, range.startContainer, range.startOffset);
			let to = _getTextOffset(el, range.endContainer, range.endOffset);

			if (from === to) {
				$tip.hide();
				return;
			}
			if (from > to) [from, to] = [to, from];

			// Position tooltip above selection
			let rect = range.getBoundingClientRect();
			let cRect = $wrapper.find("> div")[0].getBoundingClientRect();

			$tip.css({
				top: rect.top - cRect.top - 36,
				left: rect.left - cRect.left + rect.width / 2 - 50,
				display: "block",
			});

			$tip.off("click").on("click", function () {
				// Prevent overlapping blanks
				if (blanks.some((b) => from < b.to && to > b.from)) {
					frappe.msgprint("هذا التحديد يتداخل مع فراغ موجود.");
					$tip.hide();
					sel.removeAllRanges();
					return;
				}
				blanks.push({ from: from, to: to, item_id: null });
				$tip.hide();
				sel.removeAllRanges();
				renderPreview();
			});
		});

		// --- click existing blank → remove ---
		$preview.on("click", ".fill-bl", function () {
			let f = parseInt($(this).data("from"));
			let t = parseInt($(this).data("to"));
			blanks = blanks.filter((b) => !(b.from === f && b.to === t));
			renderPreview();
		});
	}

	// Hide tooltip on outside click
	d.$wrapper.on("mousedown.fill_bl", function (e) {
		if (!$(e.target).closest(".fill-add-bl-tip").length) {
			d.fields_dict.preview_html.$wrapper.find(".fill-add-bl-tip").hide();
		}
	});

	// Re-render preview when text changes (debounced)
	d.$wrapper.on(
		"input",
		'[data-fieldname="fill_text"] textarea',
		frappe.utils.debounce(renderPreview, 400)
	);

	// Cleanup on close
	d.onhide = function () {
		d.$wrapper.off("mousedown.fill_bl");
	};

	d.show();
	renderPreview();
}

// =================================================
// 📖 8. نافذة القصة متعددة الخطوات (Story)
// =================================================
function open_story_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	// --- helpers ---

	function _esc(str) {
		if (!str) return "";
		return str
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}

	function _makeStep(s) {
		s = s || {};
		return {
			text: s.text || "",
			image_url: s.image || "", // server URL (already uploaded)
			image_name: s.image_name || "", // Frappe File doc name for deletion
			pending_file: null, // browser File object, not yet uploaded
			preview_url: s.image || "", // blob URL or server URL for <img>
			item_id: s.item_id || null,
		};
	}

	// --- state ---
	let steps = (data.steps || []).map(_makeStep);
	if (steps.length === 0) steps.push(_makeStep());
	let activeIdx = 0;
	let filesToDelete = []; // image_name values to DELETE on save (removed images/steps)

	// --- state mutators ---

	function _syncStep() {
		let $w = d.fields_dict.story_html.$wrapper;
		if (steps[activeIdx]) {
			steps[activeIdx].text = $w.find(".story-text").val() || "";
		}
	}

	function _clearStepImage(s) {
		if (s.image_name) filesToDelete.push(s.image_name);
		if (s.preview_url && s.preview_url.startsWith("blob:")) URL.revokeObjectURL(s.preview_url);
		s.image_url = "";
		s.image_name = "";
		s.pending_file = null;
		s.preview_url = "";
	}

	function _removeStep(idx) {
		let s = steps[idx];
		// queue server file for deletion if already uploaded
		if (s.image_name) filesToDelete.push(s.image_name);
		// free blob memory if pending
		if (s.preview_url && s.preview_url.startsWith("blob:")) URL.revokeObjectURL(s.preview_url);
		steps.splice(idx, 1);
		if (activeIdx >= steps.length) activeIdx = steps.length - 1;
	}

	// --- async operations ---

	function _uploadPending() {
		let pending = steps.filter((s) => s.pending_file);
		if (!pending.length) return Promise.resolve();
		return Promise.all(
			pending.map(
				(s) =>
					new Promise((resolve, reject) => {
						let fd = new FormData();
						fd.append("file", s.pending_file, s.pending_file.name);
						fd.append("doctype", frm.doctype);
						fd.append("docname", frm.docname || "new-doc");
						fd.append("is_private", "0");
						// auto-optimize images >200 KB (Frappe resizes to 1920×1080, quality 85%)
						if (
							s.pending_file.size > 200 * 1024 &&
							!s.pending_file.type.includes("svg")
						) {
							fd.append("optimize", true);
						}
						$.ajax({
							url: "/api/method/upload_file",
							type: "POST",
							data: fd,
							processData: false,
							contentType: false,
							headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
							success(resp) {
								if (resp && resp.message) {
									if (s.preview_url && s.preview_url.startsWith("blob:"))
										URL.revokeObjectURL(s.preview_url);
									s.image_url = resp.message.file_url;
									s.image_name = resp.message.name;
									s.pending_file = null;
									s.preview_url = s.image_url;
								}
								resolve();
							},
							error(xhr) {
								reject(new Error(xhr.responseText || xhr.statusText));
							},
						});
					})
			)
		);
	}

	function _deleteQueued() {
		let names = filesToDelete.splice(0);
		names.forEach((name) => {
			frappe.call({ method: "frappe.client.delete", args: { doctype: "File", name } });
		});
	}

	// --- renderer ---

	function _render() {
		let $w = d.fields_dict.story_html.$wrapper;

		let tabsHtml = steps
			.map((s, i) => {
				let active = i === activeIdx;
				let preview = s.text
					? _esc(s.text.substring(0, 22)) + (s.text.length > 22 ? "…" : "")
					: s.preview_url
					? "🖼️ صورة"
					: "";
				return (
					'<div class="story-tab" data-idx="' +
					i +
					'" style="padding:9px 12px;cursor:pointer;border-bottom:1px solid #f0f0f0;' +
					"border-right:3px solid " +
					(active ? "#1d7fd4" : "transparent") +
					";background:" +
					(active ? "#e8f4fd" : "#fff") +
					';">' +
					'<div style="font-size:12px;font-weight:' +
					(active ? "bold" : "normal") +
					';color:#333;">الخطوة ' +
					(i + 1) +
					"</div>" +
					'<div style="font-size:11px;color:' +
					(preview ? "#888" : "#ccc") +
					';margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' +
					(preview || "فارغة") +
					"</div></div>"
				);
			})
			.join("");

		let cur = steps[activeIdx] || _makeStep();

		// image area: show preview+remove button OR drop zone
		let imgHtml = cur.preview_url
			? '<div style="position:relative;display:inline-block;max-width:100%;">' +
			  '<img src="' +
			  _esc(cur.preview_url) +
			  '" style="max-width:100%;max-height:220px;border-radius:8px;display:block;' +
			  'object-fit:contain;border:1px solid #e0e0e0;" onerror="this.style.display=\'none\'">' +
			  '<button class="story-img-remove" ' +
			  'style="position:absolute;top:6px;left:6px;background:rgba(220,53,69,.85);color:#fff;' +
			  "border:none;border-radius:50%;width:26px;height:26px;font-size:16px;line-height:26px;" +
			  'cursor:pointer;text-align:center;" title="إزالة الصورة">×</button>' +
			  (cur.pending_file
					? '<div style="font-size:11px;color:#888;margin-top:5px;">📎 ' +
					  _esc(cur.pending_file.name) +
					  " — سيتم الرفع عند الحفظ</div>"
					: "") +
			  "</div>"
			: '<label class="story-upload-label" ' +
			  'style="display:flex;flex-direction:column;align-items:center;justify-content:center;' +
			  "border:2px dashed #d1d8dd;border-radius:8px;padding:28px 20px;cursor:pointer;" +
			  'color:#8d99a6;min-height:90px;transition:border-color .15s,background .15s;">' +
			  '<span style="font-size:30px;margin-bottom:6px;">🖼️</span>' +
			  '<span style="font-size:13px;font-weight:500;">اضغط لاختيار صورة</span>' +
			  '<span style="font-size:11px;margin-top:3px;color:#b0bec5;">PNG، JPG، GIF — اختياري</span>' +
			  '<input class="story-file-input" type="file" accept="image/*" style="display:none;">' +
			  "</label>";

		$w.html(
			'<div style="display:flex;min-height:430px;direction:rtl;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">' +
				// sidebar
				'<div style="width:155px;border-left:1px solid #e0e0e0;background:#fafafa;display:flex;flex-direction:column;flex-shrink:0;">' +
				'<div style="padding:9px 12px;font-size:11px;font-weight:bold;color:#666;border-bottom:1px solid #e0e0e0;background:#f5f5f5;">' +
				"الخطوات (" +
				steps.length +
				")</div>" +
				'<div class="story-tabs" style="flex:1;overflow-y:auto;">' +
				tabsHtml +
				"</div>" +
				'<div style="padding:8px;border-top:1px solid #e0e0e0;">' +
				'<button class="btn btn-xs btn-primary story-add" style="width:100%;font-size:11px;">+ إضافة خطوة</button>' +
				"</div></div>" +
				// editor
				'<div style="flex:1;padding:20px;background:#fff;min-width:0;overflow-y:auto;">' +
				'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">' +
				'<span style="font-size:14px;font-weight:bold;color:#333;">الخطوة ' +
				(activeIdx + 1) +
				"</span>" +
				'<div style="display:flex;gap:6px;">' +
				'<button class="btn btn-xs btn-default story-up" ' +
				(activeIdx === 0 ? "disabled" : "") +
				' title="تحريك لأعلى" style="font-size:13px;">↑</button>' +
				'<button class="btn btn-xs btn-default story-down" ' +
				(activeIdx === steps.length - 1 ? "disabled" : "") +
				' title="تحريك لأسفل" style="font-size:13px;">↓</button>' +
				'<button class="btn btn-xs btn-danger story-del" ' +
				(steps.length === 1 ? "disabled" : "") +
				">حذف</button>" +
				"</div></div>" +
				// text
				'<div style="margin-bottom:16px;">' +
				'<label style="display:block;font-size:12px;font-weight:bold;color:#555;margin-bottom:5px;">' +
				'النص <span style="font-weight:normal;color:#aaa;">(اختياري)</span></label>' +
				'<textarea class="story-text form-control" rows="4" ' +
				'placeholder="اكتب نص الخطوة هنا..." style="resize:vertical;direction:rtl;line-height:1.9;">' +
				_esc(cur.text) +
				"</textarea></div>" +
				// image
				'<div><label style="display:block;font-size:12px;font-weight:bold;color:#555;margin-bottom:8px;">' +
				'الصورة <span style="font-weight:normal;color:#aaa;">(اختياري)</span></label>' +
				imgHtml +
				"</div>" +
				// nav
				'<div style="display:flex;justify-content:space-between;align-items:center;margin-top:20px;padding-top:12px;border-top:1px solid #f0f0f0;">' +
				'<button class="btn btn-xs btn-default story-prev" ' +
				(activeIdx === 0 ? "disabled" : "") +
				">→ السابق</button>" +
				'<span style="font-size:12px;color:#888;">' +
				(activeIdx + 1) +
				" / " +
				steps.length +
				"</span>" +
				'<button class="btn btn-xs btn-default story-next" ' +
				(activeIdx === steps.length - 1 ? "disabled" : "") +
				">التالي ←</button>" +
				"</div></div></div>"
		);

		// --- events ---

		$w.find(".story-tab").on("click", function () {
			_syncStep();
			activeIdx = parseInt($(this).data("idx"));
			_render();
		});

		$w.find(".story-add").on("click", function () {
			_syncStep();
			steps.push(_makeStep());
			activeIdx = steps.length - 1;
			_render();
		});

		$w.find(".story-del").on("click", function () {
			if (steps.length <= 1) return;
			_syncStep();
			_removeStep(activeIdx);
			_render();
		});

		$w.find(".story-up").on("click", function () {
			if (!activeIdx) return;
			_syncStep();
			[steps[activeIdx], steps[activeIdx - 1]] = [steps[activeIdx - 1], steps[activeIdx]];
			activeIdx--;
			_render();
		});

		$w.find(".story-down").on("click", function () {
			if (activeIdx === steps.length - 1) return;
			_syncStep();
			[steps[activeIdx], steps[activeIdx + 1]] = [steps[activeIdx + 1], steps[activeIdx]];
			activeIdx++;
			_render();
		});

		$w.find(".story-prev").on("click", function () {
			if (!activeIdx) return;
			_syncStep();
			activeIdx--;
			_render();
		});

		$w.find(".story-next").on("click", function () {
			if (activeIdx === steps.length - 1) return;
			_syncStep();
			activeIdx++;
			_render();
		});

		// file picker
		$w.find(".story-file-input").on("change", function (e) {
			let file = e.target.files && e.target.files[0];
			if (!file) return;
			let s = steps[activeIdx];
			// if replacing an existing server file, queue it for deletion
			if (s.image_name) filesToDelete.push(s.image_name);
			// revoke old blob URL to free memory
			if (s.preview_url && s.preview_url.startsWith("blob:"))
				URL.revokeObjectURL(s.preview_url);
			s.image_url = "";
			s.image_name = "";
			s.pending_file = file;
			s.preview_url = URL.createObjectURL(file);
			_render();
		});

		// remove image
		$w.find(".story-img-remove").on("click", function (e) {
			e.stopPropagation();
			_syncStep();
			_clearStepImage(steps[activeIdx]);
			_render();
		});

		// drop zone hover
		$w.find(".story-upload-label")
			.on("mouseenter", function () {
				$(this).css({ borderColor: "#1d7fd4", background: "#f0f7ff" });
			})
			.on("mouseleave", function () {
				$(this).css({ borderColor: "#d1d8dd", background: "" });
			});

		// live sidebar preview on text input
		$w.find(".story-text").on(
			"input",
			frappe.utils.debounce(function () {
				let val = $(this).val() || "";
				let preview = val
					? val.substring(0, 22) + (val.length > 22 ? "…" : "")
					: steps[activeIdx] && steps[activeIdx].preview_url
					? "🖼️ صورة"
					: "فارغة";
				$w.find('.story-tab[data-idx="' + activeIdx + '"] div:last-child').text(preview);
			}, 300)
		);
	}

	// --- dialog ---

	let d = new frappe.ui.Dialog({
		title: "إعدادات القصة (Story)",
		fields: [
			{
				label: "التعليمات",
				fieldname: "instruction",
				fieldtype: "Data",
				default: data.instruction || "اقرأ القصة التالية",
			},
			{ fieldtype: "Section Break", label: "خطوات القصة" },
			{ fieldname: "story_html", fieldtype: "HTML" },
		],
		size: "extra-large",
		primary_action_label: "حفظ (Save)",
		primary_action: async function (values) {
			_syncStep();

			if (!steps.some((s) => s.text || s.preview_url)) {
				frappe.msgprint("يجب إضافة محتوى لخطوة واحدة على الأقل (نص أو صورة).");
				return;
			}

			let $btn = d.$wrapper.find(".modal-footer .btn-primary");
			$btn.prop("disabled", true).text("جاري رفع الصور...");

			try {
				await _uploadPending();
			} catch (e) {
				frappe.msgprint("حدث خطأ أثناء رفع إحدى الصور. يرجى المحاولة مرة أخرى.");
				console.error(e);
				$btn.prop("disabled", false).text("حفظ (Save)");
				return;
			}

			// fire-and-forget: delete removed/replaced server files
			_deleteQueued();

			let config_payload = {
				instruction: values.instruction,
				steps: steps
					.filter((s) => s.text || s.image_url)
					.map((s, i) => {
						let step = { id: String(i + 1) };
						if (s.text) step.text = s.text;
						if (s.image_url) {
							step.image = s.image_url;
							step.image_name = s.image_name; // stored for future deletion
						}
						if (!skipItemIds) step.item_id = s.item_id || generateItemUUID();
						return step;
					}),
			};

			frappe.model.set_value(
				cdt,
				cdn,
				"config_json",
				JSON.stringify(config_payload, null, 2)
			);
			d.hide();
			frappe.show_alert({ message: "تم حفظ القصة", indicator: "green" });
		},
	});

	d.show();
	setTimeout(() => _render(), 50);
}
