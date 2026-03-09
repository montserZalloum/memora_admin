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
	frappe.only_for("System Manager")
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
	job.sync_paused = 0
	job.sync_paused_at = None
	job.save(ignore_permissions=True)

	return {"status": "success", "job_name": job_name}


@frappe.whitelist()
def clear_sync_pause(job_name: str):
	"""Manually clear sync_paused flag on an archive job."""
	frappe.only_for("System Manager")
	job = frappe.get_doc("Memora Archive Job", job_name)

	if not job.sync_paused:
		frappe.throw("Sync is not paused on this job.", frappe.ValidationError)

	job.sync_paused = 0
	job.sync_paused_at = None
	job.save(ignore_permissions=True)

	# Invalidate the sync task's paused_filters cache so the change takes
	# effect immediately (instead of waiting up to 60s for TTL expiry)
	try:
		from memora_admin.tasks.sync import invalidate_paused_filters_cache
		invalidate_paused_filters_cache()
	except ImportError:
		pass

	return {"status": "success", "job_name": job_name}
