"""Admin notification handlers for content report doc_events.

When a new Memora Content Report is inserted, notify admin users
via desk realtime alert and email.
"""

import frappe


def on_content_report_created(doc, method):
	"""Send notification to admins when a new content report is created.

	Triggered by after_insert doc_event on Memora Content Report.
	Wrapped in try/except so notification failures never surface to the player.

	Args:
		doc: Memora Content Report document
		method: Frappe hook method name (after_insert)
	"""
	try:
		_notify_content_report(doc)
	except Exception:
		frappe.log_error(title=f"Content report notification failed for {doc.name}")


def _notify_content_report(doc):
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

	# 2. Email to all Memora Email Receiver users
	admin_users = frappe.get_all(
		"Has Role",
		filters={"role": "Memora Email Receiver", "parenttype": "User"},
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
			subject=f"بلاغ محتوى: {doc.report_type} - {player_name}",
			message=f"""
			<div dir="rtl" style="font-family: 'Segoe UI', Tahoma, Arial, sans-serif; text-align: right; max-width: 560px; margin: 0 auto; padding: 24px; background: #ffffff; border-radius: 8px; border: 1px solid #e5e7eb;">
				<h3 style="color: #1f2937; margin: 0 0 20px 0; font-size: 20px;">بلاغ محتوى جديد</h3>
				<table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
					<tr>
						<td style="padding: 10px 0; border-bottom: 1px solid #f3f4f6; color: #6b7280; width: 120px;">اللاعب</td>
						<td style="padding: 10px 0; border-bottom: 1px solid #f3f4f6; color: #1f2937; font-weight: 600;">{player_name}</td>
					</tr>
					<tr>
						<td style="padding: 10px 0; border-bottom: 1px solid #f3f4f6; color: #6b7280;">النوع</td>
						<td style="padding: 10px 0; border-bottom: 1px solid #f3f4f6; color: #1f2937; font-weight: 600;">{doc.report_type}</td>
					</tr>
					<tr>
						<td style="padding: 10px 0; border-bottom: 1px solid #f3f4f6; color: #6b7280;">المادة</td>
						<td style="padding: 10px 0; border-bottom: 1px solid #f3f4f6; color: #1f2937; font-weight: 600;">{subject_name or "غير محدد"}</td>
					</tr>
					<tr>
						<td style="padding: 10px 0; border-bottom: 1px solid #f3f4f6; color: #6b7280;">الدرس</td>
						<td style="padding: 10px 0; border-bottom: 1px solid #f3f4f6; color: #1f2937; font-weight: 600;">{doc.lesson or "غير محدد"}</td>
					</tr>
					<tr>
						<td style="padding: 10px 0; color: #6b7280;">الوصف</td>
						<td style="padding: 10px 0; color: #1f2937;">{doc.description}</td>
					</tr>
				</table>
				<div style="text-align: center;">
					<a href="{report_link}" style="display: inline-block; padding: 12px 32px; background-color: #4f46e5; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 15px;">عرض البلاغ</a>
				</div>
			</div>
			""",
			now=True,
		)

	frappe.logger().info(f"Content report notification sent for {doc.name} to {len(recipients)} admins")
