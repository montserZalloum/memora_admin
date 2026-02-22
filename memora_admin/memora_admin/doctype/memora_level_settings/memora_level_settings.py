import frappe
from frappe.model.document import Document


class MemoraLevelSettings(Document):
	def validate(self):
		if self.quadratic_coefficient < 1:
			frappe.throw("Quadratic coefficient must be at least 1")
		if self.linear_coefficient < 0:
			frappe.throw("Linear coefficient must be at least 0")
		if self.max_level < 1:
			frappe.throw("Max level must be at least 1")

		seen_levels = set()
		for row in self.level_titles:
			if row.level_number < 1:
				frappe.throw(f"Row {row.idx}: Level number must be at least 1")
			if not row.title_en or not row.title_en.strip():
				frappe.throw(f"Row {row.idx}: English title is required")
			if row.level_number in seen_levels:
				frappe.throw(f"Row {row.idx}: Duplicate level number {row.level_number}")
			seen_levels.add(row.level_number)
