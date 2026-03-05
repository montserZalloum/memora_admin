"""Daily cleanup task for expired Memora Announcements.

Deletes Announcement records whose effective_end_date is in the past,
keeping the announcements table lean and avoiding stale content delivery.
"""

import frappe
from frappe.utils import today


def cleanup_expired_announcements():
	"""Delete Memora Announcement records past their effective_end_date."""
	expired = frappe.get_all(
		"Memora Announcement",
		filters={"effective_end_date": ["<", today()]},
		fields=["name"],
	)

	if not expired:
		return

	for ann in expired:
		try:
			frappe.delete_doc("Memora Announcement", ann.name, ignore_permissions=True)
		except Exception:
			frappe.log_error(title=f"Announcement cleanup failed for {ann.name}")

	frappe.db.commit()
