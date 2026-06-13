"""Override for Frappe's ``EmailAccount`` doctype.

Two reasons this override exists:

1. **Bug fix** — When an incoming mail connection fails on the ``OSError``/timeout
   path, Frappe sets ``description = frappe.message_log.pop()`` which is a *dict*
   (``{'message': ..., 'title': ...}``), not a string. It then forwards that dict
   to ``assign_to.add`` → ``strip_html(dict)`` → ``TypeError: expected string or
   bytes-like object``. We coerce the description to a string before assigning.

2. **Routing** — Stock Frappe assigns the "broken email account" ToDo to System
   Managers only. We instead route it to every user holding the
   ``Memora Email Receiver`` role (falling back to System Managers if nobody has
   the role, so notifications are never silently dropped).
"""

import frappe
from frappe.desk.form import assign_to
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.utils.user import get_system_managers, get_users_with_role

RECEIVER_ROLE = "Memora Email Receiver"


def _coerce_description(description) -> str:
	"""Frappe sometimes passes a message-log dict; assign_to.add needs a string."""
	if isinstance(description, dict):
		title = description.get("title")
		message = description.get("message", "")
		return f"{title}: {message}" if title else str(message)
	return str(description) if description is not None else ""


def _get_receivers() -> list[str]:
	receivers = get_users_with_role(RECEIVER_ROLE)
	# Never drop the notification: fall back to System Managers if the role is empty.
	return receivers or get_system_managers(only_name=True)


class MemoraEmailAccount(EmailAccount):
	def _disable_broken_incoming_account(self, description):
		if frappe.flags.in_test:
			return

		self.db_set("enable_incoming", 0)

		description = _coerce_description(description)

		for user in _get_receivers():
			try:
				assign_to.add(
					{
						"assign_to": [user],
						"doctype": self.doctype,
						"name": self.name,
						"description": description,
						"priority": "High",
						"notify": 1,
					}
				)
			except assign_to.DuplicateToDoError:
				pass
