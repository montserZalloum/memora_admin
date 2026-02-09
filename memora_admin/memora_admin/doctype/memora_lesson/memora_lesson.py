# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraLesson(Document):
	def before_insert(self):
		"""Auto-assign bit_index on lesson creation (PRD-1 section 6.3)."""
		self._assign_bit_index()

	def _assign_bit_index(self):
		"""
		Assign a unique, sequential bit_index for this lesson within its subject.

		Rules (from PRD-1 section 6.3):
		- Auto-assigned on lesson creation
		- Never changes after assignment
		- Never reused even if lesson is deleted
		- Per-subject (each subject has its own sequence starting from 0)

		Uses the subject's last_bit_index field as a monotonic counter.
		The field stores the NEXT index to assign (despite the name).
		"""
		if self.bit_index:
			return  # Already assigned (e.g., during data import)

		if not self.subject:
			return  # No subject linked yet - cannot assign

		# Lock the subject row to prevent concurrent assignment races
		subject = frappe.get_doc("Memora Subject", self.subject, for_update=True)
		current_next = subject.last_bit_index or 0

		self.bit_index = current_next
		subject.last_bit_index = current_next + 1
		subject.save(ignore_permissions=True)
