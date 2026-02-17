# Data Model: Characterization Tests

**Feature**: 015-characterization-tests
**Date**: 2026-02-17

## Overview

This feature creates tests only — no new data models, entities, or storage. The tests interact with existing data structures documented below.

## Existing Entities Under Test

### Wallet Hash (Redis)

- **Key**: `memora:wallet:{player_id}`
- **Type**: Redis Hash
- **Fields**:
  - `xp` (int) — total XP balance
  - `streak` (int) — current streak count
  - `streak_date` (string, YYYY-MM-DD) — last streak update date
- **Relevant to**: FINDING-01 (XP hydration failure)
- **Key behavior**: HINCRBY on missing field creates it with 0 before incrementing

### Interaction Buffer (Redis)

- **Key**: `memora:buffer:interactions`
- **Type**: Redis List
- **Items**: JSON strings with fields: `player`, `lesson`, `stage_id`, `item_id`, `event_type`, `started_at`, `completed_at`
- **Relevant to**: FINDING-02 (LTRIM boundary)
- **Key behavior**: LTRIM(start, -1) removes elements from index 0 to start-1

### Stats Hash (Redis)

- **Key**: `memora:stats:{user_id}:{subject_id}:v{version}`
- **Type**: Redis Hash
- **Fields**:
  - `completed` (int) — total completed lessons
  - `total` (int) — total lessons
  - `{track_id}:completed` (int) — per-track completed
  - `{track_id}:total` (int) — per-track total
  - `{unit_id}:completed` (int) — per-unit completed
  - `{topic_id}:completed` (int) — per-topic completed
- **TTL**: 3600 seconds
- **Relevant to**: FINDING-03 (double-counting race)
- **Key behavior**: EXISTS check followed by either HSET (cold) or HINCRBY (warm) is non-atomic

### Dirty Wallets Set (Redis)

- **Key**: `memora:dirty:wallets`
- **Type**: Redis Set
- **Members**: Player IDs pending MariaDB sync
- **Relevant to**: FINDING-01 (player added to dirty set even when XP reset to 0)

## State Transitions

### FINDING-01: XP Award with Hydration Failure

```
State: Wallet missing from Redis, Frappe unreachable
  ↓ ensure_hydrated() → exception caught, returns silently
  ↓ HINCRBY wallet_key "xp" 50 → creates new hash, xp = 50 (BUG: should be old_xp + 50)
  ↓ SADD dirty:wallets player_id → player queued for sync
Result: XP reset to award amount only, dirty sync will OVERWRITE MariaDB with wrong value
```

### FINDING-02: Buffer Flush with Partial Failure

```
State: Buffer = [item0, item1, item2, item3, item4], items 0,2,4 succeed, items 1,3 fail
  ↓ inserted = 3 (count of successes)
  ↓ LTRIM buffer 3 -1 → keeps items at index 3+ → [item3, item4]
Result: item1 dropped (failed, never retried), item2 dropped (succeeded but trimmed)
  BUG: LTRIM treats count as index
```

### FINDING-03: Stats Cold-Start Race

```
State: No stats hash exists, two concurrent completions for same user+subject
  ↓ Request 1: EXISTS stats_key → 0 (cold start)
  ↓ Request 2: EXISTS stats_key → 0 (cold start, same window)
  ↓ Request 1: compute_stats_from_hierarchy() → {completed: N}
  ↓ Request 1: set_stats() → HSET stats_key {completed: N}
  ↓ Request 2: EXISTS stats_key → 1 (warm, Request 1 set it)
  ↓ Request 2: HINCRBY stats_key completed 1 → completed = N+1
Result: completed = N+1, but N already includes Request 2's completion (bitmap was set in Lua)
  BUG: Double-count of Request 2's lesson
```

## No New Entities

This feature does not create new data models. All tests operate on the existing Redis structures listed above.
