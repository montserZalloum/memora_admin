# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

VALID_TRANSITIONS = {
	"Pending": {"Processing"},
	"Processing": {"Exported", "Failed", "Pending"},  # Pending = auto-retry
	"Exported": {"Transferred", "Failed"},
	"Transferred": {"Ingested", "Failed"},
	"Ingested": {"Completed", "Failed"},
	"Completed": set(),  # Terminal (no Purged state for live sync)
	"Failed": {"Pending"},
}


class MemoraLiveSyncJob(Document):
	def validate(self):
		self._validate_status_transition()

	def before_insert(self):
		if not self.flags.ignore_permissions and not self.flags.programmatic_creation:
			frappe.throw(
				"Live Sync Jobs are created automatically by the system. Manual creation is not allowed.",
				frappe.ValidationError,
			)

	def _validate_status_transition(self):
		if not self.is_new() and self.has_value_changed("status"):
			old_doc = self.get_doc_before_save()
			if old_doc:
				old_status = old_doc.status
				allowed = VALID_TRANSITIONS.get(old_status, set())
				if self.status not in allowed:
					frappe.throw(
						f"Cannot change status from {old_status} to {self.status}. "
						f"Allowed transitions: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}",
						frappe.ValidationError,
					)
