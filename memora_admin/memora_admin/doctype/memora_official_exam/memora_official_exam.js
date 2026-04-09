// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Official Exam", {
	refresh(frm) {
		// Import Questions button
		frm.add_custom_button(__("Import Questions"), function () {
			let d = new frappe.ui.Dialog({
				title: __("Import Questions from Excel"),
				fields: [
					{
						fieldname: "excel_file",
						fieldtype: "HTML",
						options: `<div style="margin: 8px 0;">
							<a href="/assets/memora_admin/files/exam_questions_template.xlsx"
							   download style="font-size: 12px;">
								&#11015; Download Template
							</a>
							<br><br>
							<input type="file" accept=".xlsx" class="excel-file-input">
						</div>`,
					},
					{
						fieldname: "mode",
						fieldtype: "Select",
						label: __("Import Mode"),
						options: "Append\nReplace",
						default: "Append",
					},
				],
				primary_action_label: __("Import"),
				primary_action(values) {
					let fileInput = d.$wrapper.find(".excel-file-input")[0];
					let file = fileInput && fileInput.files[0];
					if (!file) {
						frappe.msgprint(__("Please select an Excel file."));
						return;
					}

					let reader = new FileReader();
					reader.onload = function (e) {
						let base64 = e.target.result.split(",")[1];
						frappe.call({
							method: "memora_admin.memora_admin.doctype.memora_official_exam.memora_official_exam.import_questions_from_excel",
							args: { file_content: base64 },
							btn: d.get_primary_btn(),
							callback(r) {
								if (r.message && r.message.length) {
									if (values.mode === "Replace") {
										frm.clear_table("questions");
									}
									r.message.forEach(function (q) {
										let row = frm.add_child("questions");
										row.question_text = q.question_text;
										row.choice_1 = q.choice_1;
										row.choice_2 = q.choice_2;
										row.choice_3 = q.choice_3 || "";
										row.choice_4 = q.choice_4 || "";
										row.correct_choice = q.correct_choice;
									});
									frm.refresh_field("questions");
									frappe.msgprint(
										__("{0} questions imported.", [r.message.length])
									);
									d.hide();
									frm.dirty();
								} else {
									frappe.msgprint(__("No questions found in the file."));
								}
							},
						});
					};
					reader.readAsDataURL(file);
				},
			});
			d.show();
		});
	},
});
