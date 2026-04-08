"""Stale sync_paused checker for archive jobs.

Warns admins if sync_paused has been active for >24h without active processing.
This catches orphaned pauses (e.g., executor crash left sync_paused=1 but job
already Failed or was never progressed).

Schedule: Daily at 07:00 via hooks.py
"""

import html as html_mod

import frappe


def check_stale_archive_pauses():
	"""Warn if sync_paused has been active >24h without active archive processing."""
	stale_jobs = frappe.get_all(
		"Memora Archive Job",
		filters={
			"sync_paused": 1,
			"sync_paused_at": ["<", frappe.utils.add_days(frappe.utils.now_datetime(), -1)],
			"status": ["not in", ["Processing", "Exported", "Transferred", "Ingested"]],
		},
		fields=["name", "status", "source_doctype", "archive_scope", "sync_paused_at"],
	)

	if not stale_jobs:
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

	for job in stale_jobs:
		safe_name = html_mod.escape(str(job.name))
		safe_status = html_mod.escape(str(job.status or ""))
		safe_source = html_mod.escape(str(job.source_doctype or ""))
		safe_scope = html_mod.escape(str(job.archive_scope or ""))
		paused_at = html_mod.escape(str(job.sync_paused_at or "unknown"))
		job_link = f"{site_url}/app/memora-archive-job/{job.name}"

		# Desk realtime alert
		frappe.publish_realtime(
			event="eval_js",
			message=(
				f'frappe.show_alert({{message: "Archive job {safe_name} has stale sync_paused '
				f'(since {paused_at})", indicator: "orange"}})'
			),
			user="Administrator",
		)

		# Email notification
		if recipients:
			frappe.sendmail(
				recipients=recipients,
				subject=f"Stale Sync Pause: {job.name} ({job.archive_scope})",
				message=f"""
				<h3>Stale Sync Pause Detected</h3>
				<p>Archive job <strong>{safe_name}</strong> has had <code>sync_paused=1</code>
				for more than 24 hours, but its status is <strong>{safe_status}</strong>
				(not actively processing).</p>
				<p><strong>Source:</strong> {safe_source}</p>
				<p><strong>Scope:</strong> {safe_scope}</p>
				<p><strong>Paused Since:</strong> {paused_at}</p>
				<p>This may indicate an executor crash or misconfiguration.
				Consider clearing the pause manually.</p>
				<p><a href="{job_link}">View Job in Frappe Desk</a></p>
				""",
				now=True,
			)

	frappe.logger().info(f"Stale sync pause check: {len(stale_jobs)} job(s) with stale pause detected")
