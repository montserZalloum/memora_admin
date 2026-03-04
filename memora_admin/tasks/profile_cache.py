"""
Profile cache pre-warming task for active leaderboard players.

# Player identity is PLAYER-##### docname (not email). See Phase 32.

Per CONTEXT.md:
- Hourly pre-warm cache for active leaderboard players
- 1-hour TTL on cached profiles
- Only warm profiles of players in top 100 of each leaderboard

Scheduled via hooks.py:
- Hourly at :30: "30 * * * *"
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import frappe
import redis

from fastapi_app.core.redis_keys import LB_PREFIX, profile_key, task_ran_key
from memora_admin.tasks.task_utils import (
	AMMAN_TZ,
	TASK_DURATION,
	TASK_RUNS,
	has_run_today,
	log_task_run,
	notify_admins,
)
from memora_admin.utils.redis_connection import get_memora_redis

logger = logging.getLogger(__name__)

# Cache TTL: 1 hour per CONTEXT.md
CACHE_TTL = 3600

# LB_PREFIX imported from fastapi_app.core.redis_keys


def warm_profile_cache(triggered_by: str = "Scheduler"):
	"""Pre-warm profile cache for active leaderboard players.

	Fetches top 100 players from each active leaderboard (daily, weekly),
	deduplicates, and caches their profiles with 1-hour TTL.

	Per RESEARCH.md:
	- Only pre-warm profiles of players in top 100 leaderboards
	- Use pipeline for efficient bulk SET operations
	- Prevents Frappe batch API timeout by limiting scope

	Args:
		triggered_by: Source of trigger - "Scheduler", "Manual", or "Catch-up"
	"""
	task_name = "profile_cache_warm"
	start_time = frappe.utils.now_datetime()

	# Idempotency check - only run once per hour window
	# For hourly tasks, we check against hour rather than day
	hour_key = f"{task_name}:{datetime.now(AMMAN_TZ).strftime('%Y-%m-%d-%H')}"
	r = get_memora_redis()
	if r.get(task_ran_key(hour_key)):
		logger.info(f"{task_name} already completed for this hour")
		return

	try:
		cached_count = _do_warm_cache(r)

		# Mark this hour as completed
		r.set(task_ran_key(hour_key), "1", ex=3600)

		status = "Success"
		log_task_run(
			task_name=task_name,
			status=status,
			processed=cached_count,
			triggered_by=triggered_by,
			started_at=start_time,
		)

		TASK_RUNS.labels(task_name=task_name, status="success").inc()
		logger.info(f"{task_name}: pre-warmed {cached_count} profile(s)")

	except Exception as e:
		logger.error(f"{task_name} failed: {e}")

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
		duration = (frappe.utils.now_datetime() - start_time).total_seconds()
		TASK_DURATION.labels(task_name=task_name).observe(duration)


def _do_warm_cache(r: redis.Redis) -> int:
	"""Collect unique player_ids from leaderboards and cache their profiles.

	Strategy:
	1. Get top 100 from today's daily leaderboard
	2. Get top 100 from current weekly leaderboard
	3. Deduplicate into a single set
	4. Batch fetch profiles from Frappe
	5. Pipeline SET to Redis with TTL

	Returns:
		Number of profiles cached
	"""
	now = datetime.now(AMMAN_TZ)
	today_str = now.strftime("%Y-%m-%d")
	# Islamic week: Friday–Thursday (must match leaderboard.py key format)
	weekday = now.isoweekday()  # 1=Mon … 5=Fri … 7=Sun
	days_since_friday = (weekday - 5) % 7
	week_str = (now - timedelta(days=days_since_friday)).strftime("%Y-%m-%d")

	# Collect unique player_ids from active leaderboards
	player_ids: set[str] = set()

	# Today's daily leaderboard
	daily_players = r.zrange(f"{LB_PREFIX}:daily:{today_str}", 0, 99, desc=True)
	for p in daily_players:
		player_ids.add(p.decode() if isinstance(p, bytes) else p)

	# Current weekly leaderboard
	weekly_players = r.zrange(f"{LB_PREFIX}:weekly:{week_str}", 0, 99, desc=True)
	for p in weekly_players:
		player_ids.add(p.decode() if isinstance(p, bytes) else p)

	if not player_ids:
		logger.info("No players found in leaderboards to warm cache")
		return 0

	logger.debug(f"Found {len(player_ids)} unique players across leaderboards")

	# Batch fetch profiles from Frappe
	profiles = frappe.get_all(
		"Memora Player Profile",
		filters={"name": ["in", list(player_ids)]},
		fields=["name", "display_name", "avatar"],
	)

	if not profiles:
		logger.info("No profiles found for leaderboard players")
		return 0

	# Pipeline SET to cache each profile
	pipe = r.pipeline()
	for p in profiles:
		key = profile_key(p.name)
		data = json.dumps(
			{
				"player_id": p.name,
				"display_name": p.display_name or "",
				"avatar": p.avatar or "default_avatar",
			}
		)
		pipe.set(key, data, ex=CACHE_TTL)
	pipe.execute()

	logger.info(f"Pre-warmed {len(profiles)} profiles from {len(player_ids)} unique leaderboard players")

	return len(profiles)
