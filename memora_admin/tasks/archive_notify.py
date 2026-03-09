"""Notification task for permanently failed archive jobs.

Scans for Failed archive jobs that haven't been notified yet
(completed_at set but no prior notification), sends email to
System Manager users and publishes Desk realtime alert.

Schedule: Daily at 06:00 via hooks.py
"""

import frappe


def notify_failed_archive_jobs():
	"""Send admin notifications for archive jobs that permanently failed.

	Uses error_log prefix marker '[NOTIFIED]' to track which jobs have
	already been reported, avoiding a separate DB field.
	"""
	failed_jobs = frappe.get_all(
		"Memora Archive Job",
		filters={
			"status": "Failed",
			"completed_at": ["is", "set"],
		},
		fields=["name", "source_doctype", "archive_scope", "schema_version", "error_log", "retry_count"],
	)

	# Filter out already-notified jobs (error_log starts with [NOTIFIED])
	unnotified = [j for j in failed_jobs if not (j.error_log or "").startswith("[NOTIFIED]")]

	if not unnotified:
		return

	# Get System Manager recipients
	admin_users = frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User"},
		fields=["parent"],
	)
	recipients = []
	for u in admin_users:
		user = frappe.get_doc("User", u.parent)
		if user.enabled and user.email:
			recipients.append(user.email)

	site_url = frappe.utils.get_url()

	for job in unnotified:
		job_link = f"{site_url}/app/memora-archive-job/{job.name}"
		error_snippet = (job.error_log or "No error details")[:500]

		# Desk realtime alert
		frappe.publish_realtime(
			event="eval_js",
			message=f'frappe.show_alert({{message: "Archive job {job.name} failed permanently", indicator: "red"}})',
			user="Administrator",
		)

		# Email notification
		if recipients:
			frappe.sendmail(
				recipients=recipients,
				subject=f"Archive Job Failed: {job.name} ({job.archive_scope})",
				message=f"""
				<h3>Archive Job Permanently Failed</h3>
				<p><strong>Job:</strong> {job.name}</p>
				<p><strong>Source:</strong> {job.source_doctype}</p>
				<p><strong>Scope:</strong> {job.archive_scope}</p>
				<p><strong>Schema Version:</strong> {job.schema_version}</p>
				<p><strong>Retry Count:</strong> {job.retry_count}</p>
				<p><strong>Error:</strong></p>
				<pre>{error_snippet}</pre>
				<p><a href="{job_link}">View Job in Frappe Desk</a></p>
				""",
				now=True,
			)

		# Mark as notified by prepending marker to error_log
		frappe.db.set_value(
			"Memora Archive Job",
			job.name,
			"error_log",
			f"[NOTIFIED] {job.error_log or ''}",
			update_modified=False,
		)

	frappe.db.commit()
	frappe.logger().info(f"Archive failure notifications sent for {len(unnotified)} job(s) to {len(recipients)} admin(s)")
