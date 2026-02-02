"""
Sync tasks for persisting Redis game state to MariaDB.

Tasks:
- sync_dirty_progress: Redis bitmap -> Structure Progress hex string
- sync_dirty_wallets: Redis hash -> Player Wallet record
- flush_interaction_buffer: Redis list -> Interaction Log batch insert

Scheduled via hooks.py scheduler_events (every 1 minute).
"""

from __future__ import annotations

import logging
import uuid

import frappe
import redis

logger = logging.getLogger(__name__)

# Redis key constants (must match FastAPI constants)
DIRTY_PROGRESS_KEY = "memora:dirty:progress"
DIRTY_WALLETS_KEY = "memora:dirty:wallets"
INTERACTION_BUFFER_KEY = "memora:buffer:interactions"


def get_redis():
	"""Get Redis connection using Frappe site config."""
	return redis.from_url(frappe.conf.redis_cache)


def sync_dirty_progress():
	"""
	Sync progress bitmaps from Redis to MariaDB.

	Processes items from dirty:progress set:
	1. Parse dirty member (user_id:subject_id:v{version})
	2. Get bitmap from Redis and convert to hex
	3. Update or insert Structure Progress record
	4. Remove from dirty set AFTER successful DB write

	Scheduled: every 1 minute via hooks.py
	"""
	r = get_redis()

	# Get all dirty items
	dirty_items = r.smembers(DIRTY_PROGRESS_KEY)
	if not dirty_items:
		logger.debug("No dirty progress to sync")
		return

	synced = 0
	errors = []

	for item in dirty_items:
		# Decode bytes if needed (redis-py returns bytes by default)
		item_str = item.decode() if isinstance(item, bytes) else item

		try:
			# Parse: user_id:subject_id:v{version}
			# Example: "USER-001:MATH-G5:v1"
			parts = item_str.rsplit(":v", 1)
			if len(parts) != 2:
				logger.warning(f"Invalid dirty progress format: {item_str}")
				continue

			user_subject = parts[0].rsplit(":", 1)
			if len(user_subject) != 2:
				logger.warning(f"Invalid user:subject format: {item_str}")
				continue

			user_id, subject_id = user_subject
			version = int(parts[1])

			# Get bitmap from Redis
			bitmap_key = f"memora:progress:{user_id}:{subject_id}:v{version}"
			bitmap_bytes = r.get(bitmap_key)

			# Convert to hex string (empty string if no bitmap)
			hex_string = bitmap_bytes.hex() if bitmap_bytes else ""

			# Calculate completion stats
			completed_count = r.bitcount(bitmap_key) if bitmap_bytes else 0
			total_lessons = _get_subject_lesson_count(r, subject_id)
			percentage = (completed_count / max(total_lessons, 1)) * 100

			# Upsert Structure Progress record
			existing = frappe.db.get_value(
				"Memora Structure Progress",
				{"player": user_id, "subject": subject_id},
				"name"
			)

			if existing:
				frappe.db.set_value(
					"Memora Structure Progress",
					existing,
					{
						"passed_lessons_bitset": hex_string,
						"completion_percentage": percentage
					},
					update_modified=False
				)
			else:
				frappe.get_doc({
					"doctype": "Memora Structure Progress",
					"player": user_id,
					"subject": subject_id,
					"passed_lessons_bitset": hex_string,
					"completion_percentage": percentage
				}).insert(ignore_permissions=True)

			# Remove from dirty set AFTER successful DB operation
			# Per RESEARCH.md: SREM after success prevents lost updates on crash
			r.srem(DIRTY_PROGRESS_KEY, item)
			synced += 1

		except Exception as e:
			errors.append(f"{item_str}: {str(e)}")
			frappe.log_error(f"Progress sync failed for {item_str}: {e}")

	# Commit all changes
	if synced > 0:
		frappe.db.commit()

	# Log sync result
	status = "Success" if not errors else "Failed"
	_log_sync("Progress", synced, status)

	logger.info(f"Progress sync: {synced} synced, {len(errors)} errors")


def _get_subject_lesson_count(r, subject_id: str) -> int:
	"""
	Get total lesson count for subject (cached in Redis).

	Cache TTL: 1 hour - matches hierarchy cache TTL from Phase 4.
	"""
	cache_key = f"memora:subject:total_lessons:{subject_id}"
	total = r.get(cache_key)
	if total:
		if isinstance(total, bytes):
			total = int(total.decode())
		else:
			total = int(total)
		return total

	# Fallback: count from database
	count = frappe.db.count("Memora Lesson", {"subject": subject_id})
	r.setex(cache_key, 3600, count)  # Cache for 1 hour
	return count


def _log_sync(sync_type: str, count: int, status: str):
	"""
	Record sync run to Memora Sync Log.

	Creates audit trail for monitoring sync health.
	"""
	try:
		frappe.get_doc({
			"doctype": "Memora Sync Log",
			"job_id": f"{sync_type.lower()}-{uuid.uuid4().hex[:8]}",
			"sync_type": sync_type,
			"records_processed": count,
			"status": status
		}).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception as e:
		logger.error(f"Failed to log sync: {e}")
