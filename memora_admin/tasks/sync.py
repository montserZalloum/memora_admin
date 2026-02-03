"""
Sync tasks for persisting Redis game state to MariaDB.

Tasks:
- sync_dirty_progress: Redis bitmap -> Structure Progress hex string
- sync_dirty_wallets: Redis hash -> Player Wallet record
- flush_interaction_buffer: Redis list -> Interaction Log batch insert

Scheduled via hooks.py scheduler_events (every 1 minute).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

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


def sync_dirty_wallets():
	"""
	Sync wallets from Redis to MariaDB.

	Processes items from dirty:wallets set:
	1. Get wallet hash from Redis (xp, streak, streak_date)
	2. Update Player Wallet record
	3. Remove from dirty set AFTER successful DB write

	Scheduled: every 1 minute via hooks.py
	"""
	r = get_redis()

	# Get all dirty players
	dirty_players = r.smembers(DIRTY_WALLETS_KEY)
	if not dirty_players:
		logger.debug("No dirty wallets to sync")
		return

	synced = 0
	errors = []

	for player_id in dirty_players:
		# Decode bytes if needed
		player_id = player_id.decode() if isinstance(player_id, bytes) else player_id

		try:
			# Get wallet data from Redis hash
			wallet_key = f"memora:wallet:{player_id}"
			wallet_data = r.hgetall(wallet_key)

			if not wallet_data:
				# No wallet data in Redis - remove from dirty set
				r.srem(DIRTY_WALLETS_KEY, player_id)
				continue

			# Parse wallet values (handle bytes from Redis)
			xp_raw = wallet_data.get(b"xp") or wallet_data.get("xp")
			streak_raw = wallet_data.get(b"streak") or wallet_data.get("streak")

			xp = int(xp_raw) if xp_raw else 0
			streak = int(streak_raw) if streak_raw else 0

			# Find existing wallet record
			wallet_name = frappe.db.get_value("Memora Player Wallet", {"player": player_id}, "name")

			if wallet_name:
				# Update existing wallet
				frappe.db.set_value(
					"Memora Player Wallet",
					wallet_name,
					{
						"total_xp": xp,
						"current_streak": streak,
						"dirty_flag": 0,
						"last_sync_at": datetime.now(),
					},
					update_modified=False,
				)
				synced += 1
			else:
				# Player wallet should exist - log warning but continue
				logger.warning(f"No wallet record found for player {player_id}")

			# Remove from dirty set AFTER successful operation
			r.srem(DIRTY_WALLETS_KEY, player_id)

		except Exception as e:
			errors.append(f"{player_id}: {str(e)}")
			frappe.log_error(f"Wallet sync failed for {player_id}: {e}")

	# Commit all changes
	if synced > 0:
		frappe.db.commit()

	# Log sync result
	status = "Success" if not errors else "Failed"
	_log_sync("Wallet", synced, status)

	logger.info(f"Wallet sync: {synced} synced, {len(errors)} errors")


def flush_interaction_buffer():
	"""
	Batch insert interactions from Redis buffer to MariaDB.

	Processes INTERACTION_BUFFER_KEY list:
	1. LRANGE to get batch of items (limit 1000)
	2. JSON parse and insert to Interaction Log
	3. LTRIM to remove processed items atomically

	Scheduled: every 1 minute via hooks.py

	Per RESEARCH.md: Fixed batch size (1000) prevents memory spikes.
	"""
	r = get_redis()

	# Batch size limit to prevent memory issues
	BATCH_SIZE = 1000

	# Get batch of items from head of list
	items = r.lrange(INTERACTION_BUFFER_KEY, 0, BATCH_SIZE - 1)
	if not items:
		logger.debug("No interactions to flush")
		return

	count = len(items)
	inserted = 0
	errors = []

	for item_bytes in items:
		try:
			# Parse JSON (handle bytes from Redis)
			item_str = item_bytes.decode() if isinstance(item_bytes, bytes) else item_bytes
			item = json.loads(item_str)

			# Insert to Interaction Log DocType
			frappe.get_doc({
				"doctype": "Memora Interaction Log",
				"player": item["player"],
				"lesson": item["lesson"],
				"stage_id": str(item.get("stage_id", "")),
				"event_type": item.get("event_type", "Completed"),
				"time_spent": item.get("time_spent", 0),
				"errors_count": item.get("errors_count", 0),
				"timestamp": item.get("timestamp", datetime.now().isoformat()),
				"client_metadata": json.dumps(item.get("metadata", {})),
			}).insert(ignore_permissions=True)
			inserted += 1

		except Exception as e:
			errors.append(str(e))
			frappe.log_error(f"Insert interaction failed: {e}")

	# Trim processed items from list (atomic operation)
	# LTRIM keeps elements from count to end, removing processed ones
	r.ltrim(INTERACTION_BUFFER_KEY, count, -1)

	# Commit all inserts
	if inserted > 0:
		frappe.db.commit()

	# Log sync result - "Memory" is the sync_type for interactions per DocType schema
	status = "Success" if not errors else "Failed"
	_log_sync("Memory", inserted, status)

	logger.info(f"Interaction flush: {inserted} inserted, {len(errors)} errors")


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
