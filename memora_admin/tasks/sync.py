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

from fastapi_app.core.constants import (
	DIRTY_PROGRESS_KEY,
	DIRTY_WALLETS_KEY,
	INTERACTION_BUFFER_KEY,
)
from fastapi_app.core.redis_keys import (
	daily_xp_key,
	freeze_key,
	progress_key as _progress_key,
	subject_total_lessons_key,
	wallet_key,
)
from memora_admin.utils.redis_connection import get_memora_redis, get_memora_redis_raw

logger = logging.getLogger(__name__)

# Debug file for troubleshooting sync issues
DEBUG_LOG_FILE = "/tmp/memora_sync_debug.log"

# Maximum items to process per DB transaction (chunk)
SYNC_CHUNK_SIZE = 500

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
		return frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")

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
		return frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------------------------------------------------
# Shared batch helpers
# ---------------------------------------------------------------------------

def _chunks(lst, size):
	"""Split list into chunks of given size."""
	for i in range(0, len(lst), size):
		yield lst[i:i + size]


def _batch_srem(r, set_key, members):
	"""Pipeline SREM to remove multiple members from a set in one round-trip."""
	if not members:
		return
	pipe = r.pipeline(transaction=False)
	for member in members:
		pipe.srem(set_key, member)
	pipe.execute()


# ---------------------------------------------------------------------------
# Wallet batch helpers
# ---------------------------------------------------------------------------

def _batch_hgetall_wallets(r, player_ids):
	"""Pipeline HGETALL for multiple wallet keys. Returns {player_id: hash_dict}."""
	pipe = r.pipeline(transaction=False)
	for pid in player_ids:
		pipe.hgetall(wallet_key(pid))
	results = pipe.execute()
	return dict(zip(player_ids, results))


def _batch_hgetall_daily_xp(r, player_ids):
	"""Pipeline HGETALL for multiple daily_xp keys. Returns {player_id: hash_dict}."""
	pipe = r.pipeline(transaction=False)
	for pid in player_ids:
		pipe.hgetall(daily_xp_key(pid))
	results = pipe.execute()
	return dict(zip(player_ids, results))


def _bulk_lookup_wallet_names(player_ids):
	"""Single SELECT ... WHERE IN to map player_id -> wallet record name."""
	if not player_ids:
		return {}
	placeholders = ", ".join(["%s"] * len(player_ids))
	rows = frappe.db.sql(
		f"SELECT name, player FROM `tabMemora Player Wallet` WHERE player IN ({placeholders})",
		tuple(player_ids),
		as_dict=True,
	)
	return {row["player"]: row["name"] for row in rows}


def _batch_update_wallets(updates):
	"""CASE/WHEN UPDATE for multiple wallets in one query.

	Args:
		updates: list of (wallet_name, xp, streak, daily_xp_json)
		         daily_xp_json is a JSON string or None (skip update for that player)
	"""
	if not updates:
		return

	now_str = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")

	xp_when = " ".join(["WHEN %s THEN %s"] * len(updates))
	streak_when = " ".join(["WHEN %s THEN %s"] * len(updates))
	in_placeholders = ", ".join(["%s"] * len(updates))

	xp_params = []
	streak_params = []
	names = []
	daily_xp_updates = []  # (wallet_name, json_str) for players with non-empty data
	for wallet_name, xp, streak, daily_xp_json in updates:
		xp_params.extend([wallet_name, xp])
		streak_params.extend([wallet_name, streak])
		names.append(wallet_name)
		if daily_xp_json is not None:
			daily_xp_updates.append((wallet_name, daily_xp_json))

	# Build daily_xp_json CASE/WHEN only for players that have data;
	# use ELSE daily_xp_json to preserve existing values for others.
	daily_xp_sql = ""
	daily_xp_params = []
	if daily_xp_updates:
		daily_xp_when = " ".join(["WHEN %s THEN %s"] * len(daily_xp_updates))
		for wallet_name, json_str in daily_xp_updates:
			daily_xp_params.extend([wallet_name, json_str])
		daily_xp_sql = f",\n\t\t\tdaily_xp_json = CASE name {daily_xp_when} ELSE daily_xp_json END"

	sql = f"""
		UPDATE `tabMemora Player Wallet`
		SET total_xp = CASE name {xp_when} END,
			current_streak = CASE name {streak_when} END{daily_xp_sql},
			dirty_flag = 0,
			last_sync_at = %s
		WHERE name IN ({in_placeholders})
	"""

	all_params = xp_params + streak_params + daily_xp_params + [now_str] + names
	frappe.db.sql(sql, tuple(all_params))


# ---------------------------------------------------------------------------
# Progress batch helpers
# ---------------------------------------------------------------------------

def _batch_get_bitmaps(r_raw, bitmap_keys):
	"""Pipeline GET + BITCOUNT for multiple bitmap keys.

	Uses a raw (non-decoding) Redis client for GET because bitmap values
	are raw binary data that cannot be decoded as UTF-8.
	BITCOUNT returns an integer so it's safe with either client.

	Returns: {bitmap_key: (hex_string, completed_count)}
	"""
	if not bitmap_keys:
		return {}

	# Caller provides the raw client explicitly so this helper does not
	# bypass the active Redis connection context.
	pipe = r_raw.pipeline(transaction=False)
	for key in bitmap_keys:
		pipe.get(key)
		pipe.bitcount(key)
	results = pipe.execute()

	out = {}
	for i, key in enumerate(bitmap_keys):
		bitmap_bytes = results[i * 2]
		bitcount = results[i * 2 + 1]
		hex_string = bitmap_bytes.hex() if bitmap_bytes else ""
		completed_count = bitcount if bitmap_bytes else 0
		out[key] = (hex_string, completed_count)
	return out


def _batch_get_subject_lesson_counts(r, subject_ids):
	"""Get lesson counts for multiple subjects via pipeline + single DB fallback.

	Returns: {subject_id: lesson_count}
	"""
	if not subject_ids:
		return {}

	# Pipeline GET for all cache keys
	unique_ids = list(set(subject_ids))
	cache_keys = [subject_total_lessons_key(sid) for sid in unique_ids]
	pipe = r.pipeline(transaction=False)
	for key in cache_keys:
		pipe.get(key)
	results = pipe.execute()

	counts = {}
	missing_subjects = []
	for sid, val in zip(unique_ids, results):
		if val is not None:
			counts[sid] = int(val.decode() if isinstance(val, bytes) else val)
		else:
			missing_subjects.append(sid)

	# Single DB query for cache misses
	if missing_subjects:
		placeholders = ", ".join(["%s"] * len(missing_subjects))
		rows = frappe.db.sql(
			f"SELECT subject, COUNT(*) as cnt FROM `tabMemora Lesson` WHERE subject IN ({placeholders}) GROUP BY subject",
			tuple(missing_subjects),
			as_dict=True,
		)
		db_counts = {row["subject"]: row["cnt"] for row in rows}

		# Cache results and fill in missing (subjects not in DB get 0)
		pipe = r.pipeline(transaction=False)
		for sid in missing_subjects:
			count = db_counts.get(sid, 0)
			counts[sid] = count
			pipe.setex(subject_total_lessons_key(sid), 3600, count)
		pipe.execute()

	return counts


def _bulk_lookup_progress_records(items):
	"""Single SELECT for existing progress records.

	Args:
		items: list of (user_id, subject_id)
	Returns:
		{(user_id, subject_id): record_name}
	"""
	if not items:
		return {}
	conditions = " OR ".join(["(player = %s AND subject = %s)"] * len(items))
	params = []
	for user_id, subject_id in items:
		params.extend([user_id, subject_id])

	rows = frappe.db.sql(
		f"SELECT name, player, subject FROM `tabMemora Structure Progress` WHERE {conditions}",
		tuple(params),
		as_dict=True,
	)
	return {(row["player"], row["subject"]): row["name"] for row in rows}


def _batch_update_progress(updates):
	"""CASE/WHEN UPDATE for existing progress records.

	Args:
		updates: list of (record_name, hex_string, percentage)
	"""
	if not updates:
		return

	bitset_when = " ".join(["WHEN %s THEN %s"] * len(updates))
	pct_when = " ".join(["WHEN %s THEN %s"] * len(updates))
	in_placeholders = ", ".join(["%s"] * len(updates))

	bitset_params = []
	pct_params = []
	names = []
	for record_name, hex_string, percentage in updates:
		bitset_params.extend([record_name, hex_string])
		pct_params.extend([record_name, percentage])
		names.append(record_name)

	sql = f"""
		UPDATE `tabMemora Structure Progress`
		SET passed_lessons_bitset = CASE name {bitset_when} END,
			completion_percentage = CASE name {pct_when} END
		WHERE name IN ({in_placeholders})
	"""

	all_params = bitset_params + pct_params + names
	frappe.db.sql(sql, tuple(all_params))


def _batch_insert_progress(inserts):
	"""Multi-row INSERT for new progress records.

	Args:
		inserts: list of (user_id, subject_id, hex_string, percentage)
	"""
	if not inserts:
		return

	n = len(inserts)
	start = _reserve_name_block("PROG-", n)
	now_str = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")

	flat_values = []
	for i, (user_id, subject_id, hex_string, percentage) in enumerate(inserts):
		name = f"PROG-{start + i + 1:05d}"
		flat_values.extend([
			name, user_id, subject_id, hex_string, percentage,
			0,  # docstatus
			now_str, now_str, "Administrator", "Administrator",
		])

	placeholders = ", ".join(
		[f"({', '.join(['%s'] * 10)})"] * n
	)

	frappe.db.sql(
		f"""
		INSERT INTO `tabMemora Structure Progress`
		(name, player, subject, passed_lessons_bitset, completion_percentage,
		 docstatus, creation, modified, modified_by, owner)
		VALUES {placeholders}
		""",
		tuple(flat_values),
	)


# ---------------------------------------------------------------------------
# Main sync functions
# ---------------------------------------------------------------------------

def sync_dirty_progress():
	"""
	Sync progress bitmaps from Redis to MariaDB (batch processing).

	Processes items from dirty:progress set in chunks of SYNC_CHUNK_SIZE:
	1. SMEMBERS + parse all dirty members upfront
	2. Per chunk: pipeline GET+BITCOUNT, batch lesson counts,
	   single SELECT for existing records, CASE/WHEN UPDATE + multi-row INSERT
	3. Pipeline SREM after successful DB commit

	Scheduled: every 1 minute via hooks.py
	"""
	_write_debug_log("=== sync_dirty_progress STARTED ===")
	r = get_memora_redis()
	r_raw = get_memora_redis_raw()

	# Get all dirty items
	dirty_items = r.smembers(DIRTY_PROGRESS_KEY)
	if not dirty_items:
		logger.debug("No dirty progress to sync")
		return

	# Phase 1: Parse all members upfront, skip invalid formats
	parsed_items = []  # list of (raw_member, user_id, subject_id, version)
	for item in dirty_items:
		item_str = item.decode() if isinstance(item, bytes) else item

		parts = item_str.rsplit(":v", 1)
		if len(parts) != 2:
			logger.warning(f"Invalid dirty progress format: {item_str}")
			continue

		user_subject = parts[0].rsplit(":", 1)
		if len(user_subject) != 2:
			logger.warning(f"Invalid user:subject format: {item_str}")
			continue

		try:
			version = int(parts[1])
		except ValueError:
			logger.warning(f"Invalid version in dirty progress: {item_str}")
			continue

		user_id, subject_id = user_subject
		parsed_items.append((item, user_id, subject_id, version))

	if not parsed_items:
		return

	synced = 0
	errors = []

	# Phase 2: Process in chunks
	for chunk in _chunks(parsed_items, SYNC_CHUNK_SIZE):
		try:
			# 0. Filter out frozen players (plan change in progress)
			active_chunk = []
			frozen_count = 0
			for item in chunk:
				_, uid, _, _ = item
				if r.exists(freeze_key(uid)):
					frozen_count += 1
				else:
					active_chunk.append(item)
			if frozen_count:
				logger.info(f"Progress sync: skipped {frozen_count} frozen entries")
			if not active_chunk:
				continue
			chunk = active_chunk

			# 1. Pipeline GET + BITCOUNT for all bitmap keys
			bitmap_keys = [
				_progress_key(uid, sid, ver)
				for _, uid, sid, ver in chunk
			]
			bitmap_data = _batch_get_bitmaps(r_raw, bitmap_keys)

			# 2. Batch get subject lesson counts
			subject_ids = list({sid for _, _, sid, _ in chunk})
			lesson_counts = _batch_get_subject_lesson_counts(r, subject_ids)

			# 3. Build per-item data (hex_string, percentage)
			item_data = []  # (raw_member, user_id, subject_id, hex_string, percentage)
			for (raw_member, uid, sid, ver), bkey in zip(chunk, bitmap_keys):
				hex_string, completed_count = bitmap_data[bkey]
				total_lessons = lesson_counts.get(sid, 0)
				percentage = (completed_count / max(total_lessons, 1)) * 100
				item_data.append((raw_member, uid, sid, hex_string, percentage))

			# 4. Single SELECT for existing records
			lookup_pairs = [(uid, sid) for _, uid, sid, _, _ in item_data]
			existing_records = _bulk_lookup_progress_records(lookup_pairs)

			# 5. Partition into UPDATE vs INSERT
			updates = []  # (record_name, hex_string, percentage)
			inserts = []  # (user_id, subject_id, hex_string, percentage)
			for _, uid, sid, hex_string, percentage in item_data:
				record_name = existing_records.get((uid, sid))
				if record_name:
					updates.append((record_name, hex_string, percentage))
				else:
					inserts.append((uid, sid, hex_string, percentage))

			# 6. Execute batch DB operations
			_batch_update_progress(updates)
			_batch_insert_progress(inserts)

			# 7. Commit chunk
			frappe.db.commit()
			synced += len(chunk)

			# 8. Pipeline SREM for all processed items
			raw_members = [raw for raw, _, _, _, _ in item_data]
			_batch_srem(r, DIRTY_PROGRESS_KEY, raw_members)

		except Exception as e:
			errors.append(f"chunk of {len(chunk)}: {str(e)}")
			frappe.log_error(f"Progress sync failed for chunk: {e}")

	# Log sync result
	status = "Success" if not errors else "Failed"
	_log_sync("Progress", synced, status)

	logger.info(f"Progress sync: {synced} synced, {len(errors)} errors")


def sync_dirty_wallets():
	"""
	Sync wallets from Redis to MariaDB (batch processing).

	Processes items from dirty:wallets set in chunks of SYNC_CHUNK_SIZE:
	1. SMEMBERS + decode all player IDs upfront
	2. Per chunk: pipeline HGETALL, single SELECT for wallet names,
	   CASE/WHEN UPDATE, commit, pipeline SREM
	3. Players without Redis data removed from dirty set immediately

	Scheduled: every 1 minute via hooks.py
	"""
	r = get_memora_redis()

	# Get all dirty players
	dirty_players = r.smembers(DIRTY_WALLETS_KEY)
	if not dirty_players:
		logger.debug("No dirty wallets to sync")
		return

	# Decode all player IDs upfront
	player_ids = [
		pid.decode() if isinstance(pid, bytes) else pid
		for pid in dirty_players
	]

	synced = 0
	errors = []

	for chunk in _chunks(player_ids, SYNC_CHUNK_SIZE):
		try:
			# 0. Filter out frozen players (plan change in progress)
			frozen_players = []
			active_chunk = []
			for pid in chunk:
				if r.exists(freeze_key(pid)):
					frozen_players.append(pid)
				else:
					active_chunk.append(pid)
			if frozen_players:
				logger.info(f"Wallet sync: skipped {len(frozen_players)} frozen players")
			if not active_chunk:
				continue
			chunk = active_chunk

			# 1. Pipeline HGETALL for all wallets in chunk
			wallet_data = _batch_hgetall_wallets(r, chunk)

			# 2. Filter out players with no Redis wallet data
			players_with_data = []
			players_without_data = []
			for pid in chunk:
				if wallet_data.get(pid):
					players_with_data.append(pid)
				else:
					players_without_data.append(pid)

			# Remove players without data from dirty set immediately
			if players_without_data:
				_batch_srem(r, DIRTY_WALLETS_KEY, players_without_data)

			if not players_with_data:
				continue

			# 3a. Pipeline HGETALL for daily_xp hashes (single RTT alongside wallet fetch)
			daily_xp_data = _batch_hgetall_daily_xp(r, players_with_data)

			# 3b. Single SELECT to get wallet names
			wallet_names = _bulk_lookup_wallet_names(players_with_data)

			# 4. Build update list
			updates = []
			missing_wallets = []
			for pid in players_with_data:
				wallet_name = wallet_names.get(pid)
				if not wallet_name:
					logger.warning(f"No wallet record found for player {pid}")
					missing_wallets.append(pid)
					continue

				data = wallet_data[pid]
				xp_raw = data.get(b"xp") or data.get("xp")
				streak_raw = data.get(b"streak") or data.get("streak")
				xp = int(xp_raw) if xp_raw else 0
				streak = int(streak_raw) if streak_raw else 0

				# Serialize daily_xp hash to JSON for MariaDB persistence
				# MERGE with existing MariaDB data to avoid overwriting historical
				# data after Redis flush (Redis may only have post-flush entries).
				raw_daily_xp = daily_xp_data.get(pid, {})
				if raw_daily_xp:
					redis_daily_xp = {
						(k.decode() if isinstance(k, bytes) else k): int(v)
						for k, v in raw_daily_xp.items()
					}
					# Read existing DB value and merge (Redis wins on conflicts)
					existing_json = frappe.db.get_value(
						"Memora Player Wallet", wallet_name, "daily_xp_json"
					)
					if existing_json:
						try:
							existing = json.loads(existing_json)
						except (json.JSONDecodeError, TypeError):
							existing = {}
						# Merge: existing as base, Redis overwrites matching dates
						existing.update(redis_daily_xp)
						daily_xp_json_str = json.dumps(existing)
					else:
						daily_xp_json_str = json.dumps(redis_daily_xp)
				else:
					daily_xp_json_str = None  # No data — preserve existing DB value

				updates.append((wallet_name, xp, streak, daily_xp_json_str))

			# 5. Single CASE/WHEN UPDATE
			_batch_update_wallets(updates)
			synced += len(updates)

			# 6. Commit chunk
			frappe.db.commit()

			# 7. Pipeline SREM for all processed players (including missing wallets)
			_batch_srem(r, DIRTY_WALLETS_KEY, players_with_data)

		except Exception as e:
			errors.append(f"chunk of {len(chunk)}: {str(e)}")
			frappe.log_error(f"Wallet sync failed for chunk: {e}")

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
		r = get_memora_redis()

		batch_size = 5000

		# Log critical alert on large backlog (monitor threshold: 10000)
		buffer_len = r.llen(INTERACTION_BUFFER_KEY)
		if buffer_len > 10000:
			frappe.logger().critical(f"redis_buffer_backlog buffer_len={buffer_len}")

		# Get batch of items from head of list
		items = r.lrange(INTERACTION_BUFFER_KEY, 0, batch_size - 1)
		_write_debug_log(f"Found {len(items)} items in buffer")

		if not items:
			logger.debug("No interactions to flush")
			_write_debug_log("No items to flush - returning")
			return

		count = len(items)
		skipped = 0

		# Phase 1: Parse and validate all items
		valid_rows = []
		now_str = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S.%f")

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
	cache_key = subject_total_lessons_key(subject_id)
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
