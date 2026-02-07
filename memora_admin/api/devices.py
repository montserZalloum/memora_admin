"""Device management APIs for admin panel.

Provides whitelisted APIs for syncing device data from Redis to Frappe child table
and removing devices with session invalidation.

IMPORTANT: Uses get_fastapi_redis() (NOT frappe.cache()) to access the correct
Redis namespace shared with the FastAPI sidecar.
"""

import frappe
import redis

from memora_admin.events.access_sync import get_fastapi_redis


@frappe.whitelist()
def sync_devices_from_redis(player_name: str) -> list[dict]:
	"""Fetch live device data from Redis and populate the child table.

	Reads the memora:devices:{user_id} hash from Redis (written by FastAPI
	DeviceService) and populates the authorized_devices child table on the
	Memora Player Profile document.

	Args:
		player_name: Memora Player Profile docname

	Returns:
		List of device dicts synced from Redis

	Raises:
		frappe.throw: On Redis connection errors
	"""
	profile = frappe.get_doc("Memora Player Profile", player_name)
	user_id = profile.user

	try:
		r = get_fastapi_redis()
		devices_key = f"memora:devices:{user_id}"
		raw_data = r.hgetall(devices_key)
	except (redis.ConnectionError, redis.RedisError) as e:
		frappe.throw(f"Could not fetch live device data: {e}", title="Redis Error")

	# Parse hash fields into device dicts
	# Field format: device:{id}:{attr} where attr is name, ua, platform, last_login, fingerprint, push_token
	devices = {}
	for field_bytes, value_bytes in raw_data.items():
		field = field_bytes.decode() if isinstance(field_bytes, bytes) else field_bytes
		value = value_bytes.decode() if isinstance(value_bytes, bytes) else value_bytes

		# Parse field: "device:{id}:{attr}"
		parts = field.split(":", 2)
		if len(parts) != 3 or parts[0] != "device":
			continue

		device_id = parts[1]
		attr = parts[2]

		if device_id not in devices:
			devices[device_id] = {"device_id": device_id}

		# Map Redis field names to child table field names
		if attr == "name":
			devices[device_id]["device_name"] = value
		elif attr == "ua":
			devices[device_id]["user_agent"] = value
		else:
			# platform, last_login, fingerprint, push_token map directly
			devices[device_id][attr] = value

	# Clear existing child table and repopulate
	profile.authorized_devices = []
	device_list = []

	for device_id, device_data in devices.items():
		profile.append("authorized_devices", {
			"device_id": device_data.get("device_id", ""),
			"device_name": device_data.get("device_name", ""),
			"platform": device_data.get("platform", "Web"),
			"last_login": device_data.get("last_login", ""),
			"user_agent": device_data.get("user_agent", ""),
			"push_token": device_data.get("push_token", ""),
		})
		device_list.append(device_data)

	profile.save(ignore_permissions=True)
	frappe.logger().info(f"Synced {len(device_list)} devices from Redis for {user_id}")

	return device_list


@frappe.whitelist()
def remove_device(player_name: str, device_id: str) -> dict:
	"""Remove a device from Redis and invalidate the player's session.

	Deletes all hash fields for the specified device from the Redis devices
	hash, then deletes the session key to force immediate re-login.

	Args:
		player_name: Memora Player Profile docname
		device_id: The device ID to remove

	Returns:
		Dict with success status and device_id

	Raises:
		frappe.throw: On Redis connection errors
	"""
	profile = frappe.get_doc("Memora Player Profile", player_name)
	user_id = profile.user

	try:
		r = get_fastapi_redis()
		devices_key = f"memora:devices:{user_id}"
		session_key = f"memora:session:{user_id}"

		# Build field list for deletion (all 6 attributes per device)
		fields = [
			f"device:{device_id}:name",
			f"device:{device_id}:ua",
			f"device:{device_id}:platform",
			f"device:{device_id}:last_login",
			f"device:{device_id}:fingerprint",
			f"device:{device_id}:push_token",
		]

		# Delete device fields from hash
		deleted = r.hdel(devices_key, *fields)

		# Invalidate session to force re-login
		r.delete(session_key)

		frappe.logger().info(
			f"Device {device_id} removed from Redis for {user_id} (deleted={deleted}), session invalidated"
		)
	except (redis.ConnectionError, redis.RedisError) as e:
		frappe.throw(f"Could not remove device from Redis: {e}", title="Redis Error")

	return {"success": deleted > 0, "device_id": device_id}
