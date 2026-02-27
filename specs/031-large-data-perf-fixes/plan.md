# Implementation Plan: Large-Data Performance Fixes

**Branch**: `031-large-data-perf-fixes` | **Date**: 2026-02-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/031-large-data-perf-fixes/spec.md`

## Summary

Two targeted performance fixes for CPU-bound bottlenecks discovered during load testing (5 subjects x 10,000 lessons, 1,000 concurrent users). Fix 1: module-level in-process LRU cache for parsed `SubjectHierarchy` objects (eliminates 50-100ms JSON parse per request). Fix 2: per-key `asyncio.Lock` coalescing inside `StatsService.get_or_recompute()` (eliminates redundant bitmap recomputation on cold start).

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, redis.asyncio, Pydantic v2, structlog, asyncio
**Storage**: Redis at `redis://127.0.0.1:13001` (dedicated Memora instance) — no schema changes
**Testing**: pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio (real Redis, no mocking)
**Target Platform**: Linux server (uvicorn workers, typically 4)
**Project Type**: Web (FastAPI sidecar within Frappe bench)
**Performance Goals**: p50 < 50ms for progress endpoints under 1,000 concurrent users with 10,000-lesson subjects (down from 310ms)
**Constraints**: < 50MB additional memory per worker; zero changes to API contracts
**Scale/Scope**: 100k concurrent users, 5 subjects, ~10,000 lessons per subject

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Self-Healing Cache Architecture | **PASS** | In-process cache is an additional layer on top of Redis. Redis remains the distributed cache, MariaDB remains source of truth. Cache miss self-healing unchanged. `invalidate()` clears both local and Redis. |
| II. Sub-20ms Game API Performance | **PASS** | This feature directly improves performance toward the sub-20ms target. Hierarchy lookup drops from 50-100ms to < 1ms on local cache hit. |
| III. Content Hierarchy Integrity | **PASS** | No changes to hierarchy structure, bitmap versioning, or bit index management. |
| IV. Double-Gate Access Control | **PASS** | No changes to access control logic. |
| V. Cryptographic Voucher Security | **N/A** | Not applicable. |
| VI. Financial Precision | **N/A** | Not applicable. |
| VII. Auditable State Machines | **N/A** | Not applicable. |
| VIII. Test-First Coverage | **PASS** | Existing tests must pass; new assertions added for local cache invalidation and lock coalescing. |

**Post-Phase-1 Re-Check**: All gates still pass. No new Redis keys, no new API endpoints, no schema changes. Pure in-process optimization.

## Project Structure

### Documentation (this feature)

```text
specs/031-large-data-perf-fixes/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: in-process data structures
├── quickstart.md        # Phase 1: implementation guide
├── contracts/           # Phase 1: API contract analysis (no changes)
│   └── README.md
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (files to modify)

```text
fastapi_app/
├── services/
│   ├── hierarchy.py     # Fix 1: Add module-level local cache
│   └── stats.py         # Fix 2: Add module-level per-key locks
└── tests/
    ├── test_hierarchy_service.py  # Update: local cache assertions
    └── test_stats_service.py      # Update: lock coalescing test
```

**Structure Decision**: No new files. Both fixes modify existing service files. Tests update existing test files.

## Design Decisions

### D1: Module-Level Cache (not instance-level)

**Problem**: `deps.py` creates new `HierarchyService` and `StatsService` instances per request. Instance-level dicts would be empty every time.

**Solution**: Module-level dicts, matching existing patterns:
- `deps.py:51` — `_session_fid_cache: TTLCache` (module-level)
- `stats.py:18` — `_stats_recompute_semaphore` (module-level)

**Implementation**:
```python
# In hierarchy.py (module level)
_local_hierarchy_cache: dict[str, tuple[SubjectHierarchy, float]] = {}

# In stats.py (module level)
_compute_locks: dict[str, asyncio.Lock] = {}
```

### D2: Per-Key Lock Inside Existing `get_or_recompute()` (not a new method)

**Problem**: The user proposed a new `get_or_compute_stats()` method. However, all 4 progress endpoints already call `get_or_recompute()` — they were refactored after the user wrote the description.

**Solution**: Add per-key locking directly inside `get_or_recompute()`. Zero endpoint code changes.

**Flow**:
1. Fast path: cache hit with matching content hash → return (no lock, no semaphore)
2. Slow path: acquire per-key lock → double-check cache → acquire semaphore → recompute → cache → release both

### D3: Local Cache TTL via `time.monotonic()`

**Why monotonic**: Immune to system clock changes (NTP adjustments, DST). Standard practice for measuring elapsed time intervals.

**Why 5 minutes**: Hierarchy changes only on admin content rebuilds (rare). 5 minutes means at most 5-minute stale window if pub/sub invalidation fails. The `invalidate()` method provides immediate invalidation when pub/sub works.

### D4: Test Fixture Cleanup

Module-level caches persist across tests within the same pytest process. Test fixtures must clear them to prevent test pollution:
- Clear `_local_hierarchy_cache` before each test
- Clear `_compute_locks` before each test
