"""Pydantic models for real-time notification messages.

Defines the structured message schema for subscription status notifications
sent over WebSocket to connected clients. The SubscriptionNotification is
the client-facing payload; NotificationEnvelope wraps it with routing info
for internal pub/sub transport.
"""

from typing import Literal

from pydantic import BaseModel


class SubscriptionNotification(BaseModel):
	"""Notification sent when subscription status changes.

	This is the payload delivered to the client over WebSocket.
	Published by Frappe approval/rejection handlers via Redis pub/sub,
	then relayed to connected WebSocket clients by the notification listener.

	Attributes:
		type: Always "subscription_update" for this notification type.
		status: Whether the subscription was approved or rejected.
		transaction_id: The Memora Subscription Transaction document name.
		product_name: Human-readable product name (for display).
		subject_ids: List of subject IDs included in the subscription.
		timestamp: ISO 8601 timestamp of when the status change occurred.
	"""

	type: Literal["subscription_update"] = "subscription_update"
	status: Literal["approved", "rejected"]
	transaction_id: str
	product_name: str
	subject_ids: list[str]
	timestamp: str  # ISO 8601


class NotificationEnvelope(BaseModel):
	"""Wrapper for pub/sub transport with routing info.

	Used internally for parsing messages received from Redis pub/sub.
	The channel encodes the target user; the payload is what gets
	forwarded to the WebSocket client.

	Attributes:
		channel: Redis pub/sub channel (e.g., "memora:notify:{user_id}").
		payload: The notification to deliver to the client.
	"""

	channel: str
	payload: SubscriptionNotification
