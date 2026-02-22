# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


class MemoraReviewItem(Document):
	def validate(self):
		self._validate_item_id()
		self._validate_correct_choice()
		self._validate_content()

	def _validate_item_id(self):
		if not self.item_id or not UUID_RE.match(self.item_id):
			frappe.throw("Item ID must be a valid UUID string (e.g. 550e8400-e29b-41d4-a716-446655440000)")

	def _validate_correct_choice(self):
		if self.correct_choice is not None and self.correct_choice != 0:
			if self.correct_choice < 1 or self.correct_choice > 4:
				frappe.throw("Correct Choice must be between 1 and 4")

	def _validate_content(self):
		if not self.choice_1 and not self.content_json:
			frappe.throw("At least one of Choice 1 or Content JSON must be provided")
