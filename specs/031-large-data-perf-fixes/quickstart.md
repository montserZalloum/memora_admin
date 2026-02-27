# Quickstart: Large-Data Performance Fixes

**Feature**: 031-large-data-perf-fixes
**Branch**: `031-large-data-perf-fixes`

## What This Feature Does

Adds two in-process caching/locking mechanisms to eliminate CPU-bound bottlenecks discovered during load testing with 5 subjects x 10,000 lessons under 1,000 concurrent users.

## Files to Modify

| File | Change |
|---|---|
| `fastapi_app/services/hierarchy.py` | Add module-level local cache dict; update `get_hierarchy()`, `invalidate()`, `invalidate_all()` |
| `fastapi_app/services/stats.py` | Add module-level per-key lock dict; update `get_or_recompute()` with lock coalescing |
| `fastapi_app/tests/test_hierarchy_service.py` | Add local cache assertions to invalidation test; add new test for local cache hit |
| `fastapi_app/tests/test_stats_service.py` | Add test for concurrent lock coalescing |

## Implementation Order

1. **Fix 1**: `hierarchy.py` — add `_local_hierarchy_cache` module-level dict + `LOCAL_TTL` constant
2. **Fix 1 tests**: Update `test_hierarchy_service.py` — verify local cache hit, invalidation clears local cache
3. **Fix 2**: `stats.py` — add `_compute_locks` module-level dict; wrap `get_or_recompute()` slow path in per-key lock
4. **Fix 2 tests**: Update `test_stats_service.py` — verify compute function called exactly once under concurrency

## How to Verify

```bash
# Run all affected test suites
python3 -m pytest fastapi_app/tests/test_hierarchy_service.py -v
python3 -m pytest fastapi_app/tests/test_stats_service.py -v
python3 -m pytest fastapi_app/tests/test_progress_service.py -v
python3 -m pytest fastapi_app/tests/test_progress_endpoints.py -v
```

## Key Design Decision

Both caches use **module-level dicts** (not instance attributes) because `deps.py` creates new service instances per request. Module-level is the established pattern in this codebase (`_session_fid_cache` in deps.py, `_stats_recompute_semaphore` in stats.py).
