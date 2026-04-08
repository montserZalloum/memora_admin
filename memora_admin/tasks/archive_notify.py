"""Notification task for permanently failed archive jobs.

Scans for Failed archive jobs that haven't been notified yet
(notified_at not set), sends email to Memora Email Receiver users
and publishes Desk realtime alert.

Schedule: Daily at 06:00 via hooks.py
"""

import html as html_mod

import frappe


def notify_failed_archive_jobs():
	"""Send admin notifications for archive jobs that permanently failed.

	Uses the notified_at Datetime field to track which jobs have
	already been reported.
	"""
	failed_jobs = frappe.get_all(
		"Memora Archive Job",
		filters={
			"status": "Failed",
			"completed_at": ["is", "set"],
			"notified_at": ["is", "not set"],
		},
		fields=["name", "source_doctype", "archive_scope", "schema_version", "error_log", "retry_count"],
	)

	unnotified = failed_jobs

	if not unnotified:
		return

	# Get Memora Email Receiver recipients (single query instead of N+1)
	admin_users = frappe.get_all(
		"Has Role",
		filters={"role": "Memora Email Receiver", "parenttype": "User"},
		fields=["parent"],
	)
	admin_names = list({u.parent for u in admin_users})
	recipients = []
	if admin_names:
		users = frappe.get_all(
			"User",
			filters={"name": ["in", admin_names], "enabled": 1},
			fields=["email"],
		)
		recipients = [u.email for u in users if u.email]

	site_url = frappe.utils.get_url()

	for job in unnotified:
		safe_name = html_mod.escape(str(job.name))
		safe_source = html_mod.escape(str(job.source_doctype or ""))
		safe_scope = html_mod.escape(str(job.archive_scope or ""))
		safe_version = html_mod.escape(str(job.schema_version or ""))
		error_snippet = html_mod.escape((job.error_log or "No error details")[:500])
		job_link = f"{site_url}/app/memora-archive-job/{job.name}"

		# Desk realtime alert
		frappe.publish_realtime(
			event="eval_js",
			message=f'frappe.show_alert({{message: "Archive job {safe_name} failed permanently", indicator: "red"}})',
			user="Administrator",
		)

		# Email notification
		if recipients:
			frappe.sendmail(
				recipients=recipients,
				subject=f"Archive Job Failed: {job.name} ({job.archive_scope})",
				message=f"""
				<h3>Archive Job Permanently Failed</h3>
				<p><strong>Job:</strong> {safe_name}</p>
				<p><strong>Source:</strong> {safe_source}</p>
				<p><strong>Scope:</strong> {safe_scope}</p>
				<p><strong>Schema Version:</strong> {safe_version}</p>
				<p><strong>Retry Count:</strong> {job.retry_count}</p>
				<p><strong>Error:</strong></p>
				<pre>{error_snippet}</pre>
				<p><a href="{job_link}">View Job in Frappe Desk</a></p>
				""",
				now=True,
			)

		# Mark as notified via dedicated timestamp field
		frappe.db.set_value(
			"Memora Archive Job",
			job.name,
			"notified_at",
			frappe.utils.now(),
			update_modified=False,
		)

	frappe.db.commit()
	frappe.logger().info(f"Archive failure notifications sent for {len(unnotified)} job(s) to {len(recipients)} admin(s)")
