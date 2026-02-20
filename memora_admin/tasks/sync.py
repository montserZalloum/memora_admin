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
import os
from datetime import datetime

import frappe
import redis

from fastapi_app.core.constants import (
	DIRTY_PROGRESS_KEY,
	DIRTY_WALLETS_KEY,
	INTERACTION_BUFFER_KEY,
)

logger = logging.getLogger(__name__)

# Debug file for troubleshooting sync issues
DEBUG_LOG_FILE = "/tmp/memora_sync_debug.log"

def _write_debug_log(message: str):
	"""Write to debug log file for troubleshooting"""
	try:
		with open(DEBUG_LOG_FILE, 'a') as f:
			f.write(f"{datetime.now().isoformat()} - {message}\n")
	except Exception:
		pass  # Silent fail - don't break sync on log failures


def _parse_timestamp(timestamp_str: str) -> str:
	"""Convert ISO format timestamp to MariaDB format.

	Input: 2026-02-07T10:53:59.380Z (ISO 8601 with Z suffix)
	Output: 2026-02-07 10:53:59 (MariaDB format)
	"""
	if not timestamp_str:
		return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

	try:
		# Remove Z suffix if present
		if timestamp_str.endswith('Z'):
			timestamp_str = timestamp_str[:-1]

		# Parse the ISO format timestamp
		if '.' in timestamp_str:
			# Has milliseconds: 2026-02-07T10:53:59.380
			dt = datetime.fromisoformat(timestamp_str)
		else:
			# No milliseconds: 2026-02-07T10:53:59
			dt = datetime.fromisoformat(timestamp_str)

		# Return in MariaDB format: YYYY-MM-DD HH:MM:SS
		return dt.strftime("%Y-%m-%d %H:%M:%S")
	except Exception as e:
		logger.warning(f"Failed to parse timestamp {timestamp_str}: {e}")
		return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
	_write_debug_log("=== sync_dirty_progress STARTED ===")
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
			# IMPORTANT: Redis bit ordering convention
			# - Bit 0 = leftmost bit of first byte (0x80 = 0b10000000)
			# - Bit 7 = rightmost bit of first byte (0x01 = 0b00000001)
			# Example: hex "80" means bit 0 is set (lesson at index 0 completed)
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


def _reserve_name_block(prefix: str, count: int) -> int:
	"""Reserve a block of sequential names from tabSeries.

	Atomically increments the series counter and returns the start number.
	Names will be prefix + zero-padded numbers from (start+1) to (start+count).
	"""
	frappe.db.sql(
		"UPDATE `tabSeries` SET `current` = `current` + %(count)s WHERE `name` = %(prefix)s",
		{"count": count, "prefix": prefix},
	)
	current = frappe.db.sql(
		"SELECT `current` FROM `tabSeries` WHERE `name` = %(prefix)s",
		{"prefix": prefix},
	)[0][0]
	return current - count


def flush_interaction_buffer():
	"""
	Batch insert interactions from Redis buffer to MariaDB.

	Processes INTERACTION_BUFFER_KEY list:
	1. LRANGE to get batch of items
	2. Parse and validate all items (two-phase: parse then insert)
	3. Bulk INSERT valid rows via single raw SQL statement
	4. LTRIM to remove entire batch atomically

	Scheduled: every 1 minute via hooks.py
	"""
	_write_debug_log("=== flush_interaction_buffer STARTED ===")

	try:
		r = get_redis()

		BATCH_SIZE = 5000

		# Get batch of items from head of list
		items = r.lrange(INTERACTION_BUFFER_KEY, 0, BATCH_SIZE - 1)
		_write_debug_log(f"Found {len(items)} items in buffer")

		if not items:
			logger.debug("No interactions to flush")
			_write_debug_log("No items to flush - returning")
			return

		count = len(items)
		skipped = 0

		# Phase 1: Parse and validate all items
		valid_rows = []
		now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

		for i, item_bytes in enumerate(items):
			try:
				item_str = item_bytes.decode() if isinstance(item_bytes, bytes) else item_bytes
				item = json.loads(item_str)
			except (json.JSONDecodeError, UnicodeDecodeError) as e:
				skipped += 1
				logger.warning(f"Invalid JSON in interaction buffer: {e}")
				continue

			if not item.get("player") or not item.get("lesson"):
				skipped += 1
				logger.warning(f"Missing player or lesson in item: {str(item)[:100]}")
				continue

			valid_rows.append((
				item["player"],
				item["lesson"],
				str(item.get("stage_id", "")),
				item.get("item_id", ""),
				item.get("event_type", "Completed"),
				int(item.get("time_spent", 0)),
				int(item.get("errors_count", 0)),
				_parse_timestamp(item.get("timestamp", "")),
				json.dumps(item.get("metadata", {})),
				now_str,  # creation
				now_str,  # modified
				"Administrator",  # modified_by
				"Administrator",  # owner
			))

		inserted = 0

		# Phase 2: Bulk INSERT all valid rows in one SQL statement
		if valid_rows:
			n = len(valid_rows)
			start = _reserve_name_block("LOG-", n)

			# Build flat values list: (name, ...row_fields) for each row
			flat_values = []
			for i, row in enumerate(valid_rows):
				name = f"LOG-{start + i + 1:05d}"
				flat_values.append(name)
				flat_values.extend(row)

			placeholders = ", ".join(
				[f"({', '.join(['%s'] * 14)})"] * n
			)

			frappe.db.sql(
				f"""
				INSERT INTO `tabMemora Interaction Log`
				(name, player, lesson, stage_id, item_id, event_type,
				 time_spent, errors_count, timestamp, client_metadata,
				 creation, modified, modified_by, owner)
				VALUES {placeholders}
				""",
				tuple(flat_values),
			)
			frappe.db.commit()
			inserted = n
			_write_debug_log(f"Bulk inserted {n} rows")

		# Phase 3: Trim entire batch from buffer (invalid items won't succeed on retry)
		r.ltrim(INTERACTION_BUFFER_KEY, count, -1)
		_write_debug_log(f"Trimmed {count} items from buffer")

		# Log sync result
		status = "Success" if skipped == 0 else "Failed"
		_log_sync("Memory", inserted, status)

		_write_debug_log(f"=== COMPLETE: {inserted} inserted, {skipped} skipped ===\n")
		logger.info(f"Interaction flush: {inserted} inserted, {skipped} skipped")

	except Exception as e:
		error_msg = f"FATAL ERROR in flush_interaction_buffer: {e}"
		_write_debug_log(error_msg)
		logger.error(error_msg, exc_info=True)
		frappe.log_error(error_msg)


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
