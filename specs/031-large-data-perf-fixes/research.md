# Research: Large-Data Performance Fixes

**Feature**: 031-large-data-perf-fixes
**Date**: 2026-02-27

## Research Question 1: In-Process Cache Lifetime — Instance vs Module Level

### Context

The user's proposed design stores `_local_cache` as an instance attribute of `HierarchyService`. However, `deps.py:281-284` creates a **new** `HierarchyService` instance per request:

```python
async def get_hierarchy_service(redis_client: RedisClient) -> HierarchyService:
    frappe_client = await get_frappe_client()
    return HierarchyService(redis_client, frappe_client)
```

An instance-level dict would be empty on every request — defeating the purpose.

### Decision: Module-level dict (same pattern as existing code)

Use a **module-level dict** in `hierarchy.py`, matching the established pattern:

- `deps.py:51` — `_session_fid_cache: TTLCache[str, str] = TTLCache(maxsize=10_000, ttl=5)` (module-level, per-worker)
- `stats.py:18` — `_stats_recompute_semaphore: asyncio.Semaphore | None = None` (module-level, per-worker)

The `HierarchyService` methods access the module-level dict. Each uvicorn worker process gets its own copy (no cross-worker sharing needed).

### Rationale

- Matches existing codebase conventions
- No changes to `deps.py` or DI pattern required
- Each worker has its own cache (acceptable: 5 subjects x ~2MB = ~10MB per worker)
- TTL management via `time.monotonic()` comparison (same as proposed)

### Alternatives Considered

| Alternative | Rejected Because |
|---|---|
| Instance-level dict | New instance per request — cache always empty |
| Class-level (static) dict | Works but less explicit; module-level is the codebase convention |
| Singleton HierarchyService (like FrappeClient) | Requires DI changes in deps.py; over-engineered for this use case |
| Store on `app.state` | Requires passing app reference into service; breaks service encapsulation |

---

## Research Question 2: Per-Key Lock Lifetime — Instance vs Module Level

### Context

Same issue as HierarchyService: `deps.py:246-248` creates a new `StatsService` per request. Instance-level `_compute_locks` dict would be empty every time.

### Decision: Module-level dict in `stats.py`

Add `_compute_locks: dict[str, asyncio.Lock] = {}` at module level in `stats.py`, right next to the existing `_stats_recompute_semaphore`.

### Rationale

- Identical pattern to existing `_stats_recompute_semaphore`
- Each worker has its own lock dict (asyncio.Lock is per-event-loop, matches single-worker model)
- No DI changes required

---

## Research Question 3: Interaction Between `get_or_recompute()` and New `get_or_compute_stats()`

### Context

StatsService already has `get_or_recompute()` which:
1. Checks cache with content hash validation
2. Uses a global semaphore (max 30 concurrent recomputes)
3. Takes `completed_bits` and `hierarchy` directly (not a callback)

The progress endpoints already call `get_or_recompute()`, NOT inline `if stats is None` patterns. The code has been refactored since the user wrote the spec description.

### Decision: Add per-key locking INSIDE `get_or_recompute()` instead of creating a separate method

Since all progress endpoints already call `get_or_recompute()`, the simplest and safest approach is to add per-key locking directly inside this existing method. This avoids touching any endpoint code.

The flow becomes:
1. **Fast path**: Cache hit with matching content hash → return immediately (no lock)
2. **Slow path**: Acquire per-key lock → double-check cache → if still miss, acquire semaphore → recompute → cache → release

This replaces the semaphore-only approach with a two-tier strategy: per-key lock prevents duplicate work for the same key, semaphore limits total system-wide recompute concurrency.

### Rationale

- Zero changes to progress endpoint code (all 4 endpoints already use `get_or_recompute`)
- Per-key lock eliminates the thundering herd for same-key requests
- Semaphore still limits total system-wide CPU usage
- Double-check pattern inside lock prevents redundant recomputation

### Alternatives Considered

| Alternative | Rejected Because |
|---|---|
| New `get_or_compute_stats()` method + update all endpoints | Unnecessary churn — endpoints already call `get_or_recompute` |
| Replace semaphore with per-key locks only | Semaphore still useful as system-wide backpressure |
| `asyncio.Event` broadcast pattern | More complex, no clear benefit over lock + double-check |

---

## Research Question 4: Cache Invalidation and `invalidate()` Callers

### Context

Who calls `HierarchyService.invalidate()` and `invalidate_all()`? Need to ensure the module-level local cache is also cleared.

### Findings

`invalidate()` and `invalidate_all()` are called from within the `HierarchyService` instance methods. Since these methods will now also clear the module-level `_local_hierarchy_cache`, invalidation works correctly regardless of which HierarchyService instance calls it (they all access the same module-level dict).

External callers (Frappe hooks via Redis pubsub) trigger invalidation through the FastAPI pubsub subscriber, which calls `hierarchy_service.invalidate(subject_id)`. This correctly clears both Redis and local cache.

### Decision: No additional invalidation plumbing needed

The existing invalidation flow already calls `invalidate()`, which will be updated to clear the module-level local cache. No new pubsub channels or hooks required.

---

## Research Question 5: Test Impact

### Context

Existing tests create `HierarchyService` instances directly in fixtures. The module-level local cache persists across tests within the same pytest process, which could cause test pollution.

### Decision: Clear module-level caches in test fixtures

Add cleanup to the test fixtures:
- `hierarchy_svc` fixture: Clear `_local_hierarchy_cache` in setup/teardown
- `stats_svc` fixture: Clear `_compute_locks` in setup/teardown

This matches how `_stats_recompute_semaphore` is implicitly reset (it's `None` initially and lazily created).

### Key test additions

1. **test_tc_hir_03** (invalidation test): Add assertion that after `invalidate()`, the local cache entry is removed and a subsequent `get_hierarchy()` call hits Redis (not local cache).
2. **New test for local cache hit**: Verify that the second `get_hierarchy()` call does NOT call `redis.get()` (returns from local cache).
3. **New test for local TTL expiry**: Mock `time.monotonic()` to simulate TTL expiry and verify re-fetch from Redis.
