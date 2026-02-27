# Data Model: Large-Data Performance Fixes

**Feature**: 031-large-data-perf-fixes
**Date**: 2026-02-27

## Overview

This feature introduces no new database tables, DocTypes, or Redis key patterns. It adds two in-process (Python memory) data structures that exist only within each uvicorn worker process.

## In-Process Data Structures

### 1. Hierarchy Local Cache

**Location**: Module-level dict in `fastapi_app/services/hierarchy.py`

| Field | Type | Description |
|---|---|---|
| Key | `str` | Subject ID (e.g., `"MATH-G5"`) |
| Value.hierarchy | `SubjectHierarchy` | Parsed Pydantic model (already defined in `models/progress.py`) |
| Value.expires_at | `float` | Monotonic clock timestamp when entry expires |

**Characteristics**:
- Scope: Per-worker process (not shared across workers)
- Max entries: ~5 (one per active subject)
- Memory per entry: ~2MB (940KB JSON parsed into Pydantic model with Python object overhead)
- Total memory: ~10MB per worker
- TTL: 5 minutes (configurable via `HierarchyService.LOCAL_TTL`)
- Eviction: TTL-based (checked on access) + explicit via `invalidate()`/`invalidate_all()`

### 2. Stats Compute Locks

**Location**: Module-level dict in `fastapi_app/services/stats.py`

| Field | Type | Description |
|---|---|---|
| Key | `str` | Stats Redis key string (e.g., `"memora:stats:USR001:MATH-G5:v3"`) |
| Value | `asyncio.Lock` | Per-key lock for compute coalescing |

**Characteristics**:
- Scope: Per-worker process, per-event-loop
- Max entries: Up to (active users) x (subjects) x (versions) per worker
- Memory per entry: ~100 bytes (`asyncio.Lock` object)
- Worst case: 500k entries = ~50MB per worker
- No TTL: Locks persist for worker lifetime (cleaned up on restart)
- No eviction: Growth is bounded by active user x subject combinations

## Existing Entities (Unchanged)

No changes to:
- `SubjectHierarchy` model (read-only usage)
- Redis key patterns (all keys via `redis_keys.py` unchanged)
- MariaDB tables
- DocType schemas
