# Phase 11: Scheduled Tasks - Research

**Researched:** 2026-02-03
**Domain:** Background scheduled jobs, timezone-aware cron scheduling, Redis maintenance, Prometheus metrics
**Confidence:** HIGH

## Summary

This phase implements automated maintenance tasks for the Memora platform: streak resets at midnight Asia/Amman, hourly session cleanup, and daily/weekly leaderboard archival. The implementation builds on the existing Frappe scheduler infrastructure already used in Phase 7 for sync tasks.

Key findings:
- Frappe's `scheduler_events` with cron expressions is the established pattern (already proven in hooks.py)
- Cron expressions run in server timezone but tasks should calculate Asia/Amman time internally for streak/leaderboard boundaries
- Redis SCAN with pattern matching is the safe approach for session cleanup (avoids blocking KEYS command)
- The `prometheus_client` library is the standard for Python metrics with direct support for Counter and Histogram types
- Idempotency achieved through inherently safe operations (date comparisons, key existence checks) plus last-run tracking for observability

**Primary recommendation:** Extend the existing `tasks/` module with new task files, register them in hooks.py using cron expressions, and implement a `Memora Task Run Log` DocType for failure tracking and dashboard display.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe scheduler | v15 | Cron-based task scheduling | Already in use; `scheduler_events` in hooks.py |
| redis-py | 5.0+ | Redis operations for streak/session/leaderboard | Already integrated; async client in FastAPI |
| croniter | 2.0+ | Cron expression parsing for catch-up logic | Used internally by Frappe scheduler |
| zoneinfo | stdlib | Asia/Amman timezone handling | Already used in wallet.py and leaderboard.py |
| prometheus_client | 0.20+ | Prometheus metrics export | De facto standard for Python metrics |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | 24.0+ | Structured task logging | Already used in FastAPI services |
| frappe.log_error | v15 | Error logging to Error Log DocType | On task failures for admin visibility |
| frappe.sendmail | v15 | Email notifications to admins | Critical failure alerts |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Frappe scheduler | APScheduler in FastAPI | Would require separate process management; Frappe scheduler already handles worker lifecycle |
| prometheus_client | statsd | Prometheus is more commonly used with Grafana; direct histogram support |
| SCAN for session cleanup | KEYS pattern | KEYS blocks Redis; SCAN is non-blocking and production-safe |

**Installation:**
```bash
pip install prometheus_client
```

(Other dependencies already installed via existing requirements.txt)

## Architecture Patterns

### Recommended Project Structure
```
memora_admin/memora_admin/
├── tasks/
│   ├── __init__.py
│   ├── sync.py              # Existing (Phase 7)
│   ├── build_worker.py      # Existing (Phase 6)
│   ├── streak_reset.py      # NEW: Daily streak maintenance
│   ├── session_cleanup.py   # NEW: Hourly session cleanup
│   └── leaderboard_reset.py # NEW: Daily/weekly leaderboard archival
├── doctype/
│   └── memora_task_run_log/ # NEW: Task execution history
└── page/
    └── task_dashboard/      # NEW: Admin dashboard for task history
```

### Pattern 1: Task Function Structure
**What:** Each scheduled task follows a consistent structure with logging, metrics, error handling, and idempotency checks.
**When to use:** All scheduled maintenance tasks
**Example:**
```python
# Source: Derived from existing sync.py pattern
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# Metrics
TASK_RUNS = Counter(
    "memora_task_runs_total",
    "Total task executions",
    ["task_name", "status"]
)
TASK_DURATION = Histogram(
    "memora_task_duration_seconds",
    "Task execution duration",
    ["task_name"],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0]
)

AMMAN_TZ = ZoneInfo("Asia/Amman")


def reset_broken_streaks():
    """Reset streaks for users who missed activity yesterday."""
    start_time = datetime.now()
    task_name = "streak_reset"

    try:
        # Idempotency: Check if already ran today
        today_amman = datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")
        last_run = _get_last_successful_run(task_name)

        if last_run and last_run >= today_amman:
            logger.info(f"{task_name} already completed for {today_amman}")
            return

        # Perform task logic
        processed, failed = _do_streak_reset()

        # Log success
        _log_task_run(task_name, "Success", processed, failed)
        TASK_RUNS.labels(task_name=task_name, status="success").inc()

    except Exception as e:
        logger.critical(f"{task_name} failed: {e}")
        _log_task_run(task_name, "Failed", 0, 0, str(e))
        TASK_RUNS.labels(task_name=task_name, status="failed").inc()
        _notify_admins(task_name, str(e))
        raise  # Fail fast per CONTEXT.md

    finally:
        duration = (datetime.now() - start_time).total_seconds()
        TASK_DURATION.labels(task_name=task_name).observe(duration)
```

### Pattern 2: Catch-up on Missed Runs
**What:** If server was down at scheduled time, detect and run immediately on recovery.
**When to use:** For streak reset and leaderboard reset (critical for user fairness)
**Example:**
```python
# Source: Derived from CONTEXT.md catch-up requirement
def _should_run_catchup(task_name: str, expected_date: str) -> bool:
    """Check if task needs catch-up execution.

    Args:
        task_name: Task identifier
        expected_date: Date when task should have run (YYYY-MM-DD)

    Returns:
        True if task didn't run on expected date and should catch up
    """
    last_run = frappe.db.get_value(
        "Memora Task Run Log",
        {"task_name": task_name, "status": "Success"},
        "run_date",
        order_by="run_date desc"
    )

    if not last_run:
        return True  # Never ran, definitely catch up

    # If last successful run is before expected date, catch up needed
    return str(last_run) < expected_date
```

### Pattern 3: Partial Failure Handling
**What:** Continue processing all users even if some fail; log individual failures.
**When to use:** Streak reset (must attempt all users)
**Example:**
```python
# Source: CONTEXT.md partial failure requirement
def _do_streak_reset() -> tuple[int, int]:
    """Reset streaks for inactive users.

    Returns:
        Tuple of (processed_count, failed_count)
    """
    r = get_redis()
    processed = 0
    failed = 0
    failed_users = []

    # Get all users with wallet data
    wallet_keys = []
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match="memora:wallet:*", count=1000)
        wallet_keys.extend(keys)
        if cursor == 0:
            break

    today = get_amman_today()
    yesterday = get_amman_yesterday()

    for key in wallet_keys:
        try:
            player_id = key.decode().split(":")[-1] if isinstance(key, bytes) else key.split(":")[-1]

            # Check streak_date - if not yesterday or today, reset
            streak_date = r.hget(key, "streak_date")
            if streak_date:
                streak_date = streak_date.decode() if isinstance(streak_date, bytes) else streak_date

                if streak_date not in (today, yesterday):
                    # User missed activity - reset streak
                    r.hset(key, "streak", 0)
                    logger.debug(f"Reset streak for {player_id}, last active: {streak_date}")

            processed += 1

        except Exception as e:
            failed += 1
            failed_users.append({"player_id": player_id, "error": str(e)})
            logger.error(f"Failed to process streak for {player_id}: {e}")
            continue  # Continue to next user

    # Store failed user details for potential re-run
    if failed_users:
        _store_failed_details("streak_reset", failed_users)

    return processed, failed
```

### Pattern 4: Redis SCAN for Session Cleanup
**What:** Use SCAN to iterate session keys non-blockingly; Redis TTL handles actual expiration.
**When to use:** Hourly session cleanup
**Example:**
```python
# Source: Redis SCAN documentation best practices
def cleanup_expired_sessions():
    """Remove orphaned session keys.

    Note: Redis TTL handles normal expiration. This cleanup catches:
    - Keys with corrupted TTL
    - Keys that survived unexpected process termination
    """
    r = get_redis()
    cursor = 0
    checked = 0
    removed = 0

    while True:
        # SCAN with pattern match for session keys
        cursor, keys = r.scan(
            cursor,
            match="memora:gamesession:*",
            count=100  # Batch size hint
        )

        for key in keys:
            checked += 1
            # Check if TTL is -1 (no expiry) or -2 (key doesn't exist)
            ttl = r.ttl(key)
            if ttl == -1:  # Key exists but has no TTL (should never happen)
                r.delete(key)
                removed += 1
                logger.warning(f"Removed session key without TTL: {key}")

        if cursor == 0:
            break

    logger.info(f"Session cleanup: checked {checked}, removed {removed}")
    return checked, removed
```

### Pattern 5: Leaderboard Archival with ZUNIONSTORE
**What:** Archive yesterday's leaderboard to a dated key before the natural key rotation.
**When to use:** Daily and weekly leaderboard archival
**Example:**
```python
# Source: Redis sorted set documentation
def archive_daily_leaderboard():
    """Archive yesterday's daily leaderboard before it's lost.

    Daily keys rotate naturally based on date string in key name.
    This archives to a permanent key for historical reference.
    """
    r = get_redis()

    yesterday = get_amman_yesterday()
    source_key = f"memora:lb:daily:{yesterday}"
    archive_key = f"memora:lb:archive:daily:{yesterday}"

    # Check if source exists and archive doesn't
    if r.exists(source_key) and not r.exists(archive_key):
        # ZUNIONSTORE with single source effectively copies the ZSET
        r.zunionstore(archive_key, [source_key])

        # Set retention TTL (e.g., 90 days)
        r.expire(archive_key, 90 * 24 * 3600)

        logger.info(f"Archived daily leaderboard: {yesterday}")
        return True

    return False
```

### Anti-Patterns to Avoid
- **KEYS command in production:** Use SCAN instead to avoid blocking Redis.
- **Checking TTL for every key on every run:** Let Redis handle TTL naturally; only scan for orphaned keys.
- **Storing task state in Redis only:** Use a Frappe DocType for persistence and admin visibility.
- **Running tasks in FastAPI process:** Use Frappe scheduler for lifecycle management and error recovery.
- **Retrying automatically on failure:** Per CONTEXT.md, fail fast and notify admins instead.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cron scheduling | Custom timer loop | Frappe scheduler_events | Already handles worker lifecycle, retries, site context |
| Timezone conversion | Manual UTC offset math | zoneinfo.ZoneInfo | Handles DST transitions correctly |
| Key iteration | KEYS pattern | redis SCAN | Non-blocking; safe for production |
| Metrics export | Custom /metrics endpoint | prometheus_client | Handles multiprocess, proper formats, exposition |
| Admin notifications | Custom webhook | frappe.sendmail + role filter | Already integrated with user system |

**Key insight:** The Frappe framework already provides task scheduling, error logging, and notification infrastructure. Phase 11 should extend these patterns rather than building parallel systems.

## Common Pitfalls

### Pitfall 1: Cron Expression Timezone Confusion
**What goes wrong:** Assuming cron expressions run in local timezone when Frappe scheduler uses server timezone.
**Why it happens:** Frappe scheduler operates in UTC/server timezone, but business logic requires Asia/Amman.
**How to avoid:** Calculate Asia/Amman date/time within the task function, not via cron expression timing.
**Warning signs:** Tasks running at wrong times; streaks resetting at 3am instead of midnight.

### Pitfall 2: Random Scheduler Delay
**What goes wrong:** Expecting exact midnight execution when Frappe adds up to 10 minutes random delay.
**Why it happens:** Frappe scheduler adds `randint(1, 600)` seconds delay to prevent thundering herd.
**How to avoid:** Design for "within 15 minutes of target time" not "exactly at midnight."
**Warning signs:** Tasks logging start times 5-10 minutes after expected.

### Pitfall 3: Non-Idempotent Streak Reset
**What goes wrong:** Running twice on same day resets already-reset streaks.
**Why it happens:** No check for previous execution; reset logic not date-aware.
**How to avoid:** Track last successful run timestamp; compare dates before resetting.
**Warning signs:** Users complaining about "double resets" or unexpectedly lost streaks.

### Pitfall 4: Blocking Redis with KEYS
**What goes wrong:** Session cleanup blocks Redis for seconds on large keyspace.
**Why it happens:** Using `KEYS memora:gamesession:*` instead of SCAN.
**How to avoid:** Always use SCAN with cursor iteration for pattern matching.
**Warning signs:** Redis latency spikes during hourly cleanup; game API timeouts.

### Pitfall 5: Silent Task Failures
**What goes wrong:** Tasks fail but no one notices until users complain.
**Why it happens:** Only logging errors without notifications; no dashboard.
**How to avoid:** Per CONTEXT.md: CRITICAL log + Frappe notification + DocType record.
**Warning signs:** Error logs with no corresponding admin alerts.

### Pitfall 6: ISO Week vs Islamic Week
**What goes wrong:** Weekly leaderboard resets on Monday instead of Friday.
**Why it happens:** Python's `isocalendar()` uses ISO week (Monday start).
**How to avoid:** Custom week calculation: Saturday=1 through Friday=7, week ends Thursday night.
**Warning signs:** Users expecting fresh weekly leaderboard on Friday see old data.

## Code Examples

Verified patterns from official sources and existing codebase:

### Frappe Scheduler Registration (hooks.py)
```python
# Source: Existing hooks.py pattern + Frappe docs
scheduler_events = {
    "cron": {
        # Every 1 minute: Sync dirty data
        "* * * * *": [
            "memora_admin.memora_admin.tasks.sync.sync_dirty_progress",
            "memora_admin.memora_admin.tasks.sync.sync_dirty_wallets",
            "memora_admin.memora_admin.tasks.sync.flush_interaction_buffer",
        ],
        # Every 2 minutes: Process builds
        "*/2 * * * *": [
            "memora_admin.memora_admin.tasks.build_worker.process_pending_builds"
        ],
        # Daily at 00:05 server time: Streak reset (runs ~midnight + buffer)
        "5 0 * * *": [
            "memora_admin.memora_admin.tasks.streak_reset.reset_broken_streaks"
        ],
        # Hourly at :15: Session cleanup
        "15 * * * *": [
            "memora_admin.memora_admin.tasks.session_cleanup.cleanup_expired_sessions"
        ],
        # Daily at 00:10: Daily leaderboard archive
        "10 0 * * *": [
            "memora_admin.memora_admin.tasks.leaderboard_reset.archive_daily_leaderboard"
        ],
        # Friday at 00:15 (Asia/Amman midnight): Weekly leaderboard archive
        "15 0 * * 5": [
            "memora_admin.memora_admin.tasks.leaderboard_reset.archive_weekly_leaderboard"
        ],
    }
}
```

### Prometheus Metrics Setup
```python
# Source: prometheus_client PyPI documentation
from prometheus_client import Counter, Histogram, CollectorRegistry, multiprocess, generate_latest

# Create metrics registry for multiprocess mode (Gunicorn workers)
REGISTRY = CollectorRegistry()

# Task execution counters
TASK_RUNS = Counter(
    "memora_task_runs_total",
    "Total task executions",
    ["task_name", "status"],
    registry=REGISTRY
)

# Task duration histogram
TASK_DURATION = Histogram(
    "memora_task_duration_seconds",
    "Task execution duration in seconds",
    ["task_name"],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
    registry=REGISTRY
)

# Users processed counter
USERS_PROCESSED = Counter(
    "memora_task_users_processed_total",
    "Total users processed by tasks",
    ["task_name"],
    registry=REGISTRY
)

# Users failed counter
USERS_FAILED = Counter(
    "memora_task_users_failed_total",
    "Total users that failed processing",
    ["task_name"],
    registry=REGISTRY
)
```

### Admin Notification on Failure
```python
# Source: Frappe forum + existing notification patterns
def _notify_admins(task_name: str, error_message: str):
    """Send notification to Task Admin role users on critical failure."""
    # Get users with Task Admin role
    admin_users = frappe.get_all(
        "Has Role",
        filters={"role": "Task Admin", "parenttype": "User"},
        fields=["parent"]
    )

    recipients = []
    for u in admin_users:
        email = frappe.db.get_value("User", u.parent, "email")
        if email:
            recipients.append(email)

    if not recipients:
        # Fallback to System Manager if no Task Admin
        admin_users = frappe.get_all(
            "Has Role",
            filters={"role": "System Manager", "parenttype": "User"},
            fields=["parent"]
        )
        recipients = [frappe.db.get_value("User", u.parent, "email") for u in admin_users]

    if recipients:
        frappe.sendmail(
            recipients=recipients,
            subject=f"[CRITICAL] Scheduled Task Failed: {task_name}",
            message=f"""
            <h3>Scheduled Task Failure Alert</h3>
            <p><strong>Task:</strong> {task_name}</p>
            <p><strong>Time:</strong> {datetime.now()}</p>
            <p><strong>Error:</strong></p>
            <pre>{error_message}</pre>
            <p>Please check the Memora Task Run Log for details.</p>
            """,
            now=True
        )

    # Also log to Error Log for Frappe Desk visibility
    frappe.log_error(
        title=f"Task Failed: {task_name}",
        message=error_message
    )
```

### Task Run Log DocType Schema
```python
# Source: Derived from existing Memora Sync Log pattern
# memora_task_run_log.json fields:
{
    "fields": [
        {"fieldname": "task_name", "fieldtype": "Data", "reqd": 1, "in_list_view": 1},
        {"fieldname": "run_date", "fieldtype": "Date", "reqd": 1, "in_list_view": 1},
        {"fieldname": "started_at", "fieldtype": "Datetime", "reqd": 1},
        {"fieldname": "completed_at", "fieldtype": "Datetime"},
        {"fieldname": "duration_sec", "fieldtype": "Float"},
        {"fieldname": "status", "fieldtype": "Select", "options": "Success\nFailed\nPartial", "reqd": 1, "in_list_view": 1},
        {"fieldname": "processed_count", "fieldtype": "Int", "default": 0},
        {"fieldname": "failed_count", "fieldtype": "Int", "default": 0},
        {"fieldname": "error_message", "fieldtype": "Text"},
        {"fieldname": "failed_details", "fieldtype": "Code", "options": "JSON"},
        {"fieldname": "triggered_by", "fieldtype": "Select", "options": "Scheduler\nManual\nCatch-up"}
    ]
}
```

### Islamic Week Calculation
```python
# Source: Custom implementation per CONTEXT.md Islamic week requirement
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

AMMAN_TZ = ZoneInfo("Asia/Amman")


def get_islamic_week_key() -> str:
    """Get week key following Islamic calendar (Friday start).

    Islamic week: Friday is first day, week ends Thursday night.
    Returns format: YYYY-Www where week number is Islamic-based.
    """
    now = datetime.now(AMMAN_TZ)

    # Python weekday: Monday=0, Sunday=6
    # Islamic week: Saturday=0, Friday=6 (week ends Thursday night)
    # Adjust: Friday(4)->0, Saturday(5)->1, ..., Thursday(3)->6
    weekday = now.weekday()

    # Calculate days since last Friday (Friday = day 0 of Islamic week)
    if weekday >= 4:  # Friday, Saturday, Sunday
        days_since_friday = weekday - 4
    else:  # Monday through Thursday
        days_since_friday = weekday + 3

    # Get the Friday that started this week
    week_start = now - timedelta(days=days_since_friday)

    # Use that Friday's ISO week for consistency
    year, week, _ = week_start.isocalendar()

    return f"{year}-Wf{week:02d}"  # Wf = Week (Friday-based)


def get_yesterday_islamic_week_key() -> str:
    """Get yesterday's Islamic week key for archival."""
    yesterday = datetime.now(AMMAN_TZ) - timedelta(days=1)

    weekday = yesterday.weekday()
    if weekday >= 4:
        days_since_friday = weekday - 4
    else:
        days_since_friday = weekday + 3

    week_start = yesterday - timedelta(days=days_since_friday)
    year, week, _ = week_start.isocalendar()

    return f"{year}-Wf{week:02d}"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Frappe `daily`/`hourly` events | Cron expressions in scheduler_events | Frappe 13+ | Precise scheduling control |
| KEYS pattern for Redis | SCAN cursor iteration | Redis 2.8+ | Non-blocking key discovery |
| pytz for timezone | zoneinfo (stdlib) | Python 3.9+ | No external dependency |
| statsd metrics | prometheus_client | Industry shift ~2020 | Better Grafana integration |
| Manual retry loops | Fail-fast with notifications | Per CONTEXT.md | Simpler error handling |

**Deprecated/outdated:**
- `python-jose` for JWT: Use `PyJWT` instead (already used in project)
- `pytz` for timezone: Use `zoneinfo` stdlib (Python 3.9+)
- Frappe `scheduler_events["daily"]`: Use cron for precise timing

## Open Questions

Things that couldn't be fully resolved:

1. **Frappe Scheduler Timezone Behavior**
   - What we know: Scheduler operates in UTC/server timezone internally
   - What's unclear: Exact timezone configuration per Frappe v15
   - Recommendation: Calculate Asia/Amman time within task function, don't rely on cron expression timing

2. **Prometheus Multiprocess Mode**
   - What we know: prometheus_client supports multiprocess via `PROMETHEUS_MULTIPROC_DIR`
   - What's unclear: Whether Frappe scheduler workers need this configuration
   - Recommendation: Start with single-process mode, add multiprocess if metrics are missing

3. **Task Dashboard Page Type**
   - What we know: Frappe supports custom Desk pages and DocType list views
   - What's unclear: Best UX for task history dashboard (custom page vs. enhanced list view)
   - Recommendation: Start with DocType list view with filters; custom page if needed later

## Sources

### Primary (HIGH confidence)
- Existing codebase: `hooks.py`, `tasks/sync.py`, `tasks/build_worker.py`, `services/wallet.py`, `services/leaderboard.py`
- [Redis SCAN documentation](https://redis.io/docs/latest/commands/scan/) - Key iteration patterns
- [prometheus_client PyPI](https://pypi.org/project/prometheus-client/) - Python metrics library

### Secondary (MEDIUM confidence)
- [Frappe Scheduler blog](https://frappe.io/blog/engineering/if-you-wish-to-truly-understand-frappes-scheduler-you-must-first-invent-the-universe) - Scheduler architecture
- [Frappe scheduler.py source](https://github.com/frappe/frappe/blob/develop/frappe/utils/scheduler.py) - UTC usage, random delay
- [croniter PyPI](https://pypi.org/project/croniter/) - Timezone-aware cron parsing
- [Frappe Forum - frappe.sendmail](https://discuss.frappe.io/t/send-email-programmatically/41480) - Email notification API

### Tertiary (LOW confidence)
- WebSearch results for idempotency patterns - General best practices, verify with implementation testing

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Based on existing codebase patterns and official docs
- Architecture: HIGH - Extends proven Phase 7 task infrastructure
- Pitfalls: MEDIUM - Some based on Frappe forum reports, needs validation
- Islamic week calculation: MEDIUM - Custom implementation, needs testing

**Research date:** 2026-02-03
**Valid until:** 2026-03-03 (30 days - stable domain, existing infrastructure)
