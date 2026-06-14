"""Archive health monitoring task.

Checks 4 alert conditions every 6 hours:
1. Live sync freshness — latest completed sync older than 24h
2. Archive validation lag — jobs stuck in Exported/Transferred for >48h
3. Retry exhaustion — Failed jobs with retry_count >= 3 not yet notified
4. Stuck-state — jobs stuck between stages for >6h

Schedule: Every 6 hours via hooks.py (cron: "0 */6 * * *")
"""

import html as html_mod

import frappe


def check_archive_health():
	"""Run all 4 archive health checks and send alerts as needed."""
	alerts = []

	# Check 1: Live sync freshness
	alerts.extend(_check_live_sync_freshness())

	# Check 2: Archive validation lag
	alerts.extend(_check_archive_validation_lag())

	# Check 3: Retry exhaustion (complement to archive_notify.py)
	alerts.extend(_check_retry_exhaustion())

	# Check 4: Stuck-state detection
	alerts.extend(_check_stuck_state())

	if not alerts:
		return

	_send_alerts(alerts)


def _check_live_sync_freshness() -> list[dict]:
	"""Alert if latest completed live sync is older than 24h or none exist.

	Skipped entirely when live analytics sync is disabled in Memora Settings —
	a stale/missing sync is expected in that case, not a fault.
	"""
	alerts = []

	from memora_admin.tasks.live_sync_trigger import is_live_sync_enabled

	if not is_live_sync_enabled():
		return alerts

	latest = frappe.db.sql(
		"""
		SELECT name, completed_at,
		       TIMESTAMPDIFF(SECOND, completed_at, NOW()) / 3600.0 AS age_hours
		FROM `tabMemora Live Sync Job`
		WHERE status = 'Completed'
		ORDER BY completed_at DESC
		LIMIT 1
		""",
		as_dict=True,
	)

	if not latest:
		alerts.append({
			"type": "live_sync_freshness",
			"severity": "warning",
			"message": "No completed live sync jobs found. Live analytics data may be missing.",
		})
	else:
		age_hours = float(latest[0].get("age_hours") or 0)
		if age_hours > 24:
			alerts.append({
				"type": "live_sync_freshness",
				"severity": "warning",
				"message": (
					f"Latest live sync ({latest[0]['name']}) completed {age_hours:.1f}h ago. "
					f"Expected within 24h."
				),
			})

	return alerts


def _check_archive_validation_lag() -> list[dict]:
	"""Alert for archive jobs stuck in Exported/Transferred state for >48h."""
	alerts = []

	stale_jobs = frappe.db.sql(
		"""
		SELECT name, status, archive_scope, exported_at,
		       TIMESTAMPDIFF(SECOND, exported_at, NOW()) / 3600.0 AS age_hours
		FROM `tabMemora Archive Job`
		WHERE status IN ('Exported', 'Transferred')
		  AND exported_at < DATE_SUB(NOW(), INTERVAL 48 HOUR)
		""",
		as_dict=True,
	)

	for job in stale_jobs:
		age_hours = float(job.age_hours or 0)
		alerts.append({
			"type": "archive_validation_lag",
			"severity": "warning",
			"message": (
				f"Archive job {job.name} ({job.archive_scope}) stuck in {job.status} "
				f"for {age_hours:.1f}h (exported_at: {job.exported_at})"
			),
		})

	return alerts


def _check_retry_exhaustion() -> list[dict]:
	"""Alert for Failed jobs with retry_count >= 3 that haven't been notified.

	Complements archive_notify.py which runs daily — this catches them
	within the 6-hour monitoring window.
	"""
	alerts = []

	failed_jobs = frappe.db.sql(
		"""
		SELECT name, archive_scope, retry_count, error_log
		FROM `tabMemora Archive Job`
		WHERE status = 'Failed'
		  AND retry_count >= 3
		  AND completed_at IS NOT NULL
		  AND notified_at IS NULL
		""",
		as_dict=True,
	)

	for job in failed_jobs:
		error_snippet = (job.error_log or "No details")[:200]
		alerts.append({
			"type": "retry_exhaustion",
			"severity": "critical",
			"message": (
				f"Archive job {job.name} ({job.archive_scope}) permanently failed "
				f"after {job.retry_count} retries. Error: {error_snippet}"
			),
		})

	return alerts


def _check_stuck_state() -> list[dict]:
	"""Alert for jobs stuck between pipeline stages for >6h.

	Catches jobs that may have been missed by the executor's own stuck detection
	(e.g., if the executor itself isn't running).
	"""
	alerts = []

	# Archive jobs
	stuck_archive = frappe.db.sql(
		"""
		SELECT name, status, execution_stage, archive_scope, claimed_at,
		       TIMESTAMPDIFF(SECOND, claimed_at, NOW()) / 3600.0 AS age_hours
		FROM `tabMemora Archive Job`
		WHERE status = 'Processing'
		  AND claimed_at < DATE_SUB(NOW(), INTERVAL 6 HOUR)
		""",
		as_dict=True,
	)

	for job in stuck_archive:
		age_hours = float(job.age_hours or 0)
		alerts.append({
			"type": "stuck_state",
			"severity": "warning",
			"message": (
				f"Archive job {job.name} ({job.archive_scope}) stuck in {job.status}/{job.execution_stage} "
				f"for {age_hours:.1f}h"
			),
		})

	# Live sync jobs
	stuck_live = frappe.db.sql(
		"""
		SELECT name, status, execution_stage, started_at,
		       TIMESTAMPDIFF(SECOND, started_at, NOW()) / 3600.0 AS age_hours
		FROM `tabMemora Live Sync Job`
		WHERE status = 'Processing'
		  AND started_at < DATE_SUB(NOW(), INTERVAL 6 HOUR)
		""",
		as_dict=True,
	)

	for job in stuck_live:
		age_hours = float(job.age_hours or 0)
		alerts.append({
			"type": "stuck_state",
			"severity": "warning",
			"message": (
				f"Live sync job {job.name} stuck in {job.status}/{job.execution_stage} "
				f"for {age_hours:.1f}h"
			),
		})

	return alerts


def _send_alerts(alerts: list[dict]):
	"""Send email + Desk realtime alerts to Memora Email Receiver users."""
	# Get Memora Email Receiver recipients
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

	# Desk realtime alert
	critical_count = sum(1 for a in alerts if a["severity"] == "critical")
	warning_count = sum(1 for a in alerts if a["severity"] == "warning")
	summary = f"{critical_count} critical, {warning_count} warning" if critical_count else f"{warning_count} warning(s)"

	frappe.publish_realtime(
		event="eval_js",
		message=f'frappe.show_alert({{message: "Archive health: {summary}", indicator: "{"red" if critical_count else "orange"}"}});',
		user="Administrator",
	)

	# Email notification
	if recipients:
		alert_rows = []
		for alert in alerts:
			safe_msg = html_mod.escape(alert["message"])
			severity_color = "red" if alert["severity"] == "critical" else "orange"
			alert_rows.append(
				f'<tr><td style="color:{severity_color};font-weight:bold">{alert["severity"].upper()}</td>'
				f'<td>{alert["type"]}</td>'
				f'<td>{safe_msg}</td></tr>'
			)

		frappe.sendmail(
			recipients=recipients,
			subject=f"Archive Health Alert: {summary}",
			message=f"""
			<h3>Archive Health Monitor</h3>
			<table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse">
			<tr><th>Severity</th><th>Type</th><th>Details</th></tr>
			{"".join(alert_rows)}
			</table>
			<p><small>This check runs every 6 hours.</small></p>
			""",
			now=True,
		)

	frappe.logger().info(f"Archive health check: {len(alerts)} alert(s) sent to {len(recipients)} admin(s)")
