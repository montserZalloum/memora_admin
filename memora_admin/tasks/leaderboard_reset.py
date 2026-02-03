"""
Leaderboard archival tasks for daily and weekly boards.

Per CONTEXT.md:
- Daily: Archives yesterday's leaderboard at midnight before natural key rotation
- Weekly: Archives last week's leaderboard Friday midnight after Islamic week ends (Thursday night)
- Islamic week: Friday is first day, week ends Thursday night
- Archives stored with 90-day TTL for historical reference

Scheduled via hooks.py:
- Daily: "10 0 * * *" (00:10)
- Weekly: "15 0 * * 5" (Friday 00:15)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import frappe
import redis

from memora_admin.tasks.task_utils import (
	AMMAN_TZ,
	TASK_DURATION,
	TASK_RUNS,
	get_amman_yesterday,
	has_run_today,
	log_task_run,
	notify_admins,
)

logger = logging.getLogger(__name__)

# Archive retention: 90 days
ARCHIVE_TTL_SECONDS = 90 * 24 * 3600

# Leaderboard key prefix (must match leaderboard.py)
LB_PREFIX = "memora:lb"


def get_redis():
	"""Get Redis connection using Frappe site config."""
	return redis.from_url(frappe.conf.redis_cache)


def archive_daily_leaderboard(triggered_by: str = "Scheduler"):
	"""Archive yesterday's daily leaderboard before it's lost.

	Daily keys rotate naturally based on date string in key name.
	This archives to a permanent key for historical reference.

	Args:
		triggered_by: Source of trigger - "Scheduler", "Manual", or "Catch-up"
	"""
	task_name = "leaderboard_daily"
	start_time = datetime.now()

	# Idempotency check
	if has_run_today(task_name):
		logger.info(f"{task_name} already completed for today")
		return

	try:
		archived_count = _do_daily_archive()

		status = "Success"
		log_task_run(
			task_name=task_name,
			status=status,
			processed=archived_count,
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=task_name, status="success").inc()
		logger.info(f"{task_name}: archived {archived_count} leaderboard(s)")

	except Exception as e:
		logger.critical(f"{task_name} failed: {e}")

		log_task_run(
			task_name=task_name,
			status="Failed",
			error_message=str(e),
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=task_name, status="failed").inc()
		notify_admins(task_name, str(e))
		raise

	finally:
		duration = (datetime.now() - start_time).total_seconds()
		TASK_DURATION.labels(task_name=task_name).observe(duration)


def _do_daily_archive() -> int:
	"""Archive daily leaderboards.

	Key pattern: memora:lb:daily:YYYY-MM-DD or memora:lb:daily:YYYY-MM-DD:subject:{subject_id}

	The SCAN pattern f'{LB_PREFIX}:daily:{yesterday}*' correctly matches BOTH:
	- Global daily: memora:lb:daily:2026-02-02
	- Subject-specific: memora:lb:daily:2026-02-02:subject:math-101

	Returns:
		Number of leaderboards archived
	"""
	r = get_redis()
	yesterday = get_amman_yesterday()
	archived = 0

	# Find all daily leaderboard keys for yesterday
	# Pattern matches both global and subject-specific leaderboards:
	# - memora:lb:daily:YYYY-MM-DD (global)
	# - memora:lb:daily:YYYY-MM-DD:subject:* (subject-specific)
	cursor = 0
	daily_keys = []

	while True:
		cursor, keys = r.scan(cursor, match=f"{LB_PREFIX}:daily:{yesterday}*", count=100)
		daily_keys.extend(keys)
		if cursor == 0:
			break

	for source_key in daily_keys:
		source_str = source_key.decode() if isinstance(source_key, bytes) else source_key

		# Create archive key by replacing "daily" with "archive:daily"
		archive_key = source_str.replace(":daily:", ":archive:daily:")

		# Only archive if source exists and archive doesn't
		if r.exists(source_key) and not r.exists(archive_key):
			# ZUNIONSTORE with single source effectively copies the ZSET
			r.zunionstore(archive_key, [source_key])

			# Set retention TTL
			r.expire(archive_key, ARCHIVE_TTL_SECONDS)

			archived += 1
			logger.debug(f"Archived daily leaderboard: {source_str}")

	return archived


def archive_weekly_leaderboard(triggered_by: str = "Scheduler"):
	"""Archive last week's weekly leaderboard.

	Per CONTEXT.md: Islamic week ends Thursday night / Friday midnight.
	This runs Friday morning to archive the week that just ended.

	Args:
		triggered_by: Source of trigger - "Scheduler", "Manual", or "Catch-up"
	"""
	task_name = "leaderboard_weekly"
	start_time = datetime.now()

	# Idempotency check
	if has_run_today(task_name):
		logger.info(f"{task_name} already completed for today")
		return

	try:
		archived_count = _do_weekly_archive()

		status = "Success"
		log_task_run(
			task_name=task_name,
			status=status,
			processed=archived_count,
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=task_name, status="success").inc()
		logger.info(f"{task_name}: archived {archived_count} leaderboard(s)")

	except Exception as e:
		logger.critical(f"{task_name} failed: {e}")

		log_task_run(
			task_name=task_name,
			status="Failed",
			error_message=str(e),
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=task_name, status="failed").inc()
		notify_admins(task_name, str(e))
		raise

	finally:
		duration = (datetime.now() - start_time).total_seconds()
		TASK_DURATION.labels(task_name=task_name).observe(duration)


def _do_weekly_archive() -> int:
	"""Archive weekly leaderboards.

	Key pattern: memora:lb:weekly:YYYY-Www or memora:lb:weekly:YYYY-Www:subject:{subject_id}

	Note: Weekly keys use ISO week format from leaderboard.py: YYYY-Www
	This archives the previous week's data.

	The SCAN pattern f'{LB_PREFIX}:weekly:{last_week}*' correctly matches BOTH:
	- Global weekly: memora:lb:weekly:2026-W05
	- Subject-specific: memora:lb:weekly:2026-W05:subject:math-101

	Returns:
		Number of leaderboards archived
	"""
	r = get_redis()
	archived = 0

	# Get last week's ISO week string
	# Yesterday would be Thursday (end of Islamic week)
	yesterday = datetime.now(AMMAN_TZ) - timedelta(days=1)
	last_week = yesterday.strftime("%G-W%V")

	# Find all weekly leaderboard keys for last week
	# Pattern matches both global and subject-specific leaderboards
	cursor = 0
	weekly_keys = []

	while True:
		cursor, keys = r.scan(cursor, match=f"{LB_PREFIX}:weekly:{last_week}*", count=100)
		weekly_keys.extend(keys)
		if cursor == 0:
			break

	for source_key in weekly_keys:
		source_str = source_key.decode() if isinstance(source_key, bytes) else source_key

		# Create archive key
		archive_key = source_str.replace(":weekly:", ":archive:weekly:")

		if r.exists(source_key) and not r.exists(archive_key):
			r.zunionstore(archive_key, [source_key])
			r.expire(archive_key, ARCHIVE_TTL_SECONDS)

			archived += 1
			logger.debug(f"Archived weekly leaderboard: {source_str}")

	return archived
