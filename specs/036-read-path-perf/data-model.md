# Data Model: Progress & Practice Read-Path Performance

**Feature Branch**: `036-read-path-perf`
**Date**: 2026-03-03

## Overview

No new data models, database tables, or Redis key patterns are introduced. This feature optimizes how existing data structures are read.

## Existing Data Structures (Referenced, Not Changed)

### Stats Redis Hash

**Key**: `memora:stats:{user_id}:{subject_id}:v{version}`
**Type**: HASH
**TTL**: 1 hour (with 0-120s jitter)

| Field Pattern | Type | Description |
|--------------|------|-------------|
| `completed` | string(int) | Subject-level completed lesson count |
| `total` | string(int) | Subject-level total lesson count |
| `_content_hash` | string(hex) | First 8 chars of MD5 hash of structural fields |
| `{track_id}:completed` | string(int) | Track-level completed count |
| `{track_id}:total` | string(int) | Track-level total count |
| `{unit_id}:completed` | string(int) | Unit-level completed count |
| `{unit_id}:total` | string(int) | Unit-level total count |
| `{topic_id}:completed` | string(int) | Topic-level completed count |
| `{topic_id}:total` | string(int) | Topic-level total count |

**Read patterns being optimized**:
- Current: `HGETALL` (returns all ~500 fields regardless of endpoint)
- Optimized: `HMGET` with targeted field lists for partial endpoints

### Progress Bitmap

**Key**: `memora:progress:{user_id}:{subject_id}:v{version}`
**Type**: STRING (binary bitmap)
**TTL**: 48 hours

**Read patterns being optimized**:
- Current: `BITFIELD GET u8` (full bitmap decode) on every progress endpoint call
- Optimized: Skipped entirely when stats cache is valid; only decoded on stats miss/stale

### Hierarchy Cache

**Key**: `memora:hierarchy:{subject_id}`
**Type**: STRING (JSON)
**TTL**: 1 hour

**Read patterns being optimized**:
- Current: Frappe API call on every concurrent cache miss
- Optimized: Per-key coalescing ensures at most one Frappe call per miss per worker

### Practice Metadata Cache

**Key**: `memora:practice:hierarchy_meta:{subject_id}`
**Type**: STRING (JSON)
**TTL**: 1 hour

**Read patterns being optimized**:
- Current: Frappe API call on every concurrent cache miss
- Optimized: Per-key coalescing (same as hierarchy)

## New Internal Structures

### Stats-Derived Unlock Check

A pure function that derives unlock state from stats hash fields instead of bitmap iteration:

```
Input: stats dict, hierarchy structure, entity position (track_idx, unit_idx, topic_idx)
Output: bool (unlocked/locked)

Logic: "Previous sibling complete" = completed >= total AND total > 0
- Track N unlocked: track N-1 completed >= total (or N=0, or not linear)
- Unit M unlocked: parent track unlocked AND unit M-1 completed >= total (or M=0, or not linear)
- Topic K unlocked: parent unit unlocked AND topic K-1 completed >= total (or K=0, or not linear)
```

### Cache-Fill Lock Registry

Process-local dict (same pattern as existing `_compute_locks` in `stats.py`):

```
_hierarchy_fill_locks: dict[str, asyncio.Lock]   # in hierarchy.py
_meta_fill_locks: dict[str, asyncio.Lock]         # in practice.py
```

Keyed by subject_id. Bounded with pruning. No persistence.
