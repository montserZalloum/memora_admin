# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

VALID_TRANSITIONS = {
	"Not Started": {"In Progress", "Completed", "Cancelled"},
	"In Progress": {"Completed", "Cancelled"},
	"Completed": set(),
	"Cancelled": {"Not Started"},  # allow restart
}


class MemoraRunbook(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("RB-.#####.")

	def insert(self, *args, **kwargs):
		# Populate before super().insert() so context_doctype is set
		# before Frappe's _validate_links() checks the Dynamic Link.
		self._populate_from_template()
		return super().insert(*args, **kwargs)

	def validate(self):
		self._validate_status_transition()

	def on_update(self):
		if self.has_value_changed("status"):
			if self.status == "In Progress" and not self.started_by:
				self.db_set("started_by", frappe.session.user)
			elif self.status == "Completed":
				self.db_set("completed_at", now_datetime())

	def check_all_done(self):
		"""Return True if every step is Done or Skipped."""
		if not self.steps:
			return False
		return all(s.status in ("Done", "Skipped") for s in self.steps)

	def _populate_from_template(self):
		from memora_admin.memora_admin.runbooks.registry import get_template

		template = get_template(self.workflow_id)
		if not template:
			frappe.throw(f"Unknown workflow: {self.workflow_id}")

		self.workflow_label = template.label
		self.workflow_description = template.description
		if template.context_doctype:
			self.context_doctype = template.context_doctype

		# Only populate steps if not already set (API may have already done this)
		if not self.steps:
			for step_def in template.steps:
				action_url = step_def.action_url or ""
				if action_url and self.context_name:
					action_url = action_url.replace("{context_name}", self.context_name)
				self.append("steps", {
					"step_key": step_def.key,
					"label": step_def.label,
					"description": step_def.description,
					"hint": step_def.hint,
					"optional": step_def.optional,
					"action_url": action_url,
					"status": "Pending",
				})

	def _validate_status_transition(self):
		if self.is_new() or not self.has_value_changed("status"):
			return
		old_doc = self.get_doc_before_save()
		if not old_doc:
			return
		old_status = old_doc.status
		allowed = VALID_TRANSITIONS.get(old_status, set())
		if self.status not in allowed:
			frappe.throw(
				f"Cannot change runbook status from {old_status} to {self.status}. "
				f"Allowed: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}",
				frappe.ValidationError,
			)
