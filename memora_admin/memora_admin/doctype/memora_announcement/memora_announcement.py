# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import today


class MemoraAnnouncement(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("ANN-.#####.")

	def validate(self):
		self._validate_title_length()
		self._validate_target_plans()
		self._validate_date_range()
		self._validate_fixed_duration()
		self._compute_effective_dates()

	def _validate_target_plans(self):
		if self.target_audience == "Specific Plans" and len(self.target_plans or []) == 0:
			frappe.throw("At least one target plan is required when audience is 'Specific Plans'")

	def _validate_title_length(self):
		for field in ("title_ar", "title_en"):
			value = self.get(field) or ""
			if len(value) > 140:
				frappe.throw(f"{self.meta.get_label(field)} must not exceed 140 characters")

	def _validate_date_range(self):
		if self.duration_type == "Date Range":
			if not self.start_date or not self.end_date:
				frappe.throw("Start Date and End Date are required for Date Range duration")
			if self.end_date <= self.start_date:
				frappe.throw("End Date must be after Start Date")

	def _validate_fixed_duration(self):
		if self.duration_type == "Fixed Duration":
			if not self.duration_days or self.duration_days < 1:
				frappe.throw("Duration Days must be at least 1 for Fixed Duration")

	def _compute_effective_dates(self):
		if self.duration_type == "Date Range":
			self.effective_start_date = self.start_date
			self.effective_end_date = self.end_date
		elif self.duration_type == "Fixed Duration" and self.is_published:
			was_published = (
				(self._doc_before_save and self._doc_before_save.is_published)
				if self._doc_before_save
				else False
			)

			if not was_published:
				# First time publishing: compute effective dates from today
				publish_date = frappe.utils.getdate(today())
				self.effective_start_date = publish_date
				self.effective_end_date = publish_date + timedelta(days=self.duration_days)
			# else: already published re-save — preserve existing effective dates
