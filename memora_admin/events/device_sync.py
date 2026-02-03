# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Device sync events for admin device management."""

import frappe


def on_player_profile_update(doc, method):
	"""
	Sync device removal to Redis when admin updates player profile.

	Per CONTEXT.md:
	- When admin removes a device, session is immediately invalidated
	- Removed devices are deleted completely (no history)

	This compares the current authorized_devices with the previous state
	to detect removed devices.
	"""
	if not doc.authorized_devices:
		# No devices on profile - nothing to sync
		return

	# Get the previous state of the document
	previous_doc = doc.get_doc_before_save()
	if not previous_doc:
		return

	# Find removed devices (were in previous, not in current)
	current_device_ids = {d.device_id for d in doc.authorized_devices}
	previous_device_ids = (
		{d.device_id for d in previous_doc.authorized_devices} if previous_doc.authorized_devices else set()
	)

	removed_devices = previous_device_ids - current_device_ids

	if not removed_devices:
		return

	user_id = doc.user
	cache = frappe.cache()
	devices_key = f"memora:devices:{user_id}"
	session_key = f"memora:session:{user_id}"

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
		cache.hdel(devices_key, *fields_to_delete)

		frappe.logger().info(f"Device {device_id} removed from Redis for user {user_id}")

	# Invalidate session to force re-login
	# Per CONTEXT.md: removed device gets kicked out immediately
	cache.delete_value(session_key)
	frappe.logger().info(f"Session invalidated for user {user_id} after device removal")
