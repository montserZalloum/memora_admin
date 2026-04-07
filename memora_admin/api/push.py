"""Admin API for sending web push notifications."""

import frappe


@frappe.whitelist(methods=["POST"])
def send_push(title: str, body: str, url: str | None = None, icon: str | None = None, target_plans=None):
	"""Send a push notification to players. Enqueues as a background job.

	Args:
		title: Notification title.
		body: Notification body text.
		url: Optional URL to open on click.
		icon: Optional icon URL.
		target_plans: Optional JSON list of plan names to target.

	Returns:
		{"status": "queued"}
	"""
	frappe.only_for(["System Manager", "Memora Admin"])

	frappe.enqueue(
		"memora_admin.memora_admin.services.push_service.send_push_notification",
		title=title,
		body=body,
		url=url,
		icon=icon,
		target_plans=frappe.parse_json(target_plans) if target_plans else None,
		queue="long",
	)
	return {"status": "queued"}
