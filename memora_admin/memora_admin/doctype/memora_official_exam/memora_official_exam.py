# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

_EXCEL_REQUIRED_COLUMNS = {"question_text", "choice_1", "choice_2", "correct_choice"}


@frappe.whitelist()
def import_questions_from_excel(file_content):
	"""Parse an .xlsx file sent as base64 and return a list of question dicts for the child table.

	Expected header row: question_text, choice_1, choice_2, choice_3, choice_4, correct_choice
	"""
	import base64
	import io

	import openpyxl

	try:
		fcontent = base64.b64decode(file_content)
	except Exception as e:
		frappe.throw(f"Invalid file content: {e}")

	try:
		wb = openpyxl.load_workbook(io.BytesIO(fcontent), read_only=True, data_only=True)
	except Exception as e:
		frappe.throw(f"Could not open Excel file: {e}")

	ws = wb.active
	all_rows = list(ws.iter_rows(values_only=True))
	if not all_rows:
		frappe.throw("The Excel file is empty.")

	headers = [str(h).strip().lower() if h is not None else "" for h in all_rows[0]]
	missing = _EXCEL_REQUIRED_COLUMNS - set(headers)
	if missing:
		frappe.throw(f"Missing required column(s): {', '.join(sorted(missing))}")

	col = {h: i for i, h in enumerate(headers)}
	questions = []
	for row_num, row in enumerate(all_rows[1:], start=2):
		q_text = str(row[col["question_text"]] or "").strip()
		if not q_text:
			continue  # skip blank rows

		c1 = str(row[col["choice_1"]] or "").strip()
		c2 = str(row[col["choice_2"]] or "").strip()
		c3 = str(row[col.get("choice_3", -1)] or "").strip() if "choice_3" in col else ""
		c4 = str(row[col.get("choice_4", -1)] or "").strip() if "choice_4" in col else ""

		try:
			correct = int(row[col["correct_choice"]])
		except (TypeError, ValueError):
			frappe.throw(f"Row {row_num}: correct_choice must be an integer (1-4)")

		# Count non-empty choices
		num_choices = 2 + (1 if c3 else 0) + (1 if c4 else 0)
		if correct < 1 or correct > num_choices:
			frappe.throw(
				f"Row {row_num}: correct_choice ({correct}) must be between 1 and {num_choices}"
			)

		questions.append(
			{
				"question_text": q_text,
				"choice_1": c1,
				"choice_2": c2,
				"choice_3": c3,
				"choice_4": c4,
				"correct_choice": correct,
			}
		)

	return questions


class MemoraOfficialExam(Document):
	def validate(self):
		self._validate_questions()
		self._validate_subject()

	def _validate_questions(self):
		"""Validate question rows."""
		if not self.questions:
			frappe.throw("At least one question is required.")

		for q in self.questions:
			num_choices = 2 + (1 if q.choice_3 else 0) + (1 if q.choice_4 else 0)
			if q.correct_choice < 1 or q.correct_choice > num_choices:
				frappe.throw(
					f"Question {q.idx}: correct_choice ({q.correct_choice}) "
					f"must be between 1 and {num_choices}."
				)

	def _validate_subject(self):
		"""Subject must exist."""
		if self.subject and not frappe.db.exists("Memora Subject", self.subject):
			frappe.throw(f"Subject '{self.subject}' does not exist.")
