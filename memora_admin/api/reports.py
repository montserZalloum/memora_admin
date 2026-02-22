"""Frappe whitelisted API for content report creation.

Called by FastAPI sidecar to create Memora Content Report documents.
"""

from __future__ import annotations

import frappe
from frappe.utils.file_manager import save_file


@frappe.whitelist(allow_guest=False)
def create_content_report(
	player: str,
	report_type: str,
	description: str,
	subject: str | None = None,
	lesson: str | None = None,
	screenshot_base64: str | None = None,
	screenshot_filename: str | None = None,
) -> dict:
	"""Create a Memora Content Report document.

	Args:
		player: Player profile ID (user email)
		report_type: One of Bug, Content Error, Suggestion, Other
		description: Report description text
		subject: Optional Memora Subject link
		lesson: Optional Memora Lesson link
		screenshot_base64: Optional base64-encoded screenshot
		screenshot_filename: Filename for the screenshot

	Returns:
		Dict with report name
	"""
	# Validate player exists
	if not frappe.db.exists("Memora Player Profile", player):
		frappe.throw(f"Player {player} not found", frappe.DoesNotExistError)

	# Validate subject if provided
	if subject and not frappe.db.exists("Memora Subject", subject):
		frappe.throw(f"Subject {subject} not found", frappe.DoesNotExistError)

	# Validate lesson if provided
	if lesson and not frappe.db.exists("Memora Lesson", lesson):
		frappe.throw(f"Lesson {lesson} not found", frappe.DoesNotExistError)

	doc = frappe.get_doc(
		{
			"doctype": "Memora Content Report",
			"player": player,
			"report_type": report_type,
			"description": description,
			"subject": subject,
			"lesson": lesson,
			"status": "Open",
		}
	)
	doc.insert(ignore_permissions=True)

	# Attach screenshot if provided (non-fatal)
	if screenshot_base64 and screenshot_filename:
		try:
			file_doc = save_file(
				fname=screenshot_filename,
				content=screenshot_base64,
				dt="Memora Content Report",
				dn=doc.name,
				is_private=1,
				df="screen_shot",
				decode=True,
			)
			doc.db_set("screen_shot", file_doc.file_url)
		except Exception:
			frappe.log_error("Screenshot attachment failed for report " + doc.name)

	return {"name": doc.name}
