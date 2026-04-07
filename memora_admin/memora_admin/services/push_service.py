"""Web Push notification service.

Sends W3C Push API notifications using pywebpush + VAPID.
Designed to run as a Frappe background job on the 'long' queue.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import frappe
from pywebpush import WebPushException, webpush

from fastapi_app.core.redis_keys import devices_key
from memora_admin.utils.redis_connection import get_memora_redis

logger = logging.getLogger(__name__)


def _chunks(lst, n):
	"""Yield successive n-sized chunks from lst."""
	for i in range(0, len(lst), n):
		yield lst[i : i + n]


def _send_one(sub_json: str, payload: str, private_key: str, vapid_claims: dict) -> str:
	"""Send a single push notification. Returns 'ok', 'stale', or 'error'."""
	try:
		sub_info = json.loads(sub_json)
		webpush(
			subscription_info=sub_info,
			data=payload,
			vapid_private_key=private_key,
			vapid_claims=vapid_claims,
		)
		return "ok"
	except WebPushException as e:
		if e.response is not None and e.response.status_code in (404, 410):
			return "stale"
		logger.warning("push_send_error: %s", e)
		return "error"
	except Exception:
		logger.exception("push_send_unexpected_error")
		return "error"


def send_push_notification(
	title: str,
	body: str,
	url: str | None = None,
	icon: str | None = None,
	target_players: list[str] | None = None,
	target_plans: list[str] | None = None,
	push_notification_name: str | None = None,
) -> dict:
	"""Send web push notification to targeted players.

	Args:
		title: Notification title.
		body: Notification body text.
		url: URL to open when notification is clicked.
		icon: Icon URL for the notification.
		target_players: Specific player IDs. None = use target_plans or all.
		target_plans: Filter by academic plan. None = all players.

	Returns:
		{"sent": int, "failed": int, "stale_removed": int}
	"""
	# 1. Load VAPID keys
	settings = frappe.get_single("Memora Settings")
	private_key = settings.get_password("vapid_private_key")
	if not private_key:
		logger.warning("VAPID keys not configured — skipping push send")
		return {"sent": 0, "failed": 0, "stale_removed": 0}

	vapid_email = settings.vapid_contact_email
	if not vapid_email:
		logger.warning("VAPID contact email not configured — skipping push send")
		return {"sent": 0, "failed": 0, "stale_removed": 0}

	vapid_claims = {"sub": f"mailto:{vapid_email}"}

	# 2. Build player list
	if target_players:
		players = target_players
	elif target_plans:
		players = frappe.get_all(
			"Memora Player Profile",
			filters={"plan": ["in", target_plans], "notifications": 1},
			pluck="name",
		)
	else:
		players = frappe.get_all(
			"Memora Player Profile",
			filters={"notifications": 1},
			pluck="name",
		)

	if not players:
		return {"sent": 0, "failed": 0, "stale_removed": 0}

	# 3. Read push subscriptions from Redis (pipelined HGETALL)
	r = get_memora_redis()
	subscriptions = []  # list of (user_id, field_name, sub_json)

	for batch in _chunks(players, 500):
		pipe = r.pipeline(transaction=False)
		for player_id in batch:
			pipe.hgetall(devices_key(player_id))
		results = pipe.execute()

		for player_id, device_data in zip(batch, results, strict=True):
			if not device_data:
				continue
			for field, value in device_data.items():
				if field.endswith(":push_sub"):
					subscriptions.append((player_id, field, value))

	if not subscriptions:
		return {"sent": 0, "failed": 0, "stale_removed": 0}

	# 4. Build payload
	payload = json.dumps(
		{
			"title": title,
			"body": body,
			"icon": icon or f"{frappe.utils.get_url()}/assets/memora_admin/images/memora-logo.png",
			"data": {
				"url": url,
			},
		}
	)

	# 5. Batch send with ThreadPoolExecutor
	sent = 0
	failed = 0
	stale_removed = 0

	with ThreadPoolExecutor(max_workers=10) as pool:
		for batch in _chunks(subscriptions, 500):
			futures = {
				pool.submit(_send_one, sub_json, payload, private_key, vapid_claims): (uid, field)
				for uid, field, sub_json in batch
			}
			for future in as_completed(futures):
				uid, field = futures[future]
				try:
					result = future.result()
				except Exception:
					logger.exception("push_future_error", uid=uid)
					failed += 1
					continue
				if result == "ok":
					sent += 1
				elif result == "stale":
					stale_removed += 1
					r.hdel(devices_key(uid), field)
				else:
					failed += 1

			time.sleep(1)  # pace between batches

	logger.info("push_send_complete: sent=%d, failed=%d, stale_removed=%d", sent, failed, stale_removed)

	# Write delivery stats back to the Push Notification DocType
	if push_notification_name:
		try:
			frappe.db.set_value(
				"Memora Push Notification",
				push_notification_name,
				{
					"sent_count": sent,
					"failed_count": failed,
					"stale_removed_count": stale_removed,
				},
				update_modified=False,
			)
			frappe.db.commit()
			frappe.publish_realtime(
				"push_delivery_complete",
				{"name": push_notification_name, "sent": sent, "failed": failed, "stale_removed": stale_removed},
				doctype="Memora Push Notification",
				docname=push_notification_name,
				after_commit=True,
			)
		except Exception:
			logger.exception("push_stats_write_failed for %s", push_notification_name)

	return {"sent": sent, "failed": failed, "stale_removed": stale_removed}
