# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from memora_admin.api.products import get_grant_keys
from memora_admin.events.access_sync import get_fastapi_redis


class MemoraSubscriptionTransaction(Document):
	def on_update(self):
		if not self.has_value_changed("status"):
			return

		if self.status == "Completed":
			self._handle_approval()
		elif self.status == "Rejected":
			self._handle_rejection()

	def _handle_approval(self):
		"""Create Player Subscriptions for each grant component and clean up pending set."""
		if not self.related_grant:
			frappe.throw("Cannot approve: no Related Grant linked to this transaction.")

		# Get access keys from the product grant
		grant_keys = get_grant_keys(self.related_grant)
		if not grant_keys:
			frappe.throw(f"Cannot approve: Product Grant {self.related_grant} has no grant components.")

		# Determine expiration from player's plan season
		expires_at = self._get_expires_at()

		# All-or-nothing subscription creation
		created_subs = []
		try:
			for access_key in grant_keys:
				existing = frappe.db.exists(
					"Memora Player Subscription",
					{"player": self.player, "access_key": access_key},
				)
				if existing:
					continue  # Skip duplicates (overlapping subscriptions OK)

				sub = frappe.get_doc(
					{
						"doctype": "Memora Player Subscription",
						"player": self.player,
						"access_key": access_key,
						"expires_at": expires_at,
						"is_active": 1,
					}
				)
				sub.insert(ignore_permissions=True)
				created_subs.append(sub.name)

			frappe.db.commit()
		except Exception:
			# Rollback: delete any subscriptions we created
			for sub_name in created_subs:
				frappe.delete_doc("Memora Player Subscription", sub_name, force=True)
			frappe.db.commit()
			frappe.throw("Failed to create subscriptions. Transaction not approved.")

		# Clean up pending set (player docname = user email since autoname: field:user)
		r = get_fastapi_redis()
		r.srem(f"memora:pending:{self.player}", self.related_grant)

		frappe.logger().info(f"Transaction {self.name} approved: {len(created_subs)} subscriptions created")
		frappe.msgprint(
			f"Approved: {len(created_subs)} subscription(s) created for {self.player}",
			indicator="green",
		)

	def _handle_rejection(self):
		"""Clean up pending set so the product reappears in catalog."""
		if self.related_grant:
			r = get_fastapi_redis()
			r.srem(f"memora:pending:{self.player}", self.related_grant)

		frappe.logger().info(f"Transaction {self.name} rejected, pending cleared for {self.player}")

	def _get_expires_at(self):
		"""Get subscription expiration date from player's plan season.

		Falls back to 2099-12-31 sentinel if no season is found.
		"""
		sentinel = "2099-12-31"

		try:
			plan_id = frappe.get_value("Memora Player Profile", self.player, "plan")
			if not plan_id:
				return sentinel

			season_id = frappe.get_value("Memora Academic Plan", plan_id, "season")
			if not season_id:
				return sentinel

			end_date = frappe.get_value("Memora Season", season_id, "end_date")
			if not end_date:
				return sentinel

			return end_date
		except Exception:
			frappe.logger().warning(
				f"Could not determine season end_date for player {self.player}, using sentinel"
			)
			return sentinel
