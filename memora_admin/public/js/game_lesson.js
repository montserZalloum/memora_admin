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

/**
 * Extract item_id from parsed config_json object, checking top-level first
 * then falling back to type-specific paths.
 */
function _getItemIdFromConfig(config, stageType) {
	if (!config) return null;
	// Top-level item_id (stable location, always checked first)
	if (config.item_id) return config.item_id;

	if (stageType === "INFORMATION" || stageType === "REVEAL") {
		for (let h of config.highlights || []) {
			if (h.item_id) return h.item_id;
		}
	}
	if (stageType === "FILL_BLANK") {
		for (let b of config.blanks || []) {
			if (b.item_id) return b.item_id;
		}
	}
	if (stageType === "QUESTION") {
		for (let a of config.answers || []) {
			if (a.item_id) return a.item_id;
		}
	}
	if (stageType === "MATCHING") {
		for (let p of config.pairs || []) {
			if (p.item_id) return p.item_id;
		}
	}
	if (stageType === "SENTENCE_BUILDER") {
		for (let w of config.words || []) {
			if (typeof w === "object" && w.item_id) return w.item_id;
		}
	}
	return null;
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
		frm.add_custom_button(__("إضافة سؤال"), () => open_add_question_dialog(frm));
		frm.custom_buttons[__("إضافة سؤال")]?.removeClass("btn-default").addClass("btn-warning");
		if (frm.doc.stages && frm.doc.stages.length > 0) {
			frm.add_custom_button(__("Lesson Map"), () => open_lesson_map_dialog(frm));
			frm.custom_buttons[__("Lesson Map")]?.removeClass("btn-default").addClass("btn-danger");
		}
	},
});

// =================================================
// Add Question — quick-add a QUESTION stage
// =================================================

function open_add_question_dialog(frm) {
	let d = new frappe.ui.Dialog({
		title: "إضافة سؤال جديد",
		fields: [
			{
				label: "التعليمات",
				fieldname: "instruction",
				fieldtype: "Data",
				default: "اختر الإجابة الصحيحة",
			},
			{
				label: "نص السؤال",
				fieldname: "question",
				fieldtype: "Small Text",
				reqd: 1,
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
				],
				data: [],
			},
		],
		size: "large",
		primary_action_label: "إضافة",
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
				item_id: generateItemUUID(),
				instruction: values.instruction,
				question: values.question,
				answers: values.answers_table.map((a) => ({
					text: a.answer_text,
					is_correct: !!a.is_correct,
					item_id: generateItemUUID(),
				})),
			};

			let row = frm.add_child("stages");
			row.stage_type = "QUESTION";
			row.config_json = JSON.stringify(config_payload, null, 2);
			frm.refresh_field("stages");
			frm.dirty();
			d.hide();
			frappe.show_alert({ message: "تمت إضافة السؤال — اضغط حفظ لتأكيد", indicator: "blue" });
		},
	});

	d.show();
}

// =================================================
// Lesson Map — grouped item view with drag-to-reorder
// =================================================

const STAGE_TYPE_COLORS = {
	INFORMATION: { bg: "#e3f2fd", border: "#1976d2", text: "#1565c0", label: "معلومة" },
	FILL_BLANK: { bg: "#fff3e0", border: "#f57c00", text: "#e65100", label: "أكمل" },
	REVEAL: { bg: "#e8f5e9", border: "#388e3c", text: "#2e7d32", label: "كشف" },
	QUESTION: { bg: "#f3e5f5", border: "#7b1fa2", text: "#6a1b9a", label: "سؤال" },
	MATCHING: { bg: "#fce4ec", border: "#c62828", text: "#b71c1c", label: "توصيل" },
	STORY: { bg: "#e0f7fa", border: "#00838f", text: "#006064", label: "قصة" },
	MINDMAP: { bg: "#f1f8e9", border: "#558b2f", text: "#33691e", label: "خريطة" },
	SENTENCE_BUILDER: { bg: "#ede7f6", border: "#4527a0", text: "#311b92", label: "بناء جملة" },
};

function _extractItemId(stage) {
	let config;
	try {
		config = typeof stage.config_json === "string" ? JSON.parse(stage.config_json) : stage.config_json;
	} catch {
		return null;
	}
	return _getItemIdFromConfig(config, stage.stage_type);
}

function _extractPreviewText(stage) {
	let config;
	try {
		config = typeof stage.config_json === "string" ? JSON.parse(stage.config_json) : stage.config_json;
	} catch {
		return "";
	}
	if (!config) return "";
	return config.text || config.sentence || config.question || config.instruction || "";
}

function _groupStagesByItem(stages) {
	let groups = [];
	let groupMap = {};
	let nullCounter = 0;

	for (let stage of stages) {
		// MATCHING, MINDMAP, STORY are standalone checkpoints — never merged into an item group
		if (stage.stage_type === "MATCHING" || stage.stage_type === "MINDMAP" || stage.stage_type === "STORY") {
			groups.push({
				item_id: _extractItemId(stage),
				key: `__standalone_${nullCounter++}`,
				stages: [stage],
				isCheckpoint: true,
			});
			continue;
		}

		let itemId = _extractItemId(stage);
		let key = itemId || `__null_${nullCounter++}`;

		if (itemId && groupMap[itemId] !== undefined) {
			groups[groupMap[itemId]].stages.push(stage);
		} else {
			groupMap[key] = groups.length;
			if (itemId) groupMap[itemId] = groups.length;
			groups.push({
				item_id: itemId,
				key: key,
				stages: [stage],
			});
		}
	}

	// Set preview from the best available stage (prefer INFORMATION)
	for (let g of groups) {
		if (g.isCheckpoint) {
			g.preview = _extractPreviewText(g.stages[0]);
			continue;
		}
		let infoStage = g.stages.find((s) => s.stage_type === "INFORMATION");
		let previewStage = infoStage || g.stages[0];
		g.preview = _extractPreviewText(previewStage);
	}

	return groups;
}

function _escapeHtmlMap(str) {
	return (str || "")
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");
}

function _renderBadge(stageType, stageIdx) {
	let c = STAGE_TYPE_COLORS[stageType] || { bg: "#f5f5f5", border: "#999", text: "#666", label: stageType };
	let idxLabel = stageIdx != null ? `<span style="opacity:0.7;margin-right:3px;font-size:10px;">${stageIdx}</span>` : "";
	return `<span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:10px;font-size:11px;
		background:${c.bg};color:${c.text};border:1px solid ${c.border};margin-left:4px;">${idxLabel}${c.label}</span>`;
}

function _renderCheckpointCard(group, index, allGroups) {
	let s = group.stages[0];
	let c = STAGE_TYPE_COLORS[s.stage_type] || { bg: "#f5f5f5", border: "#999", text: "#666", label: s.stage_type };

	// Resolve subtitle: MATCHING shows referenced item numbers, others show preview text
	let subtitle = "";
	if (s.stage_type === "MATCHING") {
		try {
			let config = typeof s.config_json === "string" ? JSON.parse(s.config_json) : s.config_json;
			if (config && config.pairs) {
				let pairIds = [...new Set(config.pairs.map((p) => p.item_id).filter(Boolean))];
				let nums = [];
				for (let pid of pairIds) {
					let gi = allGroups.findIndex((g) => !g.isCheckpoint && g.item_id === pid);
					if (gi !== -1) nums.push(`#${gi + 1}`);
				}
				if (nums.length) subtitle = nums.join("، ");
			}
		} catch {}
	} else {
		subtitle = _escapeHtmlMap(_extractPreviewText(s));
		if (subtitle.length > 70) subtitle = subtitle.slice(0, 70) + "…";
	}

	return `<div class="lm-item-card lm-checkpoint-card" data-item-key="${group.key}" data-index="${index}"
		style="background:${c.bg};border:1px solid ${c.border};border-radius:6px;margin-bottom:8px;
		overflow:hidden;cursor:grab;">
		<div class="lm-item-header" data-item-key="${group.key}"
			style="display:flex;align-items:center;gap:10px;padding:8px 14px;cursor:pointer;">
			<span class="lm-drag-handle" style="cursor:grab;color:#aaa;font-size:16px;">⠿</span>
			<span style="font-weight:600;color:${c.text};min-width:50px;font-size:12px;">${c.label}</span>
			<span style="flex:1;direction:rtl;font-size:12px;color:${c.text};overflow:hidden;
				text-overflow:ellipsis;white-space:nowrap;font-weight:500;">${subtitle || c.label}</span>
			<button class="btn btn-xs btn-default lm-edit-stage-btn" data-stage-name="${s.name}"
				title="تعديل">✏️</button>
			<button class="btn btn-xs btn-danger-light lm-delete-item-btn" data-item-key="${group.key}"
				style="color:#c0392b;border-color:#e6b0aa;" title="حذف">✕</button>
		</div>
	</div>`;
}

function _renderItemCard(group, index, expanded, allGroups) {
	if (group.isCheckpoint) {
		return _renderCheckpointCard(group, index, allGroups);
	}

	let badges = group.stages.map((s) => _renderBadge(s.stage_type)).join("");
	let preview = _escapeHtmlMap(group.preview);
	if (preview.length > 90) preview = preview.slice(0, 90) + "…";

	let stagesHtml = "";
	if (expanded) {
		stagesHtml = '<div class="lm-item-stages" style="padding:10px 14px 6px;border-top:1px solid #eee;">';
		for (let s of group.stages) {
			let c = STAGE_TYPE_COLORS[s.stage_type] || { bg: "#f5f5f5", text: "#666", label: s.stage_type };
			let stagePreview = _escapeHtmlMap(_extractPreviewText(s));
			if (stagePreview.length > 100) stagePreview = stagePreview.slice(0, 100) + "…";
			stagesHtml += `<div class="lm-stage-row" data-stage-name="${s.name}"
				style="display:flex;align-items:center;gap:8px;padding:6px 8px;margin-bottom:4px;
				border-radius:4px;background:${c.bg}22;cursor:default;">
				<span style="min-width:24px;font-size:10px;color:#999;text-align:center;">${s.idx}</span>
				<span style="min-width:70px;font-size:11px;font-weight:600;color:${c.text};">${c.label}</span>
				<span style="flex:1;font-size:12px;color:#555;direction:rtl;overflow:hidden;
					text-overflow:ellipsis;white-space:nowrap;">${stagePreview || '<em style="color:#bbb;">—</em>'}</span>
				<button class="btn btn-xs btn-default lm-edit-stage-btn" data-stage-name="${s.name}"
					title="تعديل">✏️</button>
				<button class="btn btn-xs btn-danger-light lm-delete-stage-btn" data-stage-name="${s.name}"
					style="color:#c0392b;border-color:#e6b0aa;" title="حذف المرحلة">✕</button>
			</div>`;
		}
		stagesHtml += `<button class="btn btn-xs btn-default lm-add-stage-btn" data-item-key="${group.key}"
			style="width:100%;margin-top:6px;margin-bottom:4px;color:#888;border-style:dashed;">+ إضافة مرحلة</button>`;
		stagesHtml += "</div>";
	}

	return `<div class="lm-item-card" data-item-key="${group.key}" data-index="${index}"
		style="background:#fff;border:1px solid #d1d8dd;border-radius:6px;margin-bottom:8px;
		overflow:hidden;transition:box-shadow 0.15s;cursor:grab;">
		<div class="lm-item-header" data-item-key="${group.key}"
			style="display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;">
			<span class="lm-drag-handle" style="cursor:grab;color:#aaa;font-size:16px;">⠿</span>
			<span style="font-weight:600;color:#333;min-width:32px;">#${index + 1}</span>
			<span style="flex:1;direction:rtl;font-size:13px;color:#444;overflow:hidden;
				text-overflow:ellipsis;white-space:nowrap;">${preview || '<em style="color:#bbb;">بدون محتوى</em>'}</span>
			<span style="display:flex;gap:2px;flex-shrink:0;">${badges}</span>
			<button class="btn btn-xs btn-danger-light lm-delete-item-btn" data-item-key="${group.key}"
				style="color:#c0392b;border-color:#e6b0aa;margin-left:4px;" title="حذف العنصر بالكامل">🗑</button>
			<span class="lm-chevron" style="font-size:14px;color:#888;transition:transform 0.2s;
				transform:rotate(${expanded ? "90deg" : "0deg"});">▶</span>
		</div>
		${stagesHtml}
	</div>`;
}

function open_lesson_map_dialog(frm) {
	let stages = (frm.doc.stages || []).map((s) => ({ ...s }));
	let groups = _groupStagesByItem(stages);
	let expandedKeys = new Set();
	let deletedStageNames = new Set();

	let d = new frappe.ui.Dialog({
		title: `خريطة الدرس — ${frm.doc.lesson_title || frm.doc.name} (${stages.length} مرحلة، ${groups.length} عنصر)`,
		fields: [
			{
				fieldname: "map_html",
				fieldtype: "HTML",
			},
		],
		size: "extra-large",
		primary_action_label: "حفظ التغييرات",
		primary_action: function () {
			_applyChanges(frm, groups, deletedStageNames);
			d.hide();
		},
		secondary_action_label: "إلغاء",
		secondary_action: function () {
			d.hide();
		},
	});

	function _updateDialogTitle() {
		let totalStages = groups.reduce((sum, g) => sum + g.stages.length, 0);
		let delCount = deletedStageNames.size;
		let delSuffix = delCount > 0 ? ` — ${delCount} محذوف` : "";
		d.set_title(`خريطة الدرس — ${frm.doc.lesson_title || frm.doc.name} (${totalStages} مرحلة، ${groups.length} عنصر${delSuffix})`);
	}

	function render() {
		let html = `<div class="lm-container" style="max-height:65vh;overflow-y:auto;padding:4px;">
			<div class="lm-items-list">`;
		groups.forEach((g, i) => {
			html += _renderItemCard(g, i, expandedKeys.has(g.key), groups);
		});
		html += "</div></div>";

		let $wrapper = d.fields_dict.map_html.$wrapper;
		let scrollTop = $wrapper.find(".lm-container").scrollTop() || 0;
		$wrapper.html(html);
		$wrapper.find(".lm-container").scrollTop(scrollTop);

		// Expand/collapse on header click
		$wrapper.find(".lm-item-header").on("click", function (e) {
			if ($(e.target).closest(".lm-edit-stage-btn, .lm-delete-stage-btn, .lm-delete-item-btn").length) return;
			let key = $(this).data("item-key");
			if (expandedKeys.has(key)) {
				expandedKeys.delete(key);
			} else {
				expandedKeys.add(key);
			}
			render();
		});

		// Edit stage button
		$wrapper.find(".lm-edit-stage-btn").on("click", function (e) {
			e.stopPropagation();
			let stageName = $(this).data("stage-name");
			let row = (frm.doc.stages || []).find((s) => s.name === stageName);
			if (!row) return;
			frm.script_manager.trigger("edit_content_btn", row.doctype, row.name);
		});

		// Delete single stage
		$wrapper.find(".lm-delete-stage-btn").on("click", function (e) {
			e.stopPropagation();
			let stageName = $(this).data("stage-name");
			frappe.confirm(
				"هل تريد حذف هذه المرحلة؟",
				() => {
					for (let g of groups) {
						let idx = g.stages.findIndex((s) => s.name === stageName);
						if (idx !== -1) {
							g.stages.splice(idx, 1);
							deletedStageNames.add(stageName);
							// Remove empty groups
							if (g.stages.length === 0) {
								let gi = groups.indexOf(g);
								if (gi !== -1) {
									expandedKeys.delete(g.key);
									groups.splice(gi, 1);
								}
							} else {
								// Refresh preview from remaining stages
								let infoStage = g.stages.find((s) => s.stage_type === "INFORMATION");
								g.preview = _extractPreviewText(infoStage || g.stages[0]);
							}
							break;
						}
					}
					_updateDialogTitle();
					render();
				}
			);
		});

		// Delete entire item (all stages in group)
		$wrapper.find(".lm-delete-item-btn").on("click", function (e) {
			e.stopPropagation();
			let itemKey = $(this).data("item-key");
			let group = groups.find((g) => g.key === itemKey);
			if (!group) return;
			let count = group.stages.length;
			frappe.confirm(
				`هل تريد حذف هذا العنصر بالكامل؟ (${count} مرحلة)`,
				() => {
					for (let s of group.stages) {
						deletedStageNames.add(s.name);
					}
					expandedKeys.delete(itemKey);
					let gi = groups.indexOf(group);
					if (gi !== -1) groups.splice(gi, 1);
					_updateDialogTitle();
					render();
				}
			);
		});

		// Add stage to existing item group
		$wrapper.find(".lm-add-stage-btn").on("click", function (e) {
			e.stopPropagation();
			let itemKey = $(this).data("item-key");
			let group = groups.find((g) => g.key === itemKey);
			if (!group || !group.item_id) return;

			let existingTypes = new Set(group.stages.map((s) => s.stage_type));
			let allowedTypes = [
				{ value: "INFORMATION", label: STAGE_TYPE_COLORS.INFORMATION.label },
				{ value: "FILL_BLANK", label: STAGE_TYPE_COLORS.FILL_BLANK.label },
				{ value: "REVEAL", label: STAGE_TYPE_COLORS.REVEAL.label },
				{ value: "QUESTION", label: STAGE_TYPE_COLORS.QUESTION.label },
				{ value: "SENTENCE_BUILDER", label: STAGE_TYPE_COLORS.SENTENCE_BUILDER.label },
			].filter((t) => !existingTypes.has(t.value));

			if (allowedTypes.length === 0) {
				frappe.msgprint("هذا العنصر يحتوي على جميع أنواع المراحل المتاحة.");
				return;
			}
			let typeOptions = allowedTypes.map((t) => `${t.value} — ${t.label}`).join("\n");

			frappe.prompt(
				{
					label: "نوع المرحلة",
					fieldname: "stage_type",
					fieldtype: "Select",
					options: typeOptions,
					reqd: 1,
				},
				function (values) {
					let stageType = values.stage_type.split(" — ")[0];

					function _addStageRow(configJson) {
						let row = frm.add_child("stages");
						row.stage_type = stageType;
						row.config_json = JSON.stringify(configJson, null, 2);
						frm.refresh_field("stages");
						frm.dirty();

						group.stages.push(row);
						_updateDialogTitle();
						render();

						frm.script_manager.trigger("edit_content_btn", row.doctype, row.name);
					}

					if (stageType === "QUESTION") {
						// Fetch existing Review Item data for this item_id
						frappe.call({
							method: "frappe.client.get",
							args: { doctype: "Memora Review Item", name: group.item_id },
							async: true,
							callback: function (r) {
								let config = { item_id: group.item_id };
								if (r && r.message) {
									let ri = r.message;
									config.question = ri.question_text || "";
									config.instruction = "اختر الإجابة الصحيحة";
									let choices = [ri.choice_1, ri.choice_2, ri.choice_3, ri.choice_4].filter(Boolean);
									config.answers = choices.map((text, i) => ({
										text: text,
										is_correct: i + 1 === ri.correct_choice,
										item_id: generateItemUUID(),
									}));
								}
								_addStageRow(config);
							},
							error: function () {
								// Review Item not found — open with empty config
								_addStageRow({ item_id: group.item_id });
							},
						});
					} else {
						_addStageRow({ item_id: group.item_id });
					}
				},
				"إضافة مرحلة",
				"إضافة"
			);
		});

		// Init Sortable.js for drag-to-reorder
		let listEl = $wrapper.find(".lm-items-list")[0];
		if (listEl && window.Sortable) {
			new Sortable(listEl, {
				animation: 150,
				handle: ".lm-drag-handle",
				ghostClass: "lm-sortable-ghost",
				onEnd: function (evt) {
					let moved = groups.splice(evt.oldIndex, 1)[0];
					groups.splice(evt.newIndex, 0, moved);
					render();
				},
			});
		}
	}

	// Add ghost class styling
	let styleEl = document.createElement("style");
	styleEl.textContent = `
		.lm-sortable-ghost {
			opacity: 0.4;
			background: #e3f2fd !important;
			border: 2px dashed #1976d2 !important;
		}
		.lm-item-card:hover {
			box-shadow: 0 2px 8px rgba(0,0,0,0.08);
		}
	`;
	document.head.appendChild(styleEl);
	d.onhide = () => styleEl.remove();

	d.show();
	d.$wrapper.find(".modal-dialog").css("max-width", "800px");

	// Add standalone button to dialog footer (left side)
	let $footer = d.$wrapper.find(".modal-footer");
	$footer.css({ display: "flex", "justify-content": "space-between", "align-items": "center" });
	let $standaloneBtn = $(`<button class="btn btn-xs btn-default"
		style="color:#888;border-style:dashed;">+ إضافة مرحلة مستقلة (توصيل / خريطة / قصة)</button>`);
	$footer.prepend($standaloneBtn);

	$standaloneBtn.on("click", function () {
		let standaloneTypes = [
			{ value: "MATCHING", label: STAGE_TYPE_COLORS.MATCHING.label },
			{ value: "MINDMAP", label: STAGE_TYPE_COLORS.MINDMAP.label },
			{ value: "STORY", label: STAGE_TYPE_COLORS.STORY.label },
		];
		let typeOptions = standaloneTypes.map((t) => `${t.value} — ${t.label}`).join("\n");

		frappe.prompt(
			{
				label: "نوع المرحلة",
				fieldname: "stage_type",
				fieldtype: "Select",
				options: typeOptions,
				reqd: 1,
			},
			function (values) {
				let stageType = values.stage_type.split(" — ")[0];
				let row = frm.add_child("stages");
				row.stage_type = stageType;
				row.config_json = "{}";
				frm.refresh_field("stages");
				frm.dirty();

				let newGroup = {
					item_id: null,
					key: `__standalone_${Date.now()}`,
					stages: [row],
					isCheckpoint: true,
				};
				groups.push(newGroup);
				_updateDialogTitle();
				render();

				frm.script_manager.trigger("edit_content_btn", row.doctype, row.name);
			},
			"إضافة مرحلة مستقلة",
			"إضافة"
		);
	});

	render();
}

function _applyChanges(frm, groups, deletedStageNames) {
	let changed = false;

	// Remove deleted stages from the child table
	if (deletedStageNames.size > 0) {
		let toRemove = frm.doc.stages.filter((s) => deletedStageNames.has(s.name));
		for (let row of toRemove) {
			frm.doc.stages = frm.doc.stages.filter((s) => s.name !== row.name);
		}
		changed = true;
	}

	// Flatten groups back to ordered stage names
	let newOrder = [];
	for (let g of groups) {
		for (let s of g.stages) {
			newOrder.push(s.name);
		}
	}

	// Update idx on each remaining child row
	for (let i = 0; i < newOrder.length; i++) {
		let row = frm.doc.stages.find((s) => s.name === newOrder[i]);
		if (row && row.idx !== i + 1) {
			row.idx = i + 1;
			changed = true;
		}
	}

	if (changed) {
		frm.doc.stages.sort((a, b) => a.idx - b.idx);
		frm.dirty();
		frm.refresh_field("stages");
		let msg = deletedStageNames.size > 0
			? `تم حذف ${deletedStageNames.size} مرحلة وتحديث الترتيب — اضغط حفظ لتأكيد`
			: "تم تحديث الترتيب — اضغط حفظ لتأكيد التغييرات";
		frappe.show_alert({ message: msg, indicator: "blue" });
	}
}

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
	// Build item groups from lesson stages (non-checkpoint groups with item_id)
	let stages = (frm.doc.stages || []).map((s) => ({ ...s }));
	let itemGroups = [];
	let seenIds = new Set();
	for (let s of stages) {
		if (s.stage_type === "MATCHING" || s.stage_type === "MINDMAP" || s.stage_type === "STORY") continue;
		let itemId = _extractItemId(s);
		if (!itemId || seenIds.has(itemId)) continue;
		seenIds.add(itemId);
		// Find all stages for this item to get best preview
		let groupStages = stages.filter((st) => {
			if (st.stage_type === "MATCHING" || st.stage_type === "MINDMAP" || st.stage_type === "STORY") return false;
			return _extractItemId(st) === itemId;
		});
		let infoStage = groupStages.find((st) => st.stage_type === "INFORMATION");
		let preview = _extractPreviewText(infoStage || groupStages[0]);
		itemGroups.push({ item_id: itemId, preview: preview, stages: groupStages });
	}

	// Build existing pairs map for pre-filling
	let existingPairs = {};
	for (let p of data.pairs || []) {
		if (p.item_id) {
			existingPairs[p.item_id] = { right: p.right || "", left: p.left || "" };
		}
	}

	// State: which items are checked + their right/left values
	let pairState = {};
	for (let g of itemGroups) {
		let existing = existingPairs[g.item_id];
		pairState[g.item_id] = {
			checked: !!existing,
			right: existing ? existing.right : "",
			left: existing ? existing.left : "",
		};
	}

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
				fieldtype: "Section Break",
				label: "اختر العناصر وأدخل نص التوصيل لكل عنصر",
			},
			{
				fieldname: "items_html",
				fieldtype: "HTML",
			},
		],
		size: "extra-large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			// Sync inputs before saving
			_syncInputs();

			let selectedPairs = itemGroups
				.filter((g) => pairState[g.item_id].checked)
				.map((g) => pairState[g.item_id]);

			if (selectedPairs.length < 2) {
				frappe.msgprint("يجب اختيار عنصرين على الأقل للتوصيل.");
				return;
			}

			let emptyFields = selectedPairs.some((p) => !p.right.trim() || !p.left.trim());
			if (emptyFields) {
				frappe.msgprint("يجب تعبئة حقلي اليمين واليسار لكل عنصر محدد.");
				return;
			}

			let config_payload = {
				instruction: values.instruction,
				pairs: itemGroups
					.filter((g) => pairState[g.item_id].checked)
					.map((g, index) => ({
						id: String(index + 1),
						right: pairState[g.item_id].right.trim(),
						left: pairState[g.item_id].left.trim(),
						item_id: g.item_id,
					})),
			};

			frappe.model.set_value(cdt, cdn, "config_json", JSON.stringify(config_payload, null, 2));
			d.hide();
			frappe.show_alert({ message: "تم الحفظ", indicator: "green" });
		},
	});

	function _escapeHtml(str) {
		return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
	}

	function _syncInputs() {
		let $w = d.fields_dict.items_html.$wrapper;
		$w.find(".matching-item-row").each(function () {
			let id = $(this).data("item-id");
			if (!pairState[id]) return;
			pairState[id].checked = $(this).find(".matching-check").is(":checked");
			pairState[id].right = $(this).find(".matching-right").val() || "";
			pairState[id].left = $(this).find(".matching-left").val() || "";
		});
	}

	function _renderItems() {
		if (itemGroups.length === 0) {
			d.fields_dict.items_html.$wrapper.html(
				'<div style="padding:20px;text-align:center;color:#8d99a6;">لا توجد عناصر في هذا الدرس. أضف عناصر أولاً ثم أعد فتح التوصيل.</div>'
			);
			return;
		}

		_syncInputs();

		let html = "";
		itemGroups.forEach((g, i) => {
			let s = pairState[g.item_id];
			let preview = _escapeHtml(g.preview);
			if (preview.length > 80) preview = preview.slice(0, 80) + "…";
			let disabled = s.checked ? "" : "disabled";
			let opacity = s.checked ? "1" : "0.5";

			html += `<div class="matching-item-row" data-item-id="${g.item_id}"
				style="border:1px solid ${s.checked ? "#c62828" : "#e0e0e0"};border-radius:6px;
				padding:12px 14px;margin-bottom:8px;background:${s.checked ? "#fff5f5" : "#fafafa"};
				transition:all 0.15s;">
				<div style="display:flex;align-items:center;gap:10px;margin-bottom:${s.checked ? "10px" : "0"};">
					<input type="checkbox" class="matching-check" ${s.checked ? "checked" : ""}
						style="width:18px;height:18px;cursor:pointer;flex-shrink:0;">
					<span style="font-weight:600;color:#333;min-width:28px;">#${i + 1}</span>
					<span style="flex:1;direction:rtl;font-size:13px;color:#555;overflow:hidden;
						text-overflow:ellipsis;white-space:nowrap;">${preview || '<em style="color:#bbb;">بدون محتوى</em>'}</span>
				</div>
				${s.checked ? `<div style="display:flex;gap:10px;padding-right:36px;opacity:${opacity};">
					<div style="flex:1;">
						<div class="d-flex justify-content-between align-items-center mb-1">
							<label style="font-size:11px;font-weight:600;color:#000;display:block;">اليمين</label>
							<button type="button" class="btn btn-xs btn btn-primary matching-dir-toggle" data-target="right" data-item-id="${g.item_id}"
								style="font-size:10px;line-height:1;color:#fff;"
								title="تبديل اتجاه الكتابة">⇄</button>
						</div>
						<input type="text" class="matching-right form-control input-sm bg-white"
							value="${_escapeHtml(s.right)}" ${disabled}
							placeholder="المصطلح / الكلمة" style="direction:rtl;">
					</div>
					<div style="flex:1;">
						<div class="d-flex justify-content-between align-items-center mb-1">
							<label style="font-size:11px;font-weight:600;color:#000;display:block;">اليسار</label>
							<button type="button" class="btn btn-xs btn btn-primary matching-dir-toggle" data-target="left" data-item-id="${g.item_id}"
								style="font-size:10px;line-height:1;color:#fff;"
								title="تبديل اتجاه الكتابة">⇄</button>
						</div>
						<input type="text" class="matching-left form-control input-sm bg-white"
							value="${_escapeHtml(s.left)}" ${disabled}
							placeholder="التعريف / الشرح" style="direction:rtl;">
					</div>
				</div>` : ""}
			</div>`;
		});

		let $w = d.fields_dict.items_html.$wrapper;
		$w.html(html);

		// Toggle check → re-render to show/hide inputs
		$w.find(".matching-check").on("change", function () {
			_syncInputs();
			_renderItems();
		});

		// Toggle input direction RTL ↔ LTR
		$w.find(".matching-dir-toggle").on("click", function () {
			let target = $(this).data("target");
			let $input = $(this).closest(".matching-item-row").find(`.matching-${target}`);
			let current = $input.css("direction");
			$input.css("direction", current === "rtl" ? "ltr" : "rtl");
		});
	}

	d.show();
	_renderItems();
}

// =================================================
// 🔍 2. نافذة إعدادات الكشف (Reveal)
// =================================================
function open_reveal_dialog(frm, cdt, cdn, row, data, skipItemIds) {
	let _originalItemId = _getItemIdFromConfig(data, "REVEAL");
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
			if (!skipItemIds && _originalItemId) {
				config_payload.item_id = _originalItemId;
			}
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
	let _originalItemId = _getItemIdFromConfig(data, "SENTENCE_BUILDER");
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
			if (!skipItemIds && _originalItemId) {
				config_payload.item_id = _originalItemId;
			}

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
	let _originalItemId = _getItemIdFromConfig(data, "MINDMAP");

	// Build tree state from saved data
	let branches = [];
	if (data.children && Array.isArray(data.children)) {
		for (let branch of data.children) {
			let items = [];
			if (branch.children && Array.isArray(branch.children)) {
				for (let item of branch.children) {
					items.push({
						_key: `item_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
						label: item.label || "",
						description: item.description || "",
						item_id: item.item_id || null,
					});
				}
			}
			branches.push({
				_key: `br_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
				label: branch.label || "",
				description: branch.description || "",
				item_id: branch.item_id || null,
				expanded: true,
				items: items,
			});
		}
	}

	let editingNode = null; // { branchIdx, itemIdx? } — tracks which node's inline form is open

	let d = new frappe.ui.Dialog({
		title: "إعدادات الخريطة الذهنية (Mind Map)",
		fields: [
			{
				label: "عنوان الخريطة (العنوان الرئيسي)",
				fieldname: "root_label",
				fieldtype: "Data",
				reqd: 1,
				default: data.label || "",
			},
			{
				label: "وصف الخريطة",
				fieldname: "root_description",
				fieldtype: "Small Text",
				default: data.description || "",
			},
			{
				fieldtype: "Section Break",
				label: "محتوى الخريطة",
			},
			{
				fieldname: "tree_html",
				fieldtype: "HTML",
			},
		],
		size: "extra-large",
		primary_action_label: "حفظ (Save)",
		primary_action: function (values) {
			if (branches.length === 0) {
				frappe.msgprint("يجب إضافة فرع واحد على الأقل.");
				return;
			}
			for (let br of branches) {
				if (!br.label.trim()) {
					frappe.msgprint("يوجد فرع بدون عنوان. يرجى تعبئة جميع العناوين.");
					return;
				}
				for (let it of br.items) {
					if (!it.label.trim()) {
						frappe.msgprint(`يوجد عنصر بدون عنوان تحت الفرع "${br.label}". يرجى تعبئة جميع العناوين.`);
						return;
					}
				}
			}

			let used_ids = new Set();
			let children = branches.map((br) => {
				let id = _generate_mindmap_id(used_ids);
				used_ids.add(id);
				let branchObj = { id: id, label: br.label, children: [] };
				if (!skipItemIds) branchObj.item_id = br.item_id || generateItemUUID();
				if (br.description) branchObj.description = br.description;
				for (let it of br.items) {
					let iid = _generate_mindmap_id(used_ids);
					used_ids.add(iid);
					let itemObj = { id: iid, label: it.label };
					if (!skipItemIds) itemObj.item_id = it.item_id || generateItemUUID();
					if (it.description) itemObj.description = it.description;
					branchObj.children.push(itemObj);
				}
				return branchObj;
			});

			let config_payload = { label: values.root_label, children: children };
			if (values.root_description) config_payload.description = values.root_description;
			if (!skipItemIds && _originalItemId) config_payload.item_id = _originalItemId;

			frappe.model.set_value(cdt, cdn, "config_json", JSON.stringify(config_payload, null, 2));
			d.hide();
			frappe.show_alert({ message: "تم حفظ الخريطة الذهنية", indicator: "green" });
		},
	});

	function _esc(str) {
		return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
	}

	function _renderTree() {
		let $w = d.fields_dict.tree_html.$wrapper;
		let scrollTop = $w.find(".mm-container").scrollTop() || 0;

		let html = '<div class="mm-container" style="max-height:55vh;overflow-y:auto;padding:4px;">';

		if (branches.length === 0) {
			html += `<div style="text-align:center;padding:40px 20px;color:#8d99a6;">
				<div style="font-size:40px;margin-bottom:12px;">🗺️</div>
				<div style="font-size:14px;margin-bottom:16px;">لا توجد فروع بعد. أضف فرعاً لبدء بناء الخريطة الذهنية.</div>
			</div>`;
		}

		html += '<div class="mm-branches-list">';
		branches.forEach((br, bi) => {
			let chevron = br.expanded ? "▼" : "▶";
			let itemCount = br.items.length;
			let countBadge = itemCount > 0
				? `<span style="background:#e8f5e9;color:#2e7d32;font-size:10px;padding:1px 7px;border-radius:10px;margin-right:6px;">${itemCount} عنصر</span>`
				: "";

			html += `<div class="mm-branch-card" data-branch-idx="${bi}" style="background:#fff;border:1.5px solid #558b2f;
				border-radius:8px;margin-bottom:10px;overflow:hidden;">
				<div class="mm-branch-header" data-branch-idx="${bi}" style="display:flex;align-items:center;gap:8px;
					padding:10px 14px;background:#f1f8e9;cursor:pointer;user-select:none;">
					<span class="mm-branch-drag" style="cursor:grab;color:#8bc34a;font-size:18px;flex-shrink:0;">⠿</span>
					<span style="font-size:13px;color:#558b2f;flex-shrink:0;width:18px;text-align:center;">${chevron}</span>
					<span style="font-weight:700;color:#33691e;font-size:14px;flex:1;direction:rtl;overflow:hidden;
						text-overflow:ellipsis;white-space:nowrap;">${_esc(br.label) || '<em style="color:#aaa;">فرع بدون عنوان</em>'}</span>
					${countBadge}
					<button class="btn btn-xs btn-default mm-edit-branch-btn" data-branch-idx="${bi}"
						title="تعديل الفرع" style="flex-shrink:0;">✏️</button>
					<button class="btn btn-xs mm-delete-branch-btn" data-branch-idx="${bi}"
						title="حذف الفرع" style="color:#c0392b;border-color:#e6b0aa;flex-shrink:0;">✕</button>
				</div>`;

			// Inline edit form for branch
			if (editingNode && editingNode.branchIdx === bi && editingNode.itemIdx === undefined) {
				html += `<div class="mm-edit-form" style="padding:12px 14px;background:#f9fbe7;border-top:1px solid #dce775;">
					<div style="margin-bottom:8px;">
						<label style="font-size:11px;font-weight:600;color:#33691e;display:block;margin-bottom:3px;">العنوان</label>
						<input type="text" class="form-control input-sm bg-white mm-edit-label" value="${_esc(br.label)}"
							style="direction:rtl;" placeholder="عنوان الفرع">
					</div>
					<div style="margin-bottom:10px;">
						<label style="font-size:11px;font-weight:600;color:#33691e;display:block;margin-bottom:3px;">الوصف (اختياري)</label>
						<textarea class="form-control input-sm bg-white mm-edit-desc" rows="2"
							style="direction:rtl;resize:vertical;height:80px;" placeholder="وصف الفرع">${_esc(br.description)}</textarea>
					</div>
					<div style="display:flex;gap:6px;justify-content:flex-end;">
						<button class="btn btn-xs btn-default mm-edit-cancel">إلغاء</button>
						<button class="btn btn-xs btn-primary mm-edit-save" data-branch-idx="${bi}">تأكيد</button>
					</div>
				</div>`;
			}

			if (br.expanded) {
				html += `<div class="mm-items-list" data-branch-idx="${bi}" style="padding:6px 14px 6px 14px;
					border-top:1px solid #c5e1a5;min-height:32px;">`;

				if (br.items.length === 0) {
					html += `<div class="mm-empty-items" style="text-align:center;padding:12px;color:#aaa;font-size:12px;">
						لا توجد عناصر — اسحب عنصراً هنا أو اضغط الزر أدناه
					</div>`;
				}

				br.items.forEach((item, ii) => {
					html += `<div class="mm-item-row" data-branch-idx="${bi}" data-item-idx="${ii}"
						style="display:flex;align-items:center;gap:8px;padding:7px 10px;margin-bottom:4px;
						background:#fff;border:1px solid #e0e0e0;border-radius:5px;border-right:3px solid #8bc34a;">
						<span class="mm-item-drag" style="cursor:grab;color:#bbb;font-size:14px;flex-shrink:0;">⠿</span>
						<span style="flex:1;font-size:13px;color:#333;direction:rtl;overflow:hidden;
							text-overflow:ellipsis;white-space:nowrap;">${_esc(item.label) || '<em style="color:#ccc;">عنصر بدون عنوان</em>'}</span>`;

					if (item.description) {
						html += `<span style="font-size:11px;color:#999;max-width:200px;overflow:hidden;
							text-overflow:ellipsis;white-space:nowrap;direction:rtl;" title="${_esc(item.description)}">${_esc(item.description)}</span>`;
					}

					html += `<button class="btn btn-xs btn-default mm-edit-item-btn" data-branch-idx="${bi}" data-item-idx="${ii}"
							title="تعديل" style="flex-shrink:0;">✏️</button>
						<button class="btn btn-xs mm-delete-item-btn" data-branch-idx="${bi}" data-item-idx="${ii}"
							title="حذف" style="color:#c0392b;border-color:#e6b0aa;flex-shrink:0;">✕</button>
					</div>`;

					// Inline edit form for item
					if (editingNode && editingNode.branchIdx === bi && editingNode.itemIdx === ii) {
						html += `<div class="mm-edit-form" style="padding:10px 12px;margin-bottom:4px;background:#f5f5f5;
							border:1px solid #e0e0e0;border-radius:5px;">
							<div style="margin-bottom:8px;">
								<label style="font-size:11px;font-weight:600;color:#555;display:block;margin-bottom:3px;">العنوان</label>
								<input type="text" class="form-control input-sm bg-white mm-edit-label" value="${_esc(item.label)}"
									style="direction:rtl;" placeholder="عنوان العنصر">
							</div>
							<div style="margin-bottom:10px;">
								<label style="font-size:11px;font-weight:600;color:#555;display:block;margin-bottom:3px;">الوصف (اختياري)</label>
								<textarea class="form-control input-sm bg-white mm-edit-desc" rows="2"
									style="direction:rtl;resize:vertical;height:80px;" placeholder="وصف العنصر">${_esc(item.description)}</textarea>
							</div>
							<div style="display:flex;gap:6px;justify-content:flex-end;">
								<button class="btn btn-xs btn-default mm-edit-cancel">إلغاء</button>
								<button class="btn btn-xs btn-primary mm-edit-save" data-branch-idx="${bi}" data-item-idx="${ii}">تأكيد</button>
							</div>
						</div>`;
					}
				});

				html += `<button class="btn btn-xs btn-default mm-add-item-btn" data-branch-idx="${bi}"
					style="width:100%;margin-top:4px;color:#8bc34a;border-style:dashed;border-color:#c5e1a5;">
					+ إضافة عنصر</button>`;
				html += "</div>";
			}

			html += "</div>";
		});
		html += "</div>";

		// Add branch button
		html += `<button class="btn btn-sm btn-default mm-add-branch-btn"
			style="width:100%;margin-top:6px;color:#558b2f;border:2px dashed #a5d6a7;border-radius:8px;
			padding:10px;font-weight:600;font-size:13px;">
			+ إضافة فرع جديد</button>`;

		html += "</div>";

		$w.html(html);
		$w.find(".mm-container").scrollTop(scrollTop);

		// --- Event bindings ---

		// Toggle expand/collapse
		$w.find(".mm-branch-header").on("click", function (e) {
			if ($(e.target).closest(".mm-edit-branch-btn, .mm-delete-branch-btn").length) return;
			let bi = $(this).data("branch-idx");
			branches[bi].expanded = !branches[bi].expanded;
			editingNode = null;
			_renderTree();
		});

		// Edit branch
		$w.find(".mm-edit-branch-btn").on("click", function (e) {
			e.stopPropagation();
			let bi = $(this).data("branch-idx");
			editingNode = { branchIdx: bi };
			branches[bi].expanded = true;
			_renderTree();
			$w.find(".mm-edit-form .mm-edit-label").first().focus();
		});

		// Edit item
		$w.find(".mm-edit-item-btn").on("click", function (e) {
			e.stopPropagation();
			let bi = $(this).data("branch-idx");
			let ii = $(this).data("item-idx");
			editingNode = { branchIdx: bi, itemIdx: ii };
			_renderTree();
			$w.find(".mm-edit-form .mm-edit-label").first().focus();
		});

		// Save edit
		$w.find(".mm-edit-save").on("click", function () {
			let bi = $(this).data("branch-idx");
			let ii = $(this).data("item-idx");
			let $form = $(this).closest(".mm-edit-form");
			let newLabel = $form.find(".mm-edit-label").val().trim();
			let newDesc = $form.find(".mm-edit-desc").val().trim();
			if (ii !== undefined && ii !== "") {
				branches[bi].items[ii].label = newLabel;
				branches[bi].items[ii].description = newDesc;
			} else {
				branches[bi].label = newLabel;
				branches[bi].description = newDesc;
			}
			editingNode = null;
			_renderTree();
		});

		// Cancel edit
		$w.find(".mm-edit-cancel").on("click", function () {
			editingNode = null;
			_renderTree();
		});

		// Delete branch
		$w.find(".mm-delete-branch-btn").on("click", function (e) {
			e.stopPropagation();
			let bi = $(this).data("branch-idx");
			let br = branches[bi];
			let msg = br.items.length > 0
				? `هل تريد حذف الفرع "${br.label}" وجميع عناصره (${br.items.length})؟`
				: `هل تريد حذف الفرع "${br.label}"؟`;
			frappe.confirm(msg, () => {
				branches.splice(bi, 1);
				editingNode = null;
				_renderTree();
			});
		});

		// Delete item
		$w.find(".mm-delete-item-btn").on("click", function (e) {
			e.stopPropagation();
			let bi = $(this).data("branch-idx");
			let ii = $(this).data("item-idx");
			branches[bi].items.splice(ii, 1);
			editingNode = null;
			_renderTree();
		});

		// Add item (opens inline form for new item)
		$w.find(".mm-add-item-btn").on("click", function () {
			let bi = $(this).data("branch-idx");
			branches[bi].items.push({
				_key: `item_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
				label: "",
				description: "",
				item_id: null,
			});
			editingNode = { branchIdx: bi, itemIdx: branches[bi].items.length - 1 };
			_renderTree();
			$w.find(".mm-edit-form .mm-edit-label").last().focus();
		});

		// Add branch
		$w.find(".mm-add-branch-btn").on("click", function () {
			branches.push({
				_key: `br_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
				label: "",
				description: "",
				item_id: null,
				expanded: true,
				items: [],
			});
			editingNode = { branchIdx: branches.length - 1 };
			_renderTree();
			$w.find(".mm-edit-form .mm-edit-label").last().focus();
		});

		// Keyboard: Enter to save, Escape to cancel in edit forms
		$w.find(".mm-edit-form").on("keydown", function (e) {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				$(this).find(".mm-edit-save").click();
			} else if (e.key === "Escape") {
				e.preventDefault();
				// If newly added with empty label, remove it
				if (editingNode) {
					let bi = editingNode.branchIdx;
					let ii = editingNode.itemIdx;
					if (ii !== undefined) {
						if (!branches[bi].items[ii].label.trim()) branches[bi].items.splice(ii, 1);
					} else {
						if (!branches[bi].label.trim()) branches.splice(bi, 1);
					}
				}
				editingNode = null;
				_renderTree();
			}
		});

		// Init Sortable on branches list
		let branchListEl = $w.find(".mm-branches-list")[0];
		if (branchListEl && window.Sortable) {
			new Sortable(branchListEl, {
				animation: 150,
				handle: ".mm-branch-drag",
				ghostClass: "mm-sortable-ghost",
				onEnd: function (evt) {
					let moved = branches.splice(evt.oldIndex, 1)[0];
					branches.splice(evt.newIndex, 0, moved);
					editingNode = null;
					_renderTree();
				},
			});
		}

		// Init Sortable on each branch's items list
		$w.find(".mm-items-list").each(function () {
			let bi = $(this).data("branch-idx");
			let el = this;
			if (window.Sortable) {
				new Sortable(el, {
					animation: 150,
					handle: ".mm-item-drag",
					ghostClass: "mm-sortable-ghost",
					group: "mm-items",
					draggable: ".mm-item-row",
					onEnd: function (evt) {
						let fromBi = parseInt(evt.from.dataset.branchIdx);
						let toBi = parseInt(evt.to.dataset.branchIdx);
						let item = branches[fromBi].items.splice(evt.oldIndex, 1)[0];
						branches[toBi].items.splice(evt.newIndex, 0, item);
						editingNode = null;
						_renderTree();
					},
				});
			}
		});
	}

	// Add styles for the mindmap editor
	let mmStyleEl = document.createElement("style");
	mmStyleEl.textContent = `
		.mm-sortable-ghost {
			opacity: 0.4;
			background: #e8f5e9 !important;
			border: 2px dashed #4caf50 !important;
		}
		.mm-branch-card:hover {
			box-shadow: 0 2px 8px rgba(0,0,0,0.08);
		}
		.mm-item-row:hover {
			background: #fafffe !important;
			border-color: #a5d6a7 !important;
		}
		.mm-edit-form input:focus, .mm-edit-form textarea:focus {
			border-color: #8bc34a;
			box-shadow: 0 0 0 2px rgba(139,195,74,0.2);
		}
		[data-fieldname="root_description"] textarea {
			height: 80px !important;
			min-height: 80px !important;
		}
	`;
	document.head.appendChild(mmStyleEl);
	d.onhide = () => mmStyleEl.remove();

	d.show();
	d.$wrapper.find(".modal-dialog").css("max-width", "700px");
	_renderTree();
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
	let _originalItemId = _getItemIdFromConfig(data, "QUESTION");
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
			if (!skipItemIds && _originalItemId) {
				config_payload.item_id = _originalItemId;
			}

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
	let _originalItemId = _getItemIdFromConfig(data, "INFORMATION");
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
			if (!skipItemIds && _originalItemId) {
				config_payload.item_id = _originalItemId;
			}

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
	let _originalItemId = _getItemIdFromConfig(data, "FILL_BLANK");
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
			if (!skipItemIds && _originalItemId) {
				config_payload.item_id = _originalItemId;
			}

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
	let _originalItemId = _getItemIdFromConfig(data, "STORY");
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
			  '<span style="font-size:11px;margin-top:3px;color:#b0bec5;">PNG، JPG، GIF، WebP — حد أقصى 2MB</span>' +
			  '<input class="story-file-input" type="file" accept="image/png,image/jpeg,image/gif,image/webp" style="display:none;">' +
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
			let allowedTypes = ["image/png", "image/jpeg", "image/gif", "image/webp"];
			if (!allowedTypes.includes(file.type)) {
				frappe.msgprint("نوع الملف غير مدعوم. الأنواع المسموحة: PNG، JPG، GIF، WebP");
				e.target.value = "";
				return;
			}
			if (file.size > 2 * 1024 * 1024) {
				frappe.msgprint("حجم الصورة يتجاوز 2MB. يرجى اختيار صورة أصغر.");
				e.target.value = "";
				return;
			}
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
			if (!skipItemIds && _originalItemId) {
				config_payload.item_id = _originalItemId;
			}

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
