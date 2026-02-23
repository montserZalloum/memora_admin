# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Device sync events for admin device management.

Syncs device removal from Frappe child table to Redis when admin
updates a player profile. Uses get_fastapi_redis() for correct
Redis namespace (shared with FastAPI sidecar).
"""
# Player identity is PLAYER-##### docname (not email). See Phase 32.

import frappe
import redis

from fastapi_app.core.redis_keys import devices_key, session_key
from memora_admin.events.access_sync import get_fastapi_redis


def on_player_profile_update(doc, method):
	"""
	Sync device removal to Redis when admin updates player profile.

	Per CONTEXT.md:
	- When admin removes a device, session is immediately invalidated
	- Removed devices are deleted completely (no history)

	This compares the current authorized_devices with the previous state
	to detect removed devices. Works even when all devices are removed
	(empty child table).
	"""
	# Get the previous state of the document
	previous_doc = doc.get_doc_before_save()
	if not previous_doc:
		return

	# Find removed devices (were in previous, not in current)
	current_device_ids = {d.device_id for d in doc.authorized_devices} if doc.authorized_devices else set()
	previous_device_ids = (
		{d.device_id for d in previous_doc.authorized_devices} if previous_doc.authorized_devices else set()
	)

	removed_devices = previous_device_ids - current_device_ids

	if not removed_devices:
		return

	user_id = doc.name
	dk = devices_key(user_id)
	sk = session_key(user_id)

	try:
		r = get_fastapi_redis()

		for device_id in removed_devices:
			# Remove device from Redis registry
			fields_to_delete = [
				f"device:{device_id}:name",
				f"device:{device_id}:ua",
				f"device:{device_id}:platform",
				f"device:{device_id}:last_login",
				f"device:{device_id}:fingerprint",
				f"device:{device_id}:push_token",
			]
			r.hdel(dk, *fields_to_delete)

			frappe.logger().info(f"Device {device_id} removed from Redis for user {user_id}")

		# Invalidate session to force re-login
		# Per CONTEXT.md: removed device gets kicked out immediately
		r.delete(sk)
		frappe.logger().info(f"Session invalidated for user {user_id} after device removal")
	except (redis.ConnectionError, redis.RedisError) as e:
		frappe.log_error(
			f"Redis error during device sync for {user_id}: {e}",
			"Device Sync Redis Error",
		)
