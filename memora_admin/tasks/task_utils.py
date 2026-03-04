"""Shared utilities for scheduled tasks.

Provides:
- Date helpers (Asia/Amman timezone)
- Prometheus metrics (counters, histograms)
- Task run logging (to Memora Task Run Log)
- Idempotency checks (prevent duplicate runs)
- Admin notifications (email on critical failure)

All scheduled tasks (streak_reset, session_cleanup, leaderboard_archive) should
use these utilities for consistent observability and error handling.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from prometheus_client import Counter, Histogram

# Asia/Amman timezone for consistent date boundaries
# Per CONTEXT.md: All players use single timezone (no per-player config)
AMMAN_TZ = ZoneInfo("Asia/Amman")

# -----------------------------------------------------------------------------
# Prometheus Metrics
# -----------------------------------------------------------------------------

TASK_RUNS = Counter(
	"memora_task_runs_total",
	"Total scheduled task executions",
	["task_name", "status"],
)

TASK_DURATION = Histogram(
	"memora_task_duration_seconds",
	"Task execution duration",
	["task_name"],
	buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

USERS_PROCESSED = Counter(
	"memora_task_users_processed_total",
	"Users processed by scheduled tasks",
	["task_name"],
)

USERS_FAILED = Counter(
	"memora_task_users_failed_total",
	"Users that failed processing",
	["task_name"],
)

# -----------------------------------------------------------------------------
# Date Helpers
# -----------------------------------------------------------------------------


def get_amman_today() -> str:
	"""Get today's date in Asia/Amman timezone as YYYY-MM-DD.

	Returns:
		Date string in YYYY-MM-DD format
	"""
	return datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")


def get_amman_yesterday() -> str:
	"""Get yesterday's date in Asia/Amman timezone as YYYY-MM-DD.

	Returns:
		Date string in YYYY-MM-DD format
	"""
	yesterday = datetime.now(AMMAN_TZ) - timedelta(days=1)
	return yesterday.strftime("%Y-%m-%d")


# -----------------------------------------------------------------------------
# Task Run Logging
# -----------------------------------------------------------------------------


def log_task_run(
	task_name: str,
	status: str,
	processed: int = 0,
	failed: int = 0,
	error_message: str | None = None,
	failed_details: list | None = None,
	triggered_by: str = "Scheduler",
	started_at: datetime | None = None,
) -> str:
	"""Log task execution to Memora Task Run Log.

	Creates a Task Run Log document with execution details for observability.
	Also emits Prometheus metrics for monitoring.

	Args:
		task_name: Identifier for the task (e.g., "streak_reset", "session_cleanup")
		status: Result status - "Success", "Failed", or "Partial"
		processed: Number of items successfully processed
		failed: Number of individual failures
		error_message: Primary error message if task failed
		failed_details: JSON-serializable list of individual failure details
		triggered_by: How task was triggered - "Scheduler", "Manual", or "Catch-up"
		started_at: When task started (defaults to now if not provided)

	Returns:
		Document name (e.g., "TASK-00001") for reference
	"""
	now = frappe.utils.now_datetime()
	started = started_at or now
	duration = (now - started).total_seconds()

	# Create Task Run Log document
	doc = frappe.get_doc(
		{
			"doctype": "Memora Task Run Log",
			"task_name": task_name,
			"run_date": get_amman_today(),
			"started_at": started,
			"completed_at": now,
			"duration_sec": duration,
			"status": status,
			"processed_count": processed,
			"failed_count": failed,
			"error_message": error_message,
			"failed_details": json.dumps(failed_details) if failed_details else None,
			"triggered_by": triggered_by,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	# Emit Prometheus metrics
	TASK_RUNS.labels(task_name=task_name, status=status).inc()
	TASK_DURATION.labels(task_name=task_name).observe(duration)
	if processed > 0:
		USERS_PROCESSED.labels(task_name=task_name).inc(processed)
	if failed > 0:
		USERS_FAILED.labels(task_name=task_name).inc(failed)

	return doc.name


# -----------------------------------------------------------------------------
# Idempotency Checks
# -----------------------------------------------------------------------------


def get_last_successful_run(task_name: str, run_date: str | None = None) -> str | None:
	"""Get last successful run date for a task.

	Args:
		task_name: Task identifier
		run_date: Optional specific date to check (YYYY-MM-DD)

	Returns:
		Run date string if found, None otherwise
	"""
	filters = {"task_name": task_name, "status": "Success"}
	if run_date:
		filters["run_date"] = run_date

	return frappe.db.get_value(
		"Memora Task Run Log",
		filters,
		"run_date",
		order_by="run_date desc",
	)


def has_run_today(task_name: str) -> bool:
	"""Check if task already ran successfully today.

	Used for idempotency - prevents duplicate runs on the same date.
	Per RESEARCH.md: All tasks should check this before processing.

	Args:
		task_name: Task identifier

	Returns:
		True if task has already run successfully today
	"""
	today = get_amman_today()
	return get_last_successful_run(task_name, today) is not None


# -----------------------------------------------------------------------------
# Admin Notifications
# -----------------------------------------------------------------------------


def notify_admins(task_name: str, error_message: str) -> None:
	"""Send notification to Task Admin role users on critical failure.

	Per CONTEXT.md: Fail fast + Frappe notification to admin users.
	Falls back to System Manager if no Task Admin role exists.

	Args:
		task_name: Name of the failed task
		error_message: Error description to include in notification
	"""
	# Get users with Task Admin role
	admin_users = frappe.get_all(
		"Has Role",
		filters={"role": "Task Admin", "parenttype": "User"},
		fields=["parent"],
	)

	recipients = []
	for u in admin_users:
		user = frappe.get_doc("User", u.parent)
		if user.enabled and user.email:
			recipients.append(user.email)

	# Fallback to System Manager if no Task Admin
	if not recipients:
		admin_users = frappe.get_all(
			"Has Role",
			filters={"role": "System Manager", "parenttype": "User"},
			fields=["parent"],
		)
		for u in admin_users:
			user = frappe.get_doc("User", u.parent)
			if user.enabled and user.email:
				recipients.append(user.email)

	if recipients:
		frappe.sendmail(
			recipients=recipients,
			subject=f"[CRITICAL] Scheduled Task Failed: {task_name}",
			message=f"""
			<h3>Scheduled Task Failure Alert</h3>
			<p><strong>Task:</strong> {task_name}</p>
			<p><strong>Time:</strong> {frappe.utils.now_datetime()}</p>
			<p><strong>Error:</strong></p>
			<pre>{error_message}</pre>
			<p>Please check the Memora Task Run Log for details.</p>
			""",
			now=True,
		)

	# Also log to Error Log for Frappe Desk visibility
	frappe.log_error(
		title=f"Task Failed: {task_name}",
		message=error_message,
	)
