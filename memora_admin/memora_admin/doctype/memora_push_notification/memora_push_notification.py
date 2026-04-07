# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class MemoraPushNotification(Document):
	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("PUSH-.#####.")

	def validate(self):
		if self.status == "Sent" and not self.flags.sending:
			frappe.throw("Push Notifications cannot be modified after sending")

		self._validate_title_length()
		self._validate_target_plans()

	def _validate_title_length(self):
		value = self.title or ""
		if len(value) > 40:
			frappe.throw("Title must not exceed 40 characters")

	def _validate_target_plans(self):
		if self.target_audience == "Specific Plans" and len(self.target_plans or []) == 0:
			frappe.throw("At least one target plan is required when audience is 'Specific Plans'")

	@frappe.whitelist()
	def send(self):
		"""Send the push notification. Called from the form button."""
		if self.status == "Sent":
			frappe.throw("This notification has already been sent")

		self.flags.sending = True
		self.status = "Sent"
		self.sent_at = now_datetime()
		self.save()

		target_plans = None
		if self.target_audience == "Specific Plans":
			target_plans = [row.plan for row in self.target_plans]

		frappe.enqueue(
			"memora_admin.memora_admin.services.push_service.send_push_notification",
			title=self.title,
			body=self.body[:100],
			url=f"/push/{self.name}",
			target_plans=target_plans,
			push_notification_name=self.name,
			queue="long",
		)

		frappe.msgprint("Push notification queued for delivery", indicator="green", alert=True)
