"""Admin notification handlers for content report doc_events.

When a new Memora Content Report is inserted, notify admin users
via desk realtime alert and email.
"""

import frappe


def on_content_report_created(doc, method):
	"""Send notification to admins when a new content report is created.

	Triggered by after_insert doc_event on Memora Content Report.

	Args:
		doc: Memora Content Report document
		method: Frappe hook method name (after_insert)
	"""
	# Get player display name
	player_name = frappe.get_value("Memora Player Profile", doc.player, "display_name") or doc.player

	# Build link to report in Frappe Desk
	site_url = frappe.utils.get_url()
	report_link = f"{site_url}/app/memora-content-report/{doc.name}"

	# 1. Desk realtime alert for Administrator
	frappe.publish_realtime(
		event="eval_js",
		message=f'frappe.show_alert({{message: "New content report from {player_name}: {doc.report_type}", indicator: "orange"}})',
		user="Administrator",
	)

	# 2. Email to all System Manager users
	admin_users = frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User"},
		fields=["parent"],
	)
	recipients = []
	for u in admin_users:
		user = frappe.get_doc("User", u.parent)
		if user.enabled and user.email:
			recipients.append(user.email)

	if recipients:
		subject_name = ""
		if doc.subject:
			subject_name = frappe.get_value("Memora Subject", doc.subject, "subject_name") or doc.subject

		frappe.sendmail(
			recipients=recipients,
			subject=f"Content Report: {doc.report_type} - {player_name}",
			message=f"""
			<h3>New Content Report</h3>
			<p><strong>Player:</strong> {player_name}</p>
			<p><strong>Type:</strong> {doc.report_type}</p>
			<p><strong>Subject:</strong> {subject_name or "N/A"}</p>
			<p><strong>Lesson:</strong> {doc.lesson or "N/A"}</p>
			<p><strong>Description:</strong> {doc.description}</p>
			<p><a href="{report_link}">View Report in Frappe Desk</a></p>
			""",
			now=True,
		)

	frappe.logger().info(f"Content report notification sent for {doc.name} to {len(recipients)} admins")
