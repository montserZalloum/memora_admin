# Implementation Plan: Progress & Practice Read-Path Performance

**Branch**: `036-read-path-perf` | **Date**: 2026-03-03 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/036-read-path-perf/spec.md`

## Summary

Optimize high-traffic progress and practice read paths by: (1) using cached stats before bitmap decode, (2) reading only needed stats fields via HMGET, (3) coalescing concurrent hierarchy/metadata cache fills, (4) hoisting subject-level access out of practice hierarchy's per-track loop, and (5) bounding progress summary fan-out with a semaphore. Zero API contract changes.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, redis.asyncio, Pydantic v2, structlog, asyncio
**Storage**: Redis at `redis://127.0.0.1:13001` (stats hash, hierarchy JSON, practice metadata, progress bitmap)
**Testing**: pytest 8.4.2, pytest-asyncio 0.26.0, httpx 0.28.1, redis.asyncio (all pre-installed)
**Target Platform**: Linux server (single-server deployment, 4 uvicorn workers)
**Project Type**: Backend API (FastAPI sidecar)
**Performance Goals**: Progress fetch <20ms, access check <2ms, 100k concurrent users
**Constraints**: Zero API contract changes, preserve all unlock rules and business logic
**Scale/Scope**: 6 files modified, ~200-300 lines changed, no new files except tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | **PASS** | No new Redis keys. Stats-first is a read optimization on existing cache. Fallback to bitmap preserves self-healing. |
| II. Sub-20ms Game API | **PASS** | This feature directly improves hot-path latency. No Frappe ORM in hot paths. All endpoints remain async. |
| III. Content Hierarchy Integrity | **PASS** | Content hash validation preserved. Stats-derived unlock checks use same `completed >= total` semantic. Bitmap versioning unchanged. |
| IV. Double-Gate Access Control | **PASS** | Access hoisting preserves both gates. Subject-level check computed once, track-level still checked per track. |
| V. Cryptographic Voucher Security | **N/A** | No voucher code affected. |
| VI. Financial Precision | **N/A** | No financial calculations affected. |
| VII. Auditable State Machines | **N/A** | No state machines affected. |
| VIII. Test-First Coverage | **PASS** | Existing tests must pass. New tests for stats-derived unlock helpers and partial reads. |

**Gate result**: PASS — all applicable principles satisfied.

### Post-Design Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | **PASS** | Stats miss → bitmap fallback → recompute. Hierarchy miss → Frappe fill (coalesced). No new Redis-only state. |
| II. Sub-20ms Game API | **PASS** | Stats-first eliminates bitmap decode on warm path. HMGET reduces Redis payload. Coalescing reduces upstream load. |
| III. Content Hierarchy Integrity | **PASS** | `_content_hash` match required before using stats. Mismatch triggers bitmap recompute. |
| IV. Double-Gate Access Control | **PASS** | Hoisted subject check is semantically identical. Track-level grants still checked individually. |
| VIII. Test-First Coverage | **PASS** | Stats-derived unlock helpers tested in isolation. Integration tests verify end-to-end response correctness. |

## Project Structure

### Documentation (this feature)

```text
specs/036-read-path-perf/
├── plan.md              # This file
├── research.md          # Phase 0 output — technical decisions
├── data-model.md        # Phase 1 output — existing data structures reference
├── quickstart.md        # Phase 1 output — testing & deployment guide
├── contracts/           # Phase 1 output — no API changes
│   └── no-changes.md
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (files modified)

```text
fastapi_app/
├── services/
│   ├── stats.py            # Add get_partial_stats() HMGET method
│   ├── hierarchy.py        # Add per-key cache-fill coalescing locks
│   └── practice.py         # Hoist subject access + add meta coalescing locks
├── api/v1/endpoints/
│   └── progress.py         # Stats-first read path + bounded concurrency
├── core/
│   └── config.py           # Add PROGRESS_SUMMARY_CONCURRENCY setting (optional)
└── tests/
    ├── test_stats_service.py          # Add partial read tests
    ├── test_progress_endpoints.py     # Verify unchanged responses
    └── test_practice_endpoints.py     # Verify unchanged responses (if exists)
```

**Structure Decision**: All changes are modifications to existing files in the FastAPI sidecar. No new source files needed (only test additions within existing test files).

## Implementation Design

### Task 1: Stats-Derived Unlock Helpers (progress.py)

Add pure functions that derive unlock state from stats dict instead of bitmap `completed_bits`:

```python
def _is_entity_complete_from_stats(entity_id: str, stats: dict[str, str]) -> bool:
    """Check if entity is complete using cached stats (completed >= total, total > 0)."""
    completed = int(stats.get(f"{entity_id}:completed", "0"))
    total = int(stats.get(f"{entity_id}:total", "0"))
    return total > 0 and completed >= total
```

Unlock helpers follow identical logic to existing `_is_track_complete`, `_is_unit_unlocked`, `_is_topic_unlocked` but read from `stats` dict instead of iterating `completed_bits`.

**Validation rule**: Stats must have `_content_hash` matching `hierarchy.content_hash` before being used for unlock derivation. This is already enforced by `get_or_recompute()`.

### Task 2: StatsService.get_partial_stats() (stats.py)

Add targeted HMGET method:

```python
async def get_partial_stats(
    self, user_id: str, subject_id: str, version: int, fields: list[str],
) -> dict[str, str] | None:
    """Read specific fields from stats hash via HMGET.

    Returns dict of field->value for requested fields, or None if key doesn't exist.
    Fields not found in hash return None (excluded from result dict).
    """
    key = self._stats_key(user_id, subject_id, version)
    values = await self.redis.hmget(key, fields)
    result = {}
    for field, value in zip(fields, values):
        if value is not None:
            result[field] = value.decode() if isinstance(value, bytes) else value
    return result if result else None
```

### Task 3: Stats-First Endpoint Refactor (progress.py)

Refactor partial endpoints (tracks, track_detail, unit_detail) to:

1. Compute needed field names from hierarchy structure
2. Call `stats_service.get_partial_stats(fields)` with `_content_hash` included
3. If stats valid (hash matches + all needed fields present), use stats for counts and unlock
4. If stats missing/stale, fall back to current path: `get_completed_bits()` → `get_or_recompute()`

**Full subject endpoint** (`GET /progress/{subject}`): Same stats-first pattern but uses `get_stats()` (HGETALL) since it needs all fields anyway. Bitmap decode skipped when stats are valid.

**Summary endpoint** (`GET /progress/`): Reads `completed`, `total`, `_content_hash` from stats via partial read. Falls back to `get_completed_count()` (BITCOUNT) on miss. Wrapped in semaphore for bounded concurrency.

### Task 4: Hierarchy Cache-Fill Coalescing (hierarchy.py)

Add per-key locks to prevent stampede on concurrent cache misses:

```python
_hierarchy_fill_locks: dict[str, asyncio.Lock] = {}
_MAX_FILL_LOCKS = 5_000
FILL_TIMEOUT = 5.0  # seconds

def _get_fill_lock(key: str) -> asyncio.Lock:
    """Same pattern as stats.py _get_compute_lock."""
    ...
```

In `get_hierarchy()`, wrap the Frappe call path:

```python
# After Redis miss, before Frappe call:
lock = _get_fill_lock(subject_id)
try:
    await asyncio.wait_for(lock.acquire(), timeout=FILL_TIMEOUT)
    acquired = True
except asyncio.TimeoutError:
    acquired = False
    # Proceed without lock (bounded duplicate work)

try:
    if acquired:
        # Double-check Redis (another request may have filled)
        cached = await self.redis.get(key)
        if cached:
            # Use it (skip Frappe)
            ...
            return hierarchy
    # Fetch from Frappe
    result = await self.frappe.call(...)
    ...
finally:
    if acquired:
        lock.release()
```

### Task 5: Practice Metadata Coalescing (practice.py)

Same lock pattern as hierarchy, applied to `_load_hierarchy_meta()`:

```python
_meta_fill_locks: dict[str, asyncio.Lock] = {}
```

Double-check Redis after acquiring lock, before Frappe call.

### Task 6: Subject Access Hoisting (practice.py)

In `get_practice_hierarchy()`, compute subject access once before the track loop:

```python
# Before loop:
subject_key = f"SUB-{subject_id}"
has_subject_access = await self.access.check_access_with_plan(player_id, subject_key, plan_id)

# In loop (replaces _check_track_access call):
for track in hier.tracks:
    if has_subject_access:
        has_full_access = True
    else:
        track_key = f"TRK-{track.track_id}"
        has_full_access = await self.access.check_access(player_id, track_key)
    has_free = self._track_has_free_content(hier, track_id)
    has_access = has_full_access or has_free
```

The `_check_track_access()` method can either be updated to accept a pre-computed subject result or inlined in the loop.

### Task 7: Bounded Progress Summary Concurrency (progress.py)

Replace unbounded `asyncio.gather()` with semaphore-limited pattern:

```python
PROGRESS_SUMMARY_CONCURRENCY = 6  # Module constant

async def get_progress_summary(...):
    ...
    sem = asyncio.Semaphore(PROGRESS_SUMMARY_CONCURRENCY)

    async def _bounded_fetch(subject_id: str) -> SubjectSummary | None:
        async with sem:
            return await _fetch_subject_summary(subject_id)

    results = await asyncio.gather(
        *(_bounded_fetch(sid) for sid in all_accessible),
        return_exceptions=True,
    )
```

### Task 8: Production Tuning Documentation

Document recommended production `.env` values in `quickstart.md` (already done in Phase 1 artifact).

## Delivery Order

| Order | Task | Files | Dependency | FR Coverage |
|-------|------|-------|------------|-------------|
| 1 | Stats-derived unlock helpers | `progress.py` | None | FR-001, FR-002 |
| 2 | `get_partial_stats()` HMGET | `stats.py` | None | FR-004 |
| 3 | Stats-first endpoint refactor | `progress.py` | Tasks 1, 2 | FR-001–005, FR-015 |
| 4 | Hierarchy cache-fill coalescing | `hierarchy.py` | None | FR-006, FR-008, FR-009 |
| 5 | Practice metadata coalescing | `practice.py` | None | FR-007, FR-008, FR-009 |
| 6 | Subject access hoisting | `practice.py` | None | FR-010, FR-011 |
| 7 | Bounded progress summary | `progress.py` | None | FR-012–014 |
| 8 | Production tuning docs | `quickstart.md` | None | FR-017 |

Tasks 1-2 are foundations. Task 3 depends on them. Tasks 4-7 are independent of each other and can be implemented in any order or in parallel.
