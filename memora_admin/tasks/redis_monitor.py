"""
Periodic Redis health monitoring with threshold-based alerting.

Collects memory usage, buffer length, dirty set sizes, and key count.
Logs INFO on every run, WARNING/CRITICAL when thresholds are exceeded.

Scheduled: every 5 minutes via hooks.py (cron: */5 * * * *)
"""

import logging

from fastapi_app.core.redis_keys import (
	dirty_progress_key,
	dirty_wallets_key,
	interaction_buffer_key,
)
from memora_admin.utils.redis_connection import get_memora_redis

logger = logging.getLogger(__name__)


def monitor_redis_health():
	"""Collect Redis metrics and log with threshold-based alerting.

	Metrics collected:
	- Memory: used_memory_mb, max_memory_mb, memory_pct
	- Buffer: interaction buffer length (LLEN)
	- Dirty sets: wallet and progress dirty set sizes (SCARD)
	- Keys: total key count (DBSIZE)

	Thresholds:
	- WARNING: memory_pct > 80%, dirty sets > 1000
	- CRITICAL: buffer_len > 10000
	"""
	try:
		r = get_memora_redis()

		# Collect metrics
		mem_info = r.info("memory")
		used_memory = mem_info.get("used_memory", 0)
		maxmemory = mem_info.get("maxmemory", 0)

		used_mb = round(used_memory / (1024 * 1024), 2)
		max_mb = round(maxmemory / (1024 * 1024), 2) if maxmemory else 0
		memory_pct = round((used_memory / maxmemory) * 100, 1) if maxmemory else 0

		buffer_len = r.llen(interaction_buffer_key())
		dirty_wallets = r.scard(dirty_wallets_key())
		dirty_progress = r.scard(dirty_progress_key())
		total_keys = r.dbsize()

		# Always log INFO with all metrics (inline in message for Frappe log visibility)
		logger.info(
			"redis_monitor used_mb=%.2f max_mb=%.2f memory_pct=%.1f buffer=%d dirty_wallets=%d dirty_progress=%d keys=%d",
			used_mb,
			max_mb,
			memory_pct,
			buffer_len,
			dirty_wallets,
			dirty_progress,
			total_keys,
		)

		# Threshold-based alerting
		if memory_pct > 80:
			logger.warning(
				"redis_memory_high used_mb=%.2f max_mb=%.2f memory_pct=%.1f",
				used_mb,
				max_mb,
				memory_pct,
			)

		if dirty_wallets > 1000 or dirty_progress > 1000:
			logger.warning(
				"redis_sync_falling_behind dirty_wallets=%d dirty_progress=%d",
				dirty_wallets,
				dirty_progress,
			)

		if buffer_len > 10000:
			logger.critical(
				"redis_buffer_backlog buffer_len=%d",
				buffer_len,
			)

	except Exception:
		logger.exception("redis_monitor_failed")
