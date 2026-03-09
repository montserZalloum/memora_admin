# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

VALID_TRANSITIONS = {
	"Pending": {"Processing"},
	"Processing": {"Completed", "Failed"},
	"Completed": {"Purged"},
	"Failed": {"Pending"},
	"Purged": set(),  # Terminal
}


class MemoraArchiveJob(Document):
	def validate(self):
		self._validate_status_transition()

	def before_insert(self):
		if not self.flags.ignore_permissions and not self.flags.programmatic_creation:
			frappe.throw(
				"Archive Jobs are created automatically by the system. Manual creation is not allowed.",
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


@frappe.whitelist()
def retry_archive_job(job_name: str):
	"""Reset a Failed archive job back to Pending for re-processing."""
	job = frappe.get_doc("Memora Archive Job", job_name)

	if job.status != "Failed":
		frappe.throw(
			f"Only Failed jobs can be retried. Current status: {job.status}",
			frappe.ValidationError,
		)

	job.status = "Pending"
	job.retry_count = 0
	job.error_log = None
	job.execution_stage = None
	job.save(ignore_permissions=True)

	return {"status": "success", "job_name": job_name}
