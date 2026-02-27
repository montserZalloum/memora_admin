"""
Task administration API for manual task triggering.

Per CONTEXT.md:
- Admins can manually trigger tasks from Frappe UI
- Custom "Task Admin" role for task operations
- Re-run is full task only, not per-user granularity
"""

from __future__ import annotations

import frappe


@frappe.whitelist()
def trigger_task(task_name: str) -> dict:
	"""Manually trigger a scheduled task.

	Requires Task Admin or System Manager role.

	Args:
		task_name: One of "streak_reset", "session_cleanup",
				   "leaderboard_daily", "leaderboard_weekly"

	Returns:
		Dict with success status and optional error message
	"""
	# Check permissions - require Task Admin or System Manager role
	user_roles = frappe.get_roles()
	if not ("System Manager" in user_roles or "Task Admin" in user_roles):
		frappe.throw("You don't have permission to trigger tasks", frappe.PermissionError)

	# Map task names to task function paths
	task_map = {
		"streak_reset": "memora_admin.tasks.streak_reset.reset_broken_streaks",
		"session_cleanup": "memora_admin.tasks.session_cleanup.cleanup_expired_sessions",
		"leaderboard_daily": "memora_admin.tasks.leaderboard_reset.archive_daily_leaderboard",
		"leaderboard_weekly": "memora_admin.tasks.leaderboard_reset.archive_weekly_leaderboard",
	}

	if task_name not in task_map:
		return {"success": False, "error": f"Unknown task: {task_name}"}

	task_path = task_map[task_name]

	try:
		# Import and call the task function
		module_path, func_name = task_path.rsplit(".", 1)
		module = frappe.get_module(module_path)
		func = getattr(module, func_name)

		# CRITICAL: Pass triggered_by="Manual" to override default "Scheduler"
		# This ensures the Memora Task Run Log correctly shows manual trigger source
		func(triggered_by="Manual")

		return {"success": True}

	except Exception as e:
		frappe.log_error(f"Manual task trigger failed: {task_name}", str(e))
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def get_task_status(task_name: str) -> dict:
	"""Get the most recent run status for a task.

	Args:
		task_name: Task identifier

	Returns:
		Dict with last run info or None if never run
	"""
	if not frappe.has_permission("Memora Task Run Log", "read"):
		frappe.throw("You don't have permission to view task status", frappe.PermissionError)

	last_run = frappe.get_all(
		"Memora Task Run Log",
		filters={"task_name": task_name},
		fields=["run_date", "status", "duration_sec", "processed_count", "failed_count"],
		order_by="started_at desc",
		limit=1,
	)

	if last_run:
		return {"found": True, **last_run[0]}
	return {"found": False}
