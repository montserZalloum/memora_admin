# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document

from fastapi_app.core.redis_keys import notify_channel, pending_key
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
		# Batch-check existing subscriptions (PERF-14: single query instead of N exists() calls)
		existing_keys = set(
			frappe.get_all(
				"Memora Player Subscription",
				filters={"player": self.player, "access_key": ["in", grant_keys]},
				pluck="access_key",
			)
		)

		created_subs = []
		try:
			for access_key in grant_keys:
				if access_key in existing_keys:
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
		r.srem(pending_key(self.player), self.related_grant)

		# Publish real-time notification to player via Redis pub/sub
		# Skip for voucher redemptions — player already gets the result in the HTTP response
		if self.payment_method != "Voucher":
			try:
				self._publish_notification("approved", grant_keys)
			except Exception as e:
				frappe.logger().warning(f"Failed to publish notification: {e}")

		frappe.logger().info(f"Transaction {self.name} approved: {len(created_subs)} subscriptions created")
		frappe.msgprint(
			f"Approved: {len(created_subs)} subscription(s) created for {self.player}",
			indicator="green",
		)

	def _handle_rejection(self):
		"""Clean up pending set so the product reappears in catalog."""
		if self.related_grant:
			r = get_fastapi_redis()
			r.srem(pending_key(self.player), self.related_grant)

		# Publish real-time notification to player via Redis pub/sub
		try:
			rejection_keys = get_grant_keys(self.related_grant) if self.related_grant else []
			self._publish_notification("rejected", rejection_keys)
		except Exception as e:
			frappe.logger().warning(f"Failed to publish notification: {e}")

		frappe.logger().info(f"Transaction {self.name} rejected, pending cleared for {self.player}")

	def _publish_notification(self, status: str, grant_keys: list[str]) -> None:
		"""Publish subscription notification to Redis pub/sub for WebSocket relay.

		Publishes to per-user channel `memora:notify:{player_id}` so the FastAPI
		notification listener can forward to connected WebSocket clients.

		Args:
			status: Either "approved" or "rejected".
			grant_keys: List of access keys (e.g., ["SUB-SUBJ-00028"]).
		"""
		# Look up human-readable product name from the grant
		product_name = self.related_grant or ""
		try:
			if self.related_grant:
				item_code = frappe.get_value("Memora Product Grant", self.related_grant, "item_code")
				if item_code:
					item_name = frappe.get_value("Item", item_code, "item_name")
					if item_name:
						product_name = item_name
		except Exception:
			pass  # Fallback to related_grant name

		payload = {
			"type": "subscription_update",
			"status": status,
			"transaction_id": self.name,
			"product_name": product_name,
			"subject_ids": [k.replace("SUB-", "") for k in grant_keys if k.startswith("SUB-")],
			"timestamp": frappe.utils.now_datetime().isoformat(),
		}

		r = get_fastapi_redis()
		r.publish(notify_channel(self.player), json.dumps(payload))

		frappe.logger().info(f"Notification published for {self.player}: {status}")

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
