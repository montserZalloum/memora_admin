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
