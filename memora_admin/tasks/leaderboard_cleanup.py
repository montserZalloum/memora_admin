"""
Leaderboard cleanup task — delete old daily/weekly/archive keys to bound memory.

Retention policy:
- Daily keys (memora:lb:daily:*): 30 days
- Weekly keys (memora:lb:weekly:*): 90 days
- Archive daily keys (memora:lb:archive:daily:*): 90 days
- Archive weekly keys (memora:lb:archive:weekly:*): 90 days

Key date formats (from leaderboard.py):
- Daily: memora:lb:daily:{YYYY-MM-DD}[:{suffix}]
- Weekly: memora:lb:weekly:{YYYY-MM-DD}[:{suffix}]  (date = Friday of Islamic week)

Scheduled via hooks.py: "0 3 * * *" (daily at 03:00)
"""

import re
from datetime import datetime, timedelta

import frappe

from fastapi_app.core.redis_keys import LB_PREFIX
from memora_admin.utils.redis_connection import get_memora_redis

# Retention thresholds
DAILY_RETENTION_DAYS = 30
WEEKLY_RETENTION_DAYS = 90
ARCHIVE_RETENTION_DAYS = 90

# Regex to extract YYYY-MM-DD date from key names
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _extract_date(key: str) -> datetime | None:
	"""Extract the first YYYY-MM-DD date from a Redis key name.

	Args:
		key: Redis key string (e.g. "memora:lb:daily:2026-01-15:subject:math")

	Returns:
		datetime object or None if no date found
	"""
	match = _DATE_RE.search(key)
	if not match:
		return None
	try:
		return datetime.strptime(match.group(1), "%Y-%m-%d")
	except ValueError:
		return None


def _scan_and_delete(r, pattern: str, cutoff: datetime) -> int:
	"""SCAN for keys matching pattern and delete those older than cutoff.

	Args:
		r: Redis client
		pattern: SCAN match pattern
		cutoff: Delete keys with dates before this datetime

	Returns:
		Number of keys deleted
	"""
	deleted = 0
	cursor = 0
	batch = []

	while True:
		cursor, keys = r.scan(cursor, match=pattern, count=500)
		for key in keys:
			key_date = _extract_date(key)
			if key_date and key_date < cutoff:
				batch.append(key)

			# Pipeline DEL in batches of 100
			if len(batch) >= 100:
				pipe = r.pipeline()
				for k in batch:
					pipe.delete(k)
				pipe.execute()
				deleted += len(batch)
				batch = []

		if cursor == 0:
			break

	# Flush remaining batch
	if batch:
		pipe = r.pipeline()
		for k in batch:
			pipe.delete(k)
		pipe.execute()
		deleted += len(batch)

	return deleted


def cleanup_old_leaderboards():
	"""Delete old leaderboard keys to bound memory growth.

	Scans for daily, weekly, and archive keys older than retention thresholds.

	Scheduled: daily at 03:00 via hooks.py
	"""
	r = get_memora_redis()
	now = datetime.now()

	daily_cutoff = now - timedelta(days=DAILY_RETENTION_DAYS)
	weekly_cutoff = now - timedelta(days=WEEKLY_RETENTION_DAYS)
	archive_cutoff = now - timedelta(days=ARCHIVE_RETENTION_DAYS)

	total_deleted = 0

	# Daily leaderboard keys (>30d)
	count = _scan_and_delete(r, f"{LB_PREFIX}:daily:*", daily_cutoff)
	if count:
		frappe.logger().info(f"leaderboard_cleanup: deleted {count} old daily keys")
	total_deleted += count

	# Weekly leaderboard keys (>90d)
	count = _scan_and_delete(r, f"{LB_PREFIX}:weekly:*", weekly_cutoff)
	if count:
		frappe.logger().info(f"leaderboard_cleanup: deleted {count} old weekly keys")
	total_deleted += count

	# Archive daily keys (>90d)
	count = _scan_and_delete(r, f"{LB_PREFIX}:archive:daily:*", archive_cutoff)
	if count:
		frappe.logger().info(f"leaderboard_cleanup: deleted {count} old archive daily keys")
	total_deleted += count

	# Archive weekly keys (>90d)
	count = _scan_and_delete(r, f"{LB_PREFIX}:archive:weekly:*", archive_cutoff)
	if count:
		frappe.logger().info(f"leaderboard_cleanup: deleted {count} old archive weekly keys")
	total_deleted += count

	if total_deleted:
		frappe.logger().info(f"leaderboard_cleanup: total deleted {total_deleted} keys")
	else:
		frappe.logger().debug("leaderboard_cleanup: no old keys to delete")
