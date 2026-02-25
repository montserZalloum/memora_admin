"""
Hourly session cleanup task for orphaned session keys.

Per CONTEXT.md and Phase 9 (09-01):
- Redis TTL handles NORMAL session expiration (1-hour per Phase 9)
- This cleanup is a SAFETY NET for edge cases only:
  - Keys with corrupted/missing TTL (TTL -1)
  - Keys from unexpected termination scenarios

Note: This does NOT handle TTL-expired keys - Redis automatically removes
those when TTL reaches 0. This ONLY catches orphaned keys (TTL -1) that
somehow lost their expiration.

Scheduled via hooks.py: "15 * * * *"
"""

from __future__ import annotations

import logging


import frappe

from fastapi_app.core.redis_keys import GAME_SESSION_SCAN_PATTERN
from memora_admin.tasks.task_utils import (
	TASK_DURATION,
	TASK_RUNS,
	log_task_run,
	notify_admins,
)
from memora_admin.utils.redis_connection import get_memora_redis

logger = logging.getLogger(__name__)

TASK_NAME = "session_cleanup"


def cleanup_expired_sessions(triggered_by: str = "Scheduler"):
	"""Remove orphaned session keys that somehow lost their TTL.

	Per Phase 9 (09-01):
	- Game sessions have 1-hour TTL (3600s)
	- Normal expiration handled by Redis automatically
	- This catches edge cases: TTL -1 (no expiry) or corrupted keys

	IMPORTANT: This is a SAFETY NET, not the primary cleanup mechanism.
	Redis TTL handles normal session expiry. This only removes keys that
	exist but have NO TTL set (TTL == -1), indicating an orphaned key.

	Args:
		triggered_by: Source of trigger - "Scheduler", "Manual", or "Catch-up"
	"""
	start_time = frappe.utils.now_datetime()

	try:
		checked, removed, orphaned_keys = _do_session_cleanup()

		status = "Success"

		log_task_run(
			task_name=TASK_NAME,
			status=status,
			processed=checked,
			failed=removed,  # Using failed_count to track removals for observability
			failed_details=orphaned_keys if orphaned_keys else None,
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=TASK_NAME, status="success").inc()
		logger.info(f"{TASK_NAME}: checked {checked}, removed {removed} orphaned keys (TTL -1 only)")

	except Exception as e:
		logger.critical(f"{TASK_NAME} failed: {e}")

		log_task_run(
			task_name=TASK_NAME,
			status="Failed",
			error_message=str(e),
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=TASK_NAME, status="failed").inc()
		notify_admins(TASK_NAME, str(e))
		raise

	finally:
		duration = (frappe.utils.now_datetime() - start_time).total_seconds()
		TASK_DURATION.labels(task_name=TASK_NAME).observe(duration)


def _do_session_cleanup() -> tuple[int, int, list]:
	"""Execute session cleanup logic.

	Returns:
		Tuple of (checked_count, removed_count, orphaned_key_list)
	"""
	r = get_memora_redis()
	checked = 0
	removed = 0
	orphaned_keys = []

	cursor = 0
	while True:
		# SCAN with pattern match for session keys
		# Per Phase 9: memora:gamesession:{user_id}
		cursor, keys = r.scan(
			cursor,
			match=GAME_SESSION_SCAN_PATTERN,
			count=100,  # Smaller batches since sessions should be fewer than wallets
		)

		for key in keys:
			checked += 1
			key_str = key.decode() if isinstance(key, bytes) else key

			# Check TTL
			ttl = r.ttl(key)

			if ttl == -1:
				# Key exists but has no TTL - should never happen
				# This is an orphaned key that needs cleanup
				r.delete(key)
				removed += 1
				orphaned_keys.append({
					"key": key_str,
					"reason": "no_ttl",
				})
				logger.warning(f"Removed session key without TTL: {key_str}")

			# ttl == -2 means key doesn't exist (race condition with natural expiry)
			# ttl > 0 means key is healthy with valid TTL

		if cursor == 0:
			break

	return checked, removed, orphaned_keys
