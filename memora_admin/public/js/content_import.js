/**
 * Content Import Modal for Memora Topic
 *
 * 4-step wizard: Upload → Review Questions → Split Lessons → Confirm & Import
 * Loaded via doctype_js in hooks.py for Memora Topic.
 */

function generateImportUUID() {
	if (typeof crypto !== "undefined" && crypto.randomUUID) {
		return crypto.randomUUID();
	}
	return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
		var r = (Math.random() * 16) | 0;
		var v = c === "x" ? r : (r & 0x3) | 0x8;
		return v.toString(16);
	});
}

frappe.ui.form.on("Memora Topic", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("استيراد محتوى"),
				() => new ContentImportModal(frm),
				__("Actions")
			);
		}
	},
});

class ContentImportModal {
	constructor(frm) {
		this.frm = frm;
		this.topic_name = frm.doc.name;
		this.step = 1;
		this.state = {
			mode: "add",
			lessons: [],
			id_to_uuid: {},
			current_lesson: 0,
			current_question: 0,
			reviewed_lessons: new Set(),
		};
		this.make_dialog();
	}

	make_dialog() {
		this.dialog = new frappe.ui.Dialog({
			title: "استيراد محتوى — " + this.topic_name,
			size: "extra-large",
			minimizable: false,
		});
		this.dialog.$wrapper.find(".modal-dialog").addClass("content-import-modal");
		this.dialog.set_primary_action(__("التالي"), () => this.next_step());
		this.dialog.set_secondary_action_label(__("السابق"));
		this.dialog.set_secondary_action(() => this.prev_step());

		this.render();
		this.dialog.show();
	}

	render() {
		const $body = $(this.dialog.body);
		$body.empty();

		// Progress bar
		$body.append(this.render_progress_bar());

		// Step content
		const $content = $('<div class="ci-step-content"></div>');
		$body.append($content);

		switch (this.step) {
			case 1:
				this.render_step_upload($content);
				break;
			case 2:
				this.render_step_review($content);
				break;
			case 3:
				this.render_step_split($content);
				break;
			case 4:
				this.render_step_confirm($content);
				break;
		}

		this.update_buttons();
	}

	render_progress_bar() {
		const steps = [
			{ num: 1, label: "رفع الملف" },
			{ num: 2, label: "مراجعة الأسئلة" },
			{ num: 3, label: "تقسيم الدروس" },
			{ num: 4, label: "تأكيد واستيراد" },
		];
		let html = '<div class="ci-progress-bar">';
		for (const s of steps) {
			const cls =
				s.num === this.step
					? "ci-step active"
					: s.num < this.step
					? "ci-step done"
					: "ci-step";
			html += `<div class="${cls}">
				<span class="ci-step-num">${s.num}</span>
				<span class="ci-step-label">${s.label}</span>
			</div>`;
		}
		html += "</div>";
		return html;
	}

	update_buttons() {
		const $secondary = this.dialog.$wrapper
			.find(
				".btn-secondary-dark, .btn-secondary, .modal-footer .btn:not(.btn-primary):not(.btn-primary-dark)"
			)
			.first();

		if (this.step === 1) {
			$secondary.hide();
		} else {
			$secondary.show();
		}

		const $primary = this.dialog.$wrapper.find(".btn-primary, .btn-primary-dark").first();
		if (this.step === 4) {
			$primary.text(__("استيراد"));
		} else {
			$primary.text(__("التالي"));
		}
	}

	next_step() {
		if (this.step === 1 && !this.state.lessons.length) {
			frappe.msgprint(__("يرجى رفع ملف JSON صالح أولاً"));
			return;
		}
		if (this.step === 4) {
			this.do_import();
			return;
		}
		if (this.step < 4) {
			this.step++;
			this.render();
		}
	}

	prev_step() {
		if (this.step > 1) {
			this.step--;
			this.render();
		}
	}

	// =========================================================================
	// Step 1: Upload
	// =========================================================================

	render_step_upload($container) {
		const state = this.state;
		let html = `
			<div class="ci-upload-step">
				<div class="ci-mode-select">
					<label class="ci-radio-label">
						<input type="radio" name="ci-mode" value="add" ${state.mode === "add" ? "checked" : ""}>
						<span>إضافة (Add)</span>
					</label>
					<label class="ci-radio-label">
						<input type="radio" name="ci-mode" value="replace" ${state.mode === "replace" ? "checked" : ""}>
						<span>استبدال (Replace)</span>
					</label>
				</div>
				<div class="ci-dropzone">
					<input type="file" accept=".json" class="ci-file-input" style="display:none">
					<div class="ci-dropzone-label">
						<i class="fa fa-cloud-upload"></i>
						<p>اضغط أو اسحب ملف JSON هنا</p>
					</div>
				</div>
				<div class="ci-upload-status"></div>
				<div class="ci-validation-errors"></div>
				<div class="ci-validation-warnings"></div>
			</div>
		`;
		$container.html(html);

		// Mode radio
		$container.find('input[name="ci-mode"]').on("change", (e) => {
			state.mode = e.target.value;
		});

		// File input
		const $dropzone = $container.find(".ci-dropzone");
		const $fileInput = $container.find(".ci-file-input");

		$dropzone.on("click", () => $fileInput.trigger("click"));
		$dropzone.on("dragover", (e) => {
			e.preventDefault();
			$dropzone.addClass("ci-dragover");
		});
		$dropzone.on("dragleave", () => $dropzone.removeClass("ci-dragover"));
		$dropzone.on("drop", (e) => {
			e.preventDefault();
			$dropzone.removeClass("ci-dragover");
			const files = e.originalEvent.dataTransfer.files;
			if (files.length) this.handle_file(files[0], $container);
		});
		$fileInput.on("change", (e) => {
			if (e.target.files.length) this.handle_file(e.target.files[0], $container);
		});

		// Show current file info if already loaded
		if (state.lessons.length) {
			const total_q = state.lessons.reduce((s, l) => s + l.questions.length, 0);
			$container.find(".ci-upload-status").html(
				`<div class="ci-success-badge">
					<i class="fa fa-check-circle"></i>
					تم تحميل ${state.lessons.length} درس و ${total_q} سؤال
				</div>`
			);
		}
	}

	handle_file(file, $container) {
		const $status = $container.find(".ci-upload-status");
		const $errors = $container.find(".ci-validation-errors");
		const $warnings = $container.find(".ci-validation-warnings");

		$status.html(
			'<div class="ci-loading"><i class="fa fa-spinner fa-spin"></i> جاري التحقق...</div>'
		);
		$errors.empty();
		$warnings.empty();

		const reader = new FileReader();
		reader.onload = (e) => {
			const json_data = e.target.result;
			frappe.call({
				method: "memora_admin.api.content_import.validate_import_json",
				args: { topic_name: this.topic_name, json_data },
				callback: (r) => {
					const result = r.message;
					if (result.success) {
						this.state.lessons = result.lessons;
						// Generate UUIDs for all question IDs
						this.state.id_to_uuid = {};
						for (const lesson of result.lessons) {
							for (const q of lesson.questions) {
								const key = String(q.id);
								if (!this.state.id_to_uuid[key]) {
									this.state.id_to_uuid[key] = generateImportUUID();
								}
							}
						}
						this.state.current_lesson = 0;
						this.state.current_question = 0;
						this.state.reviewed_lessons = new Set();

						const total_q = result.lessons.reduce((s, l) => s + l.questions.length, 0);
						$status.html(
							`<div class="ci-success-badge">
								<i class="fa fa-check-circle"></i>
								تم تحميل ${result.lessons.length} درس و ${total_q} سؤال
							</div>`
						);
					} else {
						$status.html(
							'<div class="ci-error-badge"><i class="fa fa-times-circle"></i> فشل التحقق</div>'
						);
						this.state.lessons = [];
					}

					if (result.errors && result.errors.length) {
						$errors.html(
							'<div class="ci-error-list"><strong>أخطاء:</strong><ul>' +
								result.errors
									.map((e) => `<li>${frappe.utils.escape_html(e)}</li>`)
									.join("") +
								"</ul></div>"
						);
					}
					if (result.warnings && result.warnings.length) {
						$warnings.html(
							'<div class="ci-warning-list"><strong>تحذيرات:</strong><ul>' +
								result.warnings
									.map((w) => `<li>${frappe.utils.escape_html(w)}</li>`)
									.join("") +
								"</ul></div>"
						);
					}
				},
				error: () => {
					$status.html(
						'<div class="ci-error-badge"><i class="fa fa-times-circle"></i> خطأ في الاتصال</div>'
					);
				},
			});
		};
		reader.readAsText(file);
	}

	// =========================================================================
	// Step 2: Review Questions
	// =========================================================================

	render_step_review($container) {
		const state = this.state;
		const lessons = state.lessons;
		if (!lessons.length) return;

		// Lesson tabs
		let tabs_html = '<div class="ci-lesson-tabs">';
		for (let i = 0; i < lessons.length; i++) {
			const cls = i === state.current_lesson ? "ci-tab active" : "ci-tab";
			const reviewed = state.reviewed_lessons.has(i) ? " ci-reviewed" : "";
			tabs_html += `<button class="${cls}${reviewed}" data-idx="${i}">
				${frappe.utils.escape_html(lessons[i].title)}
				<span class="ci-badge">${lessons[i].questions.length}</span>
			</button>`;
		}
		tabs_html += "</div>";

		const lesson = lessons[state.current_lesson];
		const questions = lesson ? lesson.questions : [];
		const q_idx = state.current_question;
		const q = questions[q_idx];

		let editor_html = '<div class="ci-question-editor">';
		if (q) {
			const options = q.options || [];
			editor_html += `
				<div class="ci-q-header">
					<span class="ci-q-counter">${q_idx + 1} / ${questions.length}</span>
					<button class="btn btn-xs btn-danger ci-delete-q"><i class="fa fa-trash"></i> حذف السؤال</button>
				</div>
				<div class="ci-q-field">
					<label>نص السؤال</label>
					<textarea class="ci-q-text form-control" rows="3">${frappe.utils.escape_html(
						q.question || ""
					)}</textarea>
				</div>
				<div class="ci-q-options">`;
			for (let i = 0; i < 4; i++) {
				const val = i < options.length ? options[i] : "";
				const checked = q.correct_answer === i ? "checked" : "";
				editor_html += `
					<div class="ci-option-row ${q.correct_answer === i ? "ci-correct" : ""}">
						<input type="radio" name="ci-correct" value="${i}" ${checked}>
						<input type="text" class="form-control ci-option-input" data-idx="${i}"
							value="${frappe.utils.escape_html(val)}" placeholder="الخيار ${i + 1}">
					</div>`;
			}
			editor_html += "</div>";
		} else {
			editor_html += '<p class="text-muted text-center">لا توجد أسئلة في هذا الدرس</p>';
		}
		editor_html += "</div>";

		// Navigation
		const nav_html = `
			<div class="ci-q-nav">
				<button class="btn btn-default ci-q-prev" ${q_idx === 0 ? "disabled" : ""}>
					<i class="fa fa-arrow-right"></i> السابق
				</button>
				<button class="btn btn-default ci-q-next" ${q_idx >= questions.length - 1 ? "disabled" : ""}>
					التالي <i class="fa fa-arrow-left"></i>
				</button>
			</div>`;

		$container.html(tabs_html + editor_html + nav_html);

		// Mark current lesson as reviewed
		state.reviewed_lessons.add(state.current_lesson);

		// Event: lesson tabs
		$container.find(".ci-tab").on("click", (e) => {
			state.current_lesson = parseInt($(e.currentTarget).data("idx"));
			state.current_question = 0;
			this.render_step_review($container);
		});

		// Event: question text edit
		$container.find(".ci-q-text").on("input", (e) => {
			if (q) q.question = e.target.value;
		});

		// Event: option text edit
		$container.find(".ci-option-input").on("input", (e) => {
			if (q) {
				const idx = parseInt($(e.target).data("idx"));
				while (q.options.length <= idx) q.options.push("");
				q.options[idx] = e.target.value;
			}
		});

		// Event: correct answer change
		$container.find('input[name="ci-correct"]').on("change", (e) => {
			if (q) {
				q.correct_answer = parseInt(e.target.value);
				// Update highlight
				$container.find(".ci-option-row").removeClass("ci-correct");
				$(e.target).closest(".ci-option-row").addClass("ci-correct");
			}
		});

		// Event: delete question
		$container.find(".ci-delete-q").on("click", () => {
			if (!q) return;
			frappe.confirm(__("هل تريد حذف هذا السؤال؟"), () => {
				const q_id = String(q.id);
				questions.splice(q_idx, 1);
				// Remove from id_to_uuid
				delete state.id_to_uuid[q_id];
				// Remove item_id references from stages
				this.remove_item_id_from_stages(lesson, q_id);
				// Adjust index
				if (state.current_question >= questions.length && questions.length > 0) {
					state.current_question = questions.length - 1;
				}
				this.render_step_review($container);
			});
		});

		// Event: prev/next question
		$container.find(".ci-q-prev").on("click", () => {
			if (state.current_question > 0) {
				state.current_question--;
				this.render_step_review($container);
			}
		});
		$container.find(".ci-q-next").on("click", () => {
			if (state.current_question < questions.length - 1) {
				state.current_question++;
				this.render_step_review($container);
			}
		});

		// Keyboard navigation
		this._review_keyhandler = (e) => {
			if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
			if (e.key === "ArrowLeft" || e.key === "Enter") {
				// Next question (RTL: left = forward)
				if (state.current_question < questions.length - 1) {
					state.current_question++;
					this.render_step_review($container);
				} else if (state.current_lesson < lessons.length - 1) {
					state.current_lesson++;
					state.current_question = 0;
					this.render_step_review($container);
				}
			} else if (e.key === "ArrowRight") {
				// Previous question (RTL: right = backward)
				if (state.current_question > 0) {
					state.current_question--;
					this.render_step_review($container);
				} else if (state.current_lesson > 0) {
					state.current_lesson--;
					state.current_question = lessons[state.current_lesson].questions.length - 1;
					this.render_step_review($container);
				}
			}
		};
		$(document).off("keydown.ci_review").on("keydown.ci_review", this._review_keyhandler);
	}

	remove_item_id_from_stages(lesson, q_id) {
		// Walk stages and remove references to this question ID
		for (const stage of lesson.stages || []) {
			let config = stage.config;
			if (typeof config === "string") {
				try {
					config = JSON.parse(config);
				} catch {
					continue;
				}
			}
			if (config) {
				stage.config = this._remove_id_refs(config, q_id);
			}
		}
	}

	_remove_id_refs(obj, target_id) {
		if (Array.isArray(obj)) {
			return obj
				.filter((item) => {
					if (
						typeof item === "object" &&
						item !== null &&
						String(item.item_id) === target_id
					) {
						return false;
					}
					return true;
				})
				.map((item) => this._remove_id_refs(item, target_id));
		}
		if (typeof obj === "object" && obj !== null) {
			const result = {};
			for (const [k, v] of Object.entries(obj)) {
				result[k] = this._remove_id_refs(v, target_id);
			}
			return result;
		}
		return obj;
	}

	// =========================================================================
	// Step 3: Split Lessons
	// =========================================================================

	render_step_split($container) {
		const state = this.state;
		const lessons = state.lessons;

		// Sidebar: lesson list
		let sidebar_html = '<div class="ci-split-sidebar">';
		for (let i = 0; i < lessons.length; i++) {
			const cls = i === state.current_lesson ? "ci-split-item active" : "ci-split-item";
			const qc = lessons[i].questions.length;
			const warn = qc > 15 ? ' <span class="ci-warn-badge">!' : "";
			sidebar_html += `<div class="${cls}" data-idx="${i}">
				<span class="ci-split-title">${frappe.utils.escape_html(lessons[i].title)}</span>
				<span class="ci-split-count">${qc}${warn}</span></span>
			</div>`;
		}
		sidebar_html += "</div>";

		// Main area: question table for current lesson
		const lesson = lessons[state.current_lesson];
		let main_html = '<div class="ci-split-main">';
		main_html += `<div class="ci-split-lesson-name">
			<label>اسم الدرس</label>
			<input type="text" class="form-control ci-lesson-name-input"
				value="${frappe.utils.escape_html(lesson.title)}">
		</div>`;
		main_html += '<div class="ci-split-questions" id="ci-sortable-list">';

		for (let i = 0; i < lesson.questions.length; i++) {
			const q = lesson.questions[i];
			const text = (q.question || "").substring(0, 80);
			main_html += `
				<div class="ci-split-row" data-q-idx="${i}">
					<span class="ci-drag-handle"><i class="fa fa-bars"></i></span>
					<span class="ci-split-q-num">${i + 1}</span>
					<span class="ci-split-q-text">${frappe.utils.escape_html(text)}</span>
				</div>`;
			// Split divider between rows
			if (i < lesson.questions.length - 1) {
				main_html += `<div class="ci-split-divider" data-after="${i}" title="اقسم هنا">
					<span class="ci-split-icon"><i class="fa fa-scissors"></i></span>
				</div>`;
			}
		}
		main_html += "</div></div>";

		$container.html('<div class="ci-split-layout">' + sidebar_html + main_html + "</div>");

		// Remove keyboard handler from step 2
		$(document).off("keydown.ci_review");

		// Sidebar click
		$container.find(".ci-split-item").on("click", (e) => {
			state.current_lesson = parseInt($(e.currentTarget).data("idx"));
			this.render_step_split($container);
		});

		// Lesson name edit
		$container.find(".ci-lesson-name-input").on("input", (e) => {
			lesson.title = e.target.value;
			$container
				.find(`.ci-split-item[data-idx="${state.current_lesson}"] .ci-split-title`)
				.text(e.target.value);
		});

		// Split divider click
		$container.find(".ci-split-divider").on("click", (e) => {
			const after_idx = parseInt($(e.currentTarget).data("after"));
			this.split_lesson_at(state.current_lesson, after_idx);
			this.render_step_split($container);
		});

		// Drag-and-drop reordering via Sortable (available in Frappe)
		const sortableEl = $container.find("#ci-sortable-list")[0];
		if (sortableEl && window.Sortable) {
			new Sortable(sortableEl, {
				handle: ".ci-drag-handle",
				animation: 150,
				filter: ".ci-split-divider",
				onEnd: (evt) => {
					// Reorder questions array
					const questions = lesson.questions;
					const moved = questions.splice(evt.oldIndex, 1)[0];
					questions.splice(evt.newIndex, 0, moved);
					this.render_step_split($container);
				},
			});
		}
	}

	split_lesson_at(lesson_idx, after_question_idx) {
		const lessons = this.state.lessons;
		const lesson = lessons[lesson_idx];
		const questions_before = lesson.questions.slice(0, after_question_idx + 1);
		const questions_after = lesson.questions.slice(after_question_idx + 1);

		if (!questions_after.length) return;

		// Determine which stages go where based on item_id majority
		const ids_before = new Set(questions_before.map((q) => String(q.id)));
		const ids_after = new Set(questions_after.map((q) => String(q.id)));
		const stages_before = [];
		const stages_after = [];

		for (const stage of lesson.stages || []) {
			const stage_ids = this._extract_item_ids(stage.config || stage);
			let count_before = 0;
			let count_after = 0;
			for (const sid of stage_ids) {
				if (ids_before.has(sid)) count_before++;
				if (ids_after.has(sid)) count_after++;
			}
			if (count_after > count_before) {
				stages_after.push(stage);
			} else {
				stages_before.push(stage);
			}
		}

		// Update current lesson
		lesson.questions = questions_before;
		lesson.stages = stages_before;

		// Insert new lesson after current
		const new_lesson = {
			title: lesson.title + " (2)",
			questions: questions_after,
			stages: stages_after,
		};
		lessons.splice(lesson_idx + 1, 0, new_lesson);
	}

	_extract_item_ids(obj) {
		const ids = [];
		if (Array.isArray(obj)) {
			for (const item of obj) ids.push(...this._extract_item_ids(item));
		} else if (typeof obj === "object" && obj !== null) {
			for (const [k, v] of Object.entries(obj)) {
				if (k === "item_id") ids.push(String(v));
				else ids.push(...this._extract_item_ids(v));
			}
		}
		return ids;
	}

	// =========================================================================
	// Step 4: Confirm & Import
	// =========================================================================

	render_step_confirm($container) {
		$(document).off("keydown.ci_review");
		const state = this.state;
		const lessons = state.lessons;
		const total_q = lessons.reduce((s, l) => s + l.questions.length, 0);
		const total_s = lessons.reduce((s, l) => s + (l.stages || []).length, 0);

		let html = '<div class="ci-confirm-step">';

		// Mode warning
		if (state.mode === "replace") {
			html += `<div class="ci-replace-warning">
				<i class="fa fa-exclamation-triangle"></i>
				<strong>وضع الاستبدال:</strong> سيتم حذف جميع الدروس الحالية لهذا الموضوع واستبدالها بالمحتوى الجديد
			</div>`;
		}

		html += `
			<div class="ci-confirm-summary">
				<h4>ملخص الاستيراد</h4>
				<table class="table ci-confirm-table">
					<tr><td>الموضوع</td><td><strong>${frappe.utils.escape_html(this.topic_name)}</strong></td></tr>
					<tr><td>عدد الدروس</td><td><strong>${lessons.length}</strong></td></tr>
					<tr><td>إجمالي المراحل</td><td><strong>${total_s}</strong></td></tr>
					<tr><td>إجمالي الأسئلة</td><td><strong>${total_q}</strong></td></tr>
				</table>
			</div>

			<div class="ci-confirm-lessons">
				<h5>تفاصيل الدروس</h5>
				<table class="table table-bordered ci-detail-table">
					<thead><tr><th>#</th><th>اسم الدرس</th><th>المراحل</th><th>الأسئلة</th></tr></thead>
					<tbody>`;

		for (let i = 0; i < lessons.length; i++) {
			const l = lessons[i];
			html += `<tr>
				<td>${i + 1}</td>
				<td>${frappe.utils.escape_html(l.title)}</td>
				<td>${(l.stages || []).length}</td>
				<td>${l.questions.length}</td>
			</tr>`;
		}

		html += `</tbody></table></div>
			<div class="ci-import-result"></div>
		</div>`;

		$container.html(html);
	}

	// =========================================================================
	// Execute Import
	// =========================================================================

	do_import() {
		if (this.import_done) return;

		const $result = this.dialog.$wrapper.find(".ci-import-result");
		$result.html(
			'<div class="ci-loading"><i class="fa fa-spinner fa-spin"></i> جاري الاستيراد...</div>'
		);

		// Disable buttons during import
		const $primary = this.dialog.$wrapper.find(".btn-primary, .btn-primary-dark").first();
		const $secondary = this.dialog.$wrapper.find(
			".btn-secondary-dark, .btn-secondary, .modal-footer .btn:not(.btn-primary):not(.btn-primary-dark)"
		).first();
		$primary.prop("disabled", true);
		$secondary.hide();

		frappe.call({
			method: "memora_admin.api.content_import.execute_import",
			args: {
				topic_name: this.topic_name,
				lessons_json: JSON.stringify(this.state.lessons),
				id_to_uuid_json: JSON.stringify(this.state.id_to_uuid),
				mode: this.state.mode,
			},
			callback: (r) => {
				this.import_done = true;
				const result = r.message;
				$result.html(`
					<div class="ci-success-result">
						<i class="fa fa-check-circle"></i>
						<strong>تم الاستيراد بنجاح!</strong>
						<p>تم إنشاء ${result.lessons_created} درس، ${result.stages_created} مرحلة، ${result.review_items_created} سؤال مراجعة</p>
					</div>
				`);
				$primary.text(__("إغلاق")).prop("disabled", false);
				$primary.off("click").on("click", () => {
					this.dialog.hide();
					this.frm.reload_doc();
				});
			},
			error: () => {
				$result.html(
					'<div class="ci-error-badge"><i class="fa fa-times-circle"></i> فشل الاستيراد. يمكنك العودة وإعادة المحاولة.</div>'
				);
				$primary.prop("disabled", false);
				$secondary.show();
			},
		});
	}
}
