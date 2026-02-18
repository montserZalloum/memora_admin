# API Contracts: Progress Endpoints (Updated for Content Hash)

**Feature**: 019-stats-content-hash
**Date**: 2026-02-18

## Overview

No new endpoints are added. Four existing progress endpoints gain content-hash-based staleness detection. Two endpoints remain unchanged (they don't use the stats cache).

## Modified Endpoints

### 1. GET /progress/{subject}

**Current behavior**: Returns subject-level progress from stats cache. Cold-start recompute if stats missing.

**New behavior**: Additionally recomputes if `stats._content_hash != hierarchy.content_hash`.

**Response schema** (unchanged):

```json
{
  "subject_id": "SUBJ-00704",
  "completed": 2,
  "total": 3,
  "percentage": 66.7
}
```

**Staleness check change**:
```python
# Before:
if stats is None or "total" not in stats:

# After:
if stats is None or "total" not in stats or stats.get("_content_hash") != hierarchy.content_hash:
```

---

### 2. GET /progress/{subject}/tracks

**Current behavior**: Returns track-level breakdowns from stats cache.

**New behavior**: Additionally recomputes if content hash mismatch.

**Response schema** (unchanged):

```json
{
  "subject_id": "SUBJ-00704",
  "tracks": [
    {
      "track_id": "TRK-00123",
      "completed": 1,
      "total": 2,
      "percentage": 50.0
    }
  ]
}
```

**Staleness check change**: Same pattern as endpoint 1.

---

### 3. GET /progress/{subject}/tracks/{track_id}

**Current behavior**: Returns unit-level breakdowns within a track from stats cache.

**New behavior**: Additionally recomputes if content hash mismatch.

**Response schema** (unchanged):

```json
{
  "track_id": "TRK-00123",
  "completed": 1,
  "total": 2,
  "percentage": 50.0,
  "units": [
    {
      "unit_id": "UNT-00456",
      "completed": 1,
      "total": 1,
      "percentage": 100.0
    }
  ]
}
```

**Staleness check change**: Same pattern as endpoint 1.

---

### 4. GET /progress/{subject}/tracks/{track_id}/units/{unit_id}

**Current behavior**: Returns topic-level breakdowns within a unit from stats cache.

**New behavior**: Additionally recomputes if content hash mismatch.

**Response schema** (unchanged):

```json
{
  "unit_id": "UNT-00456",
  "completed": 1,
  "total": 1,
  "percentage": 100.0,
  "topics": [
    {
      "topic_id": "TPC-00789",
      "completed": 1,
      "total": 1,
      "percentage": 100.0
    }
  ]
}
```

**Staleness check change**: Same pattern as endpoint 1.

---

## Unchanged Endpoints

### GET /progress/

**Reason**: Uses raw `BITCOUNT` via `get_completed_count()`, not stats cache. Already returns correct counts from bitmap.

### GET /progress/{subject}/topics/{topic_id}/lessons

**Reason**: Uses direct `GETBIT` per lesson. Already correct by design — FR-011 states staleness check must NOT apply to lesson-level endpoints.

---

## Internal Contracts

### `_compute_content_hash(hierarchy: dict) -> str`

**Location**: `memora_admin/api/hierarchy.py`
**Called by**: `get_subject_hierarchy()` (once per hierarchy build)

**Input**: Full hierarchy dict (as built by Frappe)
**Output**: 8-character hex string (MD5 truncated)
**Determinism**: Same input always produces same output
**Side effects**: None (pure function)

### `compute_stats_from_hierarchy(hierarchy, completed_bits) -> dict`

**Location**: `fastapi_app/services/stats.py`
**Change**: Output dict now includes `_content_hash` field

**Before**:
```python
{"completed": "2", "total": "3", "TPC-00499:completed": "2", "TPC-00499:total": "3", ...}
```

**After**:
```python
{"completed": "2", "total": "3", "TPC-00499:completed": "2", "TPC-00499:total": "3", ..., "_content_hash": "a1b2c3d4"}
```

### `StatsService.get_stats()` / `StatsService.set_stats()`

**No changes** — these are generic `HGETALL`/`HSET mapping` operations. The `_content_hash` field flows through as just another hash field.

### `StatsService.increment_completion_stats()`

**No changes** — FR-008. The 4x `HINCRBY` + `EXPIRE` warm path is untouched. `_content_hash` survives because `HINCRBY` only operates on specific `:completed` fields.
