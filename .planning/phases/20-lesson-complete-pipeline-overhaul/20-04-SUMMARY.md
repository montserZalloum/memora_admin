---
phase: 20
plan: 04
subsystem: frappe-tasks
tags: [fsrs, spaced-repetition, background-task, scheduler, memory-state, redis-cache]
requires:
  - "20-01 (fsrs>=6.0.0 in requirements.txt, Memora Settings fsrs_weights field)"
provides:
  - "process_fsrs_reviews() background task for FSRS spaced repetition"
  - "FSRS state persistence to Memora Memory State DocType + Redis cache"
  - "Skippable stage filtering via Memora Lesson Stage Settings"
  - "Idempotent processing with Redis-based dedup keys"
affects:
  - "Future review scheduling features (FSRS state is now available)"
  - "Memora Memory State DocType receives production data"
tech-stack:
  added: []
  patterns:
    - "FSRS Scheduler(parameters=weights) for configurable spaced repetition"
    - "Idempotency via Redis key with TTL (prevents duplicate processing)"
    - "Background processing off hot path (1-minute cron cycle)"
key-files:
  created:
    - memora_admin/tasks/fsrs_processor.py
  modified:
    - memora_admin/hooks.py
key-decisions:
  - id: fsrs-off-hot-path
    decision: "FSRS processing in background task, not hot path"
    reason: "Keeps lesson completion under 10ms; FSRS computation is non-latency-critical"
  - id: interaction-log-source
    decision: "Read from Memora Interaction Log (already synced by flush_interaction_buffer), not Redis buffer"
    reason: "Data already persisted and structured; avoids coupling to buffer format"
  - id: idempotency-redis-key
    decision: "5-minute TTL Redis key per player:stage:creation for dedup"
    reason: "2-minute overlap window means same interaction could appear in consecutive runs"
  - id: subject-direct-field
    decision: "Resolve subject from Memora Lesson.subject (direct field) with hierarchy chain fallback"
    reason: "Lesson DocType has denormalized subject link field; chain traversal only as safety net"
  - id: fsrs-rating-mapping
    decision: "0 fails=Good, 1 fail=Hard, 2+ fails=Again"
    reason: "Maps student performance to FSRS difficulty signal per research spec"
duration: 3m
completed: 2026-02-07
---

# Phase 20 Plan 04: FSRS Background Processor Summary

**FSRS background task processes non-skippable stage interactions every minute, computing spaced repetition state with configurable weights and persisting to Memora Memory State + Redis cache**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~3 minutes |
| Started | 2026-02-07T09:55:18Z |
| Completed | 2026-02-07T09:58:19Z |
| Tasks | 2/2 |
| Files created | 1 |
| Files modified | 1 |

## Accomplishments

### FSRS Processor Task (`memora_admin/tasks/fsrs_processor.py`)
- **`process_fsrs_reviews()`**: Scheduled every 1 minute, processes recent stage completions
- Queries Memora Interaction Log (last 2 minutes, "Completed" events)
- Filters out skippable stages via Memora Lesson Stage Settings `is_skippable` flag
- Maps `errors_count` to FSRS `Rating`: 0 fails=Good, 1=Hard, 2+=Again
- Loads FSRS weights from Memora Settings `fsrs_weights` (JSON array of 21 floats)
- Creates `fsrs.Scheduler(parameters=weights)` with fallback to defaults
- For each interaction: loads/creates FSRS Card, applies review, persists state
- Upserts Memora Memory State DocType (stability, difficulty, next_review)
- Caches FSRS state in Redis (`memora:fsrs:{player}:{stage_id}`, 24hr TTL)
- Idempotency: Redis key `memora:fsrs:processed:{player}:{stage_id}:{creation}` with 5min TTL

### Hooks Registration
- Added `process_fsrs_reviews` to the every-minute cron schedule in `hooks.py`
- Runs alongside existing sync tasks (progress, wallets, interaction flush)

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Create FSRS processor background task | `4e91625` | `memora_admin/tasks/fsrs_processor.py` |
| 2 | Register FSRS task in hooks.py scheduler | `c71529a` | `memora_admin/hooks.py` |

## Files Created

| File | Purpose |
|------|---------|
| `memora_admin/tasks/fsrs_processor.py` | Scheduled FSRS processor task (268 lines) |

## Files Modified

| File | Change |
|------|--------|
| `memora_admin/hooks.py` | Added FSRS task to every-minute cron schedule |

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Processing location | Background task (not hot path) | Keeps lesson completion under 10ms |
| Data source | Memora Interaction Log (not Redis buffer) | Already synced by flush_interaction_buffer |
| Idempotency | Redis key with 5min TTL per interaction | Handles 2-minute overlap window safely |
| Subject resolution | Direct Lesson.subject field with chain fallback | Denormalized field is O(1); chain is safety net |
| Rating mapping | 0=Good, 1=Hard, 2+=Again | Per research spec for mapping fail counts |
| FSRS API | `Scheduler(parameters=weights)` v6.x | No Parameters class in v6.x; accepts tuple/list |

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

None.

## Next Phase Readiness

This completes Phase 20 (Lesson Complete Pipeline Overhaul). All 4 plans are now complete:

- **20-01**: Hierarchy API returns base_xp, max_hearts; GamificationSettings with xp_per_heart; fsrs>=6.0.0
- **20-02**: StageResult.time_spent documented as milliseconds; legacy POST /progress/complete removed
- **20-03**: SESSION_COMPLETE_SCRIPT Lua for atomic completion; end_session rewritten with pipeline; hearts bonus XP
- **20-04**: FSRS background processor with configurable weights, skippable stage filtering, dual persistence

The full lesson completion pipeline is now operational:
1. Hot path: FastAPI end_session -> Lua atomic completion + pipeline writes (~6-7 Redis RTs)
2. Background: flush_interaction_buffer -> Memora Interaction Log (every 1 min)
3. Background: process_fsrs_reviews -> FSRS state to Memory State + Redis cache (every 1 min)

## Self-Check: PASSED
