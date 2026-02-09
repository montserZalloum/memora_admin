"""Setup module for Memora Admin app.

Runs after bench install-app to create required roles.
"""

import frappe


def after_install():
	"""Create custom roles after app installation."""
	create_task_admin_role()


def create_task_admin_role():
	"""Create Task Admin role for scheduled task operations.

	Grants:
	- Read/write access to Memora Task Run Log
	- Ability to trigger manual task runs (via API)
	- View task dashboard page
	"""
	if frappe.db.exists("Role", "Task Admin"):
		return  # Already exists

	role = frappe.get_doc({
		"doctype": "Role",
		"role_name": "Task Admin",
		"desk_access": 1,
		"is_custom": 1,
	})
	role.insert(ignore_permissions=True)
	frappe.db.commit()
	print("Created Task Admin role")


def after_migrate():
	"""Ensure custom composite indexes exist after migration.

	Frappe's migrate only preserves single-column indexes via Property Setters.
	Multi-column indexes must be re-created explicitly.
	"""
	_ensure_memory_state_composite_index()


def _ensure_memory_state_composite_index():
	"""Create composite index (player, subject, next_review) on Memora Memory State.

	Enables <5ms review queries: WHERE player=? AND subject=? AND next_review<=?
	"""
	frappe.db.add_index(
		"Memora Memory State",
		["player", "subject", "next_review"],
		index_name="player_subject_next_review_index",
	)
