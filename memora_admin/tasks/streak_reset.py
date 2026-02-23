"""
Daily streak reset task for users who missed activity.

Per CONTEXT.md:
- Runs at midnight Asia/Amman (scheduled ~00:05 server time to allow buffer)
- Resets streak to 0 for users whose streak_date is not today or yesterday
- Also clears streak_date to match wallet.py patterns (clean state)
- Idempotent: checks has_run_today() before processing
- Partial failure handling: continues processing on individual errors
- Fail fast: logs CRITICAL + notifies admins on complete failure

Scheduled via hooks.py: "5 0 * * *"
"""

from __future__ import annotations

import logging


import frappe
import redis

from fastapi_app.core.redis_keys import WALLET_SCAN_PATTERN
from memora_admin.tasks.task_utils import (
	AMMAN_TZ,
	TASK_DURATION,
	TASK_RUNS,
	USERS_FAILED,
	USERS_PROCESSED,
	get_amman_today,
	get_amman_yesterday,
	has_run_today,
	log_task_run,
	notify_admins,
)

logger = logging.getLogger(__name__)

TASK_NAME = "streak_reset"


def get_redis():
	"""Get Redis connection using Frappe site config."""
	return redis.from_url(frappe.conf.redis_cache)


def reset_broken_streaks(triggered_by: str = "Scheduler"):
	"""Reset streaks for users who missed activity yesterday.

	Per CONTEXT.md:
	- Daily requirement: 1 lesson completion maintains streak
	- If streak_date is not today or yesterday, reset streak to 0
	- Also delete streak_date field per wallet.py patterns (clean reset state)
	- Continue processing all users even if some fail (partial failure)
	- Log individual failures for debugging

	Args:
		triggered_by: Source of trigger - "Scheduler", "Manual", or "Catch-up"
	"""
	start_time = frappe.utils.now_datetime()

	# Idempotency check: don't run twice on same day
	if has_run_today(TASK_NAME):
		logger.info(f"{TASK_NAME} already completed for {get_amman_today()}")
		return

	try:
		processed, failed, failed_details = _do_streak_reset()

		# Determine status
		if failed > 0 and processed == 0:
			status = "Failed"
		elif failed > 0:
			status = "Partial"
		else:
			status = "Success"

		# Log to DocType
		log_task_run(
			task_name=TASK_NAME,
			status=status,
			processed=processed,
			failed=failed,
			failed_details=failed_details if failed_details else None,
			triggered_by=triggered_by,
			started_at=start_time,
		)

		# Update Prometheus metrics
		TASK_RUNS.labels(task_name=TASK_NAME, status=status.lower()).inc()
		USERS_PROCESSED.labels(task_name=TASK_NAME).inc(processed)
		if failed > 0:
			USERS_FAILED.labels(task_name=TASK_NAME).inc(failed)

		logger.info(f"{TASK_NAME}: {processed} processed, {failed} failed")

	except Exception as e:
		# Complete task failure - fail fast per CONTEXT.md
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
		raise  # Re-raise to signal failure to scheduler

	finally:
		duration = (frappe.utils.now_datetime() - start_time).total_seconds()
		TASK_DURATION.labels(task_name=TASK_NAME).observe(duration)


def _do_streak_reset() -> tuple[int, int, list]:
	"""Execute streak reset logic.

	Returns:
		Tuple of (processed_count, failed_count, failed_details_list)
	"""
	r = get_redis()
	processed = 0
	failed = 0
	failed_details = []

	today = get_amman_today()
	yesterday = get_amman_yesterday()

	# Use SCAN to iterate wallet keys (not KEYS - per RESEARCH.md Pitfall 4)
	cursor = 0
	while True:
		cursor, keys = r.scan(cursor, match=WALLET_SCAN_PATTERN, count=1000)

		for key in keys:
			key_str = None
			try:
				# Decode key if bytes
				key_str = key.decode() if isinstance(key, bytes) else key

				# Extract player_id from key pattern memora:wallet:{player_id}
				player_id = key_str.split(":")[-1]

				# Get current streak_date
				streak_date = r.hget(key, "streak_date")
				if streak_date:
					streak_date = streak_date.decode() if isinstance(streak_date, bytes) else streak_date

					# Check if user was active today or yesterday
					if streak_date not in (today, yesterday):
						# User missed activity - reset streak to 0 AND clear streak_date
						# Per wallet.py: When user completes again, streak starts fresh at 1
						# with a new streak_date. Clearing streak_date ensures clean state.
						r.hset(key, "streak", 0)
						r.hdel(key, "streak_date")
						logger.debug(f"Reset streak for {player_id}, last active: {streak_date}")

				processed += 1

			except Exception as e:
				failed += 1
				# Try to extract player_id for error tracking
				try:
					pid = key_str.split(":")[-1] if key_str else str(key)
				except Exception:
					pid = str(key)

				failed_details.append({"player_id": pid, "error": str(e)})
				logger.error(f"Failed to process streak for {pid}: {e}")
				continue  # Continue to next user per CONTEXT.md

		if cursor == 0:
			break

	return processed, failed, failed_details
