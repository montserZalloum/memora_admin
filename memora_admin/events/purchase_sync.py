"""Admin notification handlers for purchase request doc_events.

When a new Memora Subscription Transaction is inserted with Pending Approval
status, notify admin users via desk realtime alert and email.
"""

import frappe


def on_purchase_request_created(doc, method):
	"""Send notification to admins when a new purchase request is created.

	Triggered by after_insert doc_event on Memora Subscription Transaction.
	Wrapped in try/except so notification failures never surface to the player.

	Args:
		doc: Memora Subscription Transaction document
		method: Frappe hook method name (after_insert)
	"""
	if doc.status != "Pending Approval":
		return

	# Voucher redemptions auto-approve instantly -- no admin notification needed
	if doc.payment_method == "Voucher":
		return

	try:
		_notify_purchase_request(doc)
	except Exception:
		frappe.log_error(title=f"Purchase request notification failed for {doc.name}")


def _notify_purchase_request(doc):
	# Get player display name
	player_name = frappe.get_value("Memora Player Profile", doc.player, "display_name") or doc.player

	# Get product name from grant's item
	item_name = doc.related_grant  # fallback
	if doc.related_grant:
		grant_item_code = frappe.get_value("Memora Product Grant", doc.related_grant, "item_code")
		if grant_item_code:
			item_name = frappe.get_value("Item", grant_item_code, "item_name") or grant_item_code

	# Build link to transaction in Frappe Desk
	site_url = frappe.utils.get_url()
	trx_link = f"{site_url}/app/memora-subscription-transaction/{doc.name}"

	# 1. Desk realtime alert for Administrator
	frappe.publish_realtime(
		event="eval_js",
		message=f'frappe.show_alert("New purchase request from {player_name}")',
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
		frappe.sendmail(
			recipients=recipients,
			subject=f"New Purchase Request: {item_name} - {player_name}",
			message=f"""
			<h3>New Purchase Request</h3>
			<p><strong>Player:</strong> {player_name}</p>
			<p><strong>Product:</strong> {item_name}</p>
			<p><strong>Amount:</strong> {doc.amount_paid}</p>
			<p><strong>Payment Method:</strong> {doc.payment_method}</p>
			<p><a href="{trx_link}">Review Transaction in Frappe Desk</a></p>
			""",
			now=True,
		)

	frappe.logger().info(f"Purchase request notification sent for {doc.name} to {len(recipients)} admins")
