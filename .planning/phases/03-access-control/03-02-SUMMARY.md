---
phase: 03
plan: 02
subsystem: access-control
tags: [redis, frappe-hooks, access-sets, doc-events]
dependency-graph:
  requires: [02-auth, 01-redis]
  provides: [access-service, subscription-sync, season-sync]
  affects: [03-03-gates, 03-04-webhooks]
tech-stack:
  added: []
  patterns: [redis-sets, doc-events-hooks, o1-membership-check]
key-files:
  created:
    - fastapi_app/services/access.py
    - memora_admin/events/__init__.py
    - memora_admin/events/access_sync.py
  modified:
    - fastapi_app/services/__init__.py
    - memora_admin/hooks.py
decisions:
  - key: player-access-key-pattern
    choice: memora:access:{user_id}
    rationale: Consistent with session key pattern from Phase 2
  - key: grant-sync-approach
    choice: Direct cache.sadd/srem in doc_events
    rationale: Sub-second propagation required per CONTEXT.md
metrics:
  duration: 2min
  completed: 2026-02-02
---

# Phase 03 Plan 02: Player Access Sets Summary

**One-liner:** Redis set-based player access management with Frappe doc_events hooks for immediate subscription/season sync

## What Was Built

### AccessService (FastAPI)

Created `fastapi_app/services/access.py` with AccessService class providing:

- **check_access**: O(1) membership check via SISMEMBER
- **grant_access**: Idempotent grant addition via SADD (returns count of new grants)
- **revoke_access**: Grant removal via SREM
- **get_player_grants**: Full grant set retrieval for admin/debugging

Key pattern: `memora:access:{player_id}` stores set of content keys (e.g., "SUB-MATH", "TRK-MATH-01")

### Subscription Sync Handlers (Frappe)

Created `memora_admin/events/access_sync.py` with:

- **on_subscription_change**: Syncs grant to Redis on subscription create/update
- **on_subscription_deleted**: Removes grant on subscription delete
- **on_season_change**: Syncs season metadata to Redis hash on create/update
- **on_season_deleted**: Removes season from Redis cache on delete

Configured doc_events in `hooks.py` for both Memora Season and Memora Player Subscription doctypes.

## Key Implementation Details

### O(1) Access Checks

```python
# fastapi_app/services/access.py
async def check_access(self, player_id: str, content_key: str) -> bool:
    key = self._access_key(player_id)
    result = await self.redis.sismember(key, content_key)
    return bool(result)
```

### Immediate Grant Propagation

```python
# memora_admin/events/access_sync.py
def on_subscription_change(doc, method):
    cache = frappe.cache
    redis_key = f"memora:access:{user_id}"
    if doc.is_active:
        cache.sadd(redis_key, access_key)
    else:
        cache.srem(redis_key, access_key)
```

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Redis key pattern | `memora:access:{user_id}` | Consistent with session pattern, uses user_id not player docname |
| Grant sync method | Direct `cache.sadd/srem` in doc_events | Sub-second propagation per CONTEXT.md requirement |
| Player ID resolution | Look up user from Player Profile | Subscription links to Player Profile, but Redis key needs user_id |

## Deviations from Plan

### Auto-added (Rule 2)

**1. Season sync handlers**
- **Found during:** Task 2 planning
- **Issue:** Plan only mentioned subscription handlers, but RESEARCH.md shows season handlers needed for Gate 1
- **Fix:** Added on_season_change and on_season_deleted handlers
- **Files modified:** memora_admin/events/access_sync.py
- **Commit:** d1af1a9

## Verification Results

- AccessService imports correctly with all methods present
- check_access uses SISMEMBER for O(1) lookup
- grant_access uses SADD for idempotent grants
- on_subscription_change handler exists and uses cache.sadd
- Both Memora Season and Memora Player Subscription configured in hooks.py

## Next Phase Readiness

Ready for Plan 03-03 (Gate dependencies):
- AccessService provides check_access for Gate 2 validation
- Season sync enables Gate 1 season validation
- Redis key patterns established and documented

## Artifacts

| File | Purpose | Exports |
|------|---------|---------|
| fastapi_app/services/access.py | Player access set operations | AccessService |
| memora_admin/events/access_sync.py | Subscription/season sync handlers | on_subscription_change, on_subscription_deleted, on_season_change, on_season_deleted |
| memora_admin/hooks.py | doc_events configuration | doc_events dict |

## Commit Log

| Hash | Type | Description |
|------|------|-------------|
| ef19b7f | feat | Create AccessService for player grants |
| d1af1a9 | feat | Add subscription sync handlers to Frappe hooks |
