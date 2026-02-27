"""Tests for webhook endpoints.

Tests verify webhook endpoint:
- POST /api/v1/webhooks/payment - Handle payment webhook

Reference: contracts/endpoint-test-contracts.md §10
"""
import pytest

from fastapi_app.core.redis_keys import webhook_idempotency_key


def _make_webhook_payload(event_id: str, **overrides) -> dict:
	"""Build a valid WebhookPayload dict with all required fields."""
	payload = {
		"event_id": event_id,
		"event_type": "payment.completed",
		"transaction_id": f"TXN-{event_id}",
		"player_id": "PLAYER-001",
		"product_grant_id": "GRNT-001",
		"amount": 50.0,
		"currency": "EGP",
		"timestamp": "2026-02-27T12:00:00Z",
	}
	payload.update(overrides)
	return payload


@pytest.mark.asyncio
class TestWebhookEndpoints:
	"""Webhook transaction tests."""

	async def test_webhook_payment_accepted(self, app_client, redis_client, mock_frappe):
		"""Webhook payment event accepted returns 200."""
		try:
			payload = _make_webhook_payload("evt-001")

			resp = await app_client.post(
				"/api/v1/webhooks/payment",
				json=payload,
			)

			assert resp.status_code == 200
			data = resp.json()
			assert data.get("status") == "accepted"
		finally:
			await redis_client.delete(webhook_idempotency_key("evt-001"))

	async def test_webhook_payment_idempotent(self, app_client, redis_client, mock_frappe):
		"""Duplicate webhook event_id returns already_processed."""
		event_id = "evt-002"

		try:
			payload = _make_webhook_payload(event_id)

			resp1 = await app_client.post("/api/v1/webhooks/payment", json=payload)
			assert resp1.status_code == 200
			data1 = resp1.json()
			assert data1.get("status") == "accepted"

			# Second request (same event_id)
			resp2 = await app_client.post("/api/v1/webhooks/payment", json=payload)
			assert resp2.status_code == 200
			data2 = resp2.json()
			assert data2.get("status") == "already_processed"
		finally:
			await redis_client.delete(webhook_idempotency_key(event_id))

	async def test_webhook_payment_invalid_payload_422(self, app_client):
		"""Invalid webhook payload returns 422."""
		# Missing required fields
		resp = await app_client.post(
			"/api/v1/webhooks/payment",
			json={"event_id": "evt-003"},
		)

		assert resp.status_code == 422

	async def test_webhook_payment_no_auth_needed(self, app_client, redis_client, mock_frappe):
		"""Webhook endpoint accepts requests without auth."""
		event_id = "evt-004"
		try:
			payload = _make_webhook_payload(event_id)

			resp = await app_client.post(
				"/api/v1/webhooks/payment",
				json=payload,
			)

			# Should succeed without auth
			assert resp.status_code == 200
		finally:
			await redis_client.delete(webhook_idempotency_key(event_id))
