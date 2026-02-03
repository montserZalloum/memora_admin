---
phase: 11
plan: 02
title: Core Scheduled Tasks
subsystem: scheduling
tags: [redis, streak, session, leaderboard, scheduled-tasks, frappe-hooks]

dependency_graph:
  requires:
    - phase: 11-01
      provides: [task-utils, prometheus-metrics, task-run-log]
  provides:
    - streak_reset task for daily streak maintenance
    - session_cleanup task for orphaned session removal
    - leaderboard archival tasks (daily and weekly)
  affects: [11-03-scheduler-hooks, 11-04-admin-dashboard]

tech_stack:
  added: []
  patterns: [redis-scan-iteration, idempotent-tasks, partial-failure-handling]

key_files:
  created:
    - memora_admin/memora_admin/tasks/streak_reset.py
    - memora_admin/memora_admin/tasks/session_cleanup.py
    - memora_admin/memora_admin/tasks/leaderboard_reset.py
  modified: []

decisions:
  - id: streak-date-cleared-on-reset
    choice: Delete streak_date along with resetting streak to 0
    rationale: Matches wallet.py pattern for clean reset state
  - id: session-cleanup-ttl-minus-one-only
    choice: Only remove keys with TTL -1 (safety net, not primary expiry)
    rationale: Redis TTL handles normal expiry; this catches orphaned keys
  - id: leaderboard-archive-scan-patterns
    choice: Use wildcard SCAN patterns to match global and subject-specific keys
    rationale: Single pattern handles both memora:lb:daily:YYYY-MM-DD and memora:lb:daily:YYYY-MM-DD:subject:*

patterns-established:
  - "Scheduled task structure: try/except with log_task_run, Prometheus metrics, notify_admins on failure"
  - "Redis key iteration: Always use SCAN (not KEYS) per RESEARCH.md Pitfall 4"
  - "Idempotency: has_run_today() check before processing daily tasks"
  - "Partial failure: Continue processing all items, log individual failures"

metrics:
  duration: ~2 min
  completed: 2026-02-03
---

# Phase 11 Plan 02: Core Scheduled Tasks Summary

**Daily streak reset, hourly session cleanup, and daily/weekly leaderboard archival tasks with idempotency, partial failure handling, and manual trigger support.**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-02-03T10:58:17Z
- **Completed:** 2026-02-03T11:00:28Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments

- streak_reset.py: Resets streak=0 AND deletes streak_date for inactive users
- session_cleanup.py: Safety net for orphaned session keys (TTL -1 only)
- leaderboard_reset.py: Archives daily and weekly leaderboards with 90-day retention
- All tasks accept triggered_by parameter for manual override via admin UI

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement streak_reset.py** - `6ab80d5` (feat)
2. **Task 2: Implement session_cleanup.py** - `da3330c` (feat)
3. **Task 3: Implement leaderboard_reset.py** - `d398174` (feat)

## Files Created

| File | Purpose |
|------|---------|
| `memora_admin/memora_admin/tasks/streak_reset.py` | Daily streak reset for inactive users |
| `memora_admin/memora_admin/tasks/session_cleanup.py` | Hourly orphaned session key cleanup |
| `memora_admin/memora_admin/tasks/leaderboard_reset.py` | Daily/weekly leaderboard archival |

## Key Implementation Details

### streak_reset.py

- **Function:** `reset_broken_streaks(triggered_by="Scheduler")`
- **Schedule:** Daily at 00:05 server time (midnight Asia/Amman buffer)
- **Logic:** If streak_date is not today or yesterday, reset streak=0 AND delete streak_date
- **Pattern:** Uses SCAN to iterate `memora:wallet:*` keys, partial failure handling

### session_cleanup.py

- **Function:** `cleanup_expired_sessions(triggered_by="Scheduler")`
- **Schedule:** Hourly at :15
- **Logic:** Only removes keys where TTL == -1 (orphaned keys without expiry)
- **Note:** This is a safety net - Redis TTL handles normal session expiry

### leaderboard_reset.py

- **Functions:**
  - `archive_daily_leaderboard(triggered_by="Scheduler")` - daily at 00:10
  - `archive_weekly_leaderboard(triggered_by="Scheduler")` - Friday at 00:15
- **Logic:** ZUNIONSTORE copies ZSET to archive key with 90-day TTL
- **Patterns:** SCAN matches both global (`memora:lb:daily:YYYY-MM-DD`) and subject-specific (`memora:lb:daily:YYYY-MM-DD:subject:*`)

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Streak date handling | Delete streak_date on reset | Matches wallet.py pattern for clean state |
| Session cleanup scope | TTL -1 only | Redis handles normal TTL expiry |
| Archive patterns | Wildcard SCAN | Single pattern handles global + subject |
| Weekly archive timing | Friday midnight | After Islamic week ends (Thursday night) |

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for:**
- 11-03: Register all tasks in hooks.py scheduler_events
- 11-04: Admin dashboard for task history and manual trigger

**Task exports verified:**
- `streak_reset.reset_broken_streaks(triggered_by)`
- `session_cleanup.cleanup_expired_sessions(triggered_by)`
- `leaderboard_reset.archive_daily_leaderboard(triggered_by)`
- `leaderboard_reset.archive_weekly_leaderboard(triggered_by)`

---
*Phase: 11-scheduled-tasks*
*Completed: 2026-02-03*
