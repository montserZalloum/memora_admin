"""Practice Arena — Frappe scheduler tasks for background write worker
and session cleanup.

Scheduled via hooks.py cron:
- ``process_write_queue``: every minute (``* * * * *``)
- ``cleanup_orphaned_sessions``: hourly (``0 * * * *``)

Creates Redis connection, delegates to ``fastapi_app.services.practice_writer``
for core processing, and handles logging + cleanup.
"""

from __future__ import annotations

import logging

import frappe
from frappe.utils import now_datetime

from memora_admin.tasks.task_utils import (
    TASK_DURATION,
    TASK_RUNS,
    log_task_run,
    notify_admins,
)
from memora_admin.utils.redis_connection import get_memora_redis

logger = logging.getLogger(__name__)

TASK_NAME = "practice_write_queue"
SESSION_CLEANUP_TASK_NAME = "practice_session_cleanup"


def process_write_queue(triggered_by: str = "Scheduler", **kwargs) -> None:
    """Process the practice result write queue.

    Called every minute by the Frappe scheduler.  Reads messages from the
    Redis Stream, persists Practice Log + Player Summary rows, and reclaims
    stale messages from crashed consumers.
    """
    from fastapi_app.services.practice_writer import (
        ensure_consumer_group_sync,
        log_queue_health,
        process_write_queue as _process,
        reclaim_stale_messages as _reclaim,
    )

    start_time = now_datetime()

    try:
        r = get_memora_redis()

        # Ensure stream + consumer group exist (idempotent) — covers the case
        # where FastAPI hasn't started yet or Redis was flushed.
        ensure_consumer_group_sync(r)

        # T028: Log queue health metrics each cycle
        log_queue_health(r)

        processed = _process(r, frappe.db)
        reclaimed, dead_lettered = _reclaim(r, frappe.db)

        total = processed + reclaimed

        if total > 0 or dead_lettered > 0:
            logger.info(
                "practice_write_queue: processed=%d reclaimed=%d dead_lettered=%d",
                processed,
                reclaimed,
                dead_lettered,
            )

        log_task_run(
            task_name=TASK_NAME,
            status="Success",
            processed=total,
            triggered_by=triggered_by,
            started_at=start_time,
        )

    except Exception as e:
        try:
            frappe.db.rollback()
        except Exception:
            pass

        logger.critical("practice_write_queue failed: %s", e)
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
        duration = (now_datetime() - start_time).total_seconds()
        TASK_DURATION.labels(task_name=TASK_NAME).observe(duration)


# ---------------------------------------------------------------------------
# T030 — Scheduled practice session cleanup (safety net)
# ---------------------------------------------------------------------------


def cleanup_orphaned_sessions(triggered_by: str = "Scheduler", **kwargs) -> None:
    """Remove practice session keys that lost their TTL.

    Runs hourly as a safety net.  Normal session expiry is handled by Redis
    TTL (3600s, refreshed on submit/continue).  This task only catches keys
    with TTL <= 0 or TTL == -1 (no expiry set), indicating an orphaned key.
    """
    from fastapi_app.core.redis_keys import PRACTICE_SESSION_SCAN_PATTERN

    start_time = now_datetime()

    try:
        r = get_memora_redis()
        checked = 0
        removed = 0

        cursor = 0
        while True:
            cursor, keys = r.scan(
                cursor,
                match=PRACTICE_SESSION_SCAN_PATTERN,
                count=100,
            )

            for key in keys:
                checked += 1
                ttl = r.ttl(key)

                if ttl == -1:
                    # Key exists but has no TTL — orphaned, delete it
                    r.delete(key)
                    removed += 1
                    key_str = key.decode() if isinstance(key, bytes) else key
                    logger.warning(
                        "practice_session_cleanup: removed orphaned key %s (no TTL)",
                        key_str,
                    )

                # ttl == -2: key already expired/gone (race condition)
                # ttl > 0: healthy key with valid TTL

            if cursor == 0:
                break

        logger.info(
            "practice_session_cleanup: checked=%d removed=%d",
            checked,
            removed,
        )

        log_task_run(
            task_name=SESSION_CLEANUP_TASK_NAME,
            status="Success",
            processed=checked,
            failed=removed,
            triggered_by=triggered_by,
            started_at=start_time,
        )

    except Exception as e:
        logger.critical("practice_session_cleanup failed: %s", e)
        log_task_run(
            task_name=SESSION_CLEANUP_TASK_NAME,
            status="Failed",
            error_message=str(e),
            triggered_by=triggered_by,
            started_at=start_time,
        )
        TASK_RUNS.labels(task_name=SESSION_CLEANUP_TASK_NAME, status="failed").inc()
        notify_admins(SESSION_CLEANUP_TASK_NAME, str(e))
        raise

    finally:
        duration = (now_datetime() - start_time).total_seconds()
        TASK_DURATION.labels(task_name=SESSION_CLEANUP_TASK_NAME).observe(duration)
