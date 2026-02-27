# Feature Specification: Large-Data Performance Fixes

**Feature Branch**: `031-large-data-perf-fixes`
**Created**: 2026-02-27
**Status**: Draft
**Input**: User description: "Two Performance Fixes for Large-Data Bottlenecks — in-process LRU cache for parsed hierarchy models and per-key lock coalescing for stats cold-start computations"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fast Hierarchy Lookups Under Load (Priority: P1)

A student opens a subject with 10,000 lessons. The system retrieves the subject hierarchy from cache. Currently, even on a Redis cache hit, the server spends 50-100ms re-parsing 940KB of JSON into a Pydantic model on every single request. Under 1,000 concurrent users, this causes p50 latency to spike from 10ms to 310ms. After this fix, the parsed hierarchy object is cached in-process per worker, so subsequent requests for the same subject skip both the Redis round-trip and JSON parsing entirely.

**Why this priority**: This is the #1 bottleneck identified in load testing. Every progress, session, and access endpoint depends on `HierarchyService.get_hierarchy()`. Fixing this single function has the widest impact across all API endpoints.

**Independent Test**: Can be fully tested by calling `get_hierarchy()` twice for the same subject and verifying the second call returns instantly without touching Redis, and by verifying that `invalidate()` clears the local cache.

**Acceptance Scenarios**:

1. **Given** a subject hierarchy is already cached in Redis, **When** two requests arrive for the same subject within 5 minutes, **Then** only the first request parses JSON from Redis; the second returns the in-process cached object without any Redis call.
2. **Given** a subject hierarchy is cached in-process, **When** the local cache TTL (5 minutes) expires, **Then** the next request fetches from Redis and re-parses the JSON, storing the result in the local cache again.
3. **Given** a subject hierarchy is cached in-process, **When** `invalidate(subject_id)` is called (e.g., via pub/sub on content update), **Then** both the Redis key and the in-process cached entry are removed, and the next request fetches fresh data from Frappe.
4. **Given** a subject hierarchy is cached in-process, **When** `invalidate_all()` is called, **Then** all in-process cached entries are cleared along with Redis keys.
5. **Given** neither Redis nor in-process cache has the hierarchy, **When** a request arrives (full cache miss), **Then** the system fetches from Frappe, caches in Redis with 1-hour TTL, caches in-process with 5-minute TTL, and auto-repairs the free content set if applicable.

---

### User Story 2 - Eliminate Redundant Stats Recomputation on Cold Start (Priority: P2)

A student's stats cache expires (or Redis restarts). Under load, 50 concurrent requests arrive for the same user+subject and all independently compute the full bitmap-to-stats recompute (read 1,250 bytes bitmap, iterate 10,000 bits, compute per-track/unit/topic counts). This wastes CPU and causes latency pile-ups. After this fix, only one request per unique (user, subject, version) key computes stats; all others wait for the first to finish and then read the cached result.

**Why this priority**: This is the #2 bottleneck. The current global semaphore (capped at 30 concurrent recomputes) limits throughput but does NOT prevent duplicate work for the same key. Per-key locking ensures exactly one computation per cache miss.

**Independent Test**: Can be tested by triggering 10 concurrent `get_or_recompute()` calls for the same key with an empty cache, and verifying the compute function executes exactly once while all 10 calls return valid stats.

**Acceptance Scenarios**:

1. **Given** no stats cache exists for a (user, subject, version) key, **When** multiple concurrent requests arrive for that key, **Then** only one request executes the compute function; the others wait and read from cache after the first completes.
2. **Given** stats cache exists and is valid, **When** a request arrives, **Then** the fast path returns cached stats without acquiring any lock.
3. **Given** a per-key lock is held by a computing request, **When** a second request for the same key arrives, **Then** the second request waits for the lock, double-checks cache (which should now be populated), and returns the cached result without recomputing.
4. **Given** stats cache exists but content hash mismatches, **When** a request arrives, **Then** the per-key lock coalescing still applies — only one request recomputes for the new content hash.

---

### User Story 3 - All Existing Tests Continue to Pass (Priority: P1)

Both fixes are internal optimizations that must not change any observable API behavior. All existing test suites for hierarchy, stats, progress services, and progress endpoints must pass without modification (except adding new assertions for local cache invalidation).

**Why this priority**: Regression prevention is critical — these are performance optimizations, not feature changes.

**Independent Test**: Run the full test suites: `test_hierarchy_service.py`, `test_stats_service.py`, `test_progress_service.py`, `test_progress_endpoints.py`.

**Acceptance Scenarios**:

1. **Given** the in-process cache is added to HierarchyService, **When** the hierarchy test suite runs, **Then** all existing tests pass and the invalidation test additionally verifies local cache is cleared.
2. **Given** per-key lock coalescing is added to StatsService, **When** the stats and progress test suites run, **Then** all existing tests pass with no behavioral changes.

---

### Edge Cases

- What happens when a worker process has a stale local cache entry and pub/sub invalidation fails to reach it? The entry expires after 5 minutes (LOCAL_TTL), serving stale data for at most 5 minutes. This is acceptable for hierarchy data that changes only on admin content rebuilds.
- What happens when the compute function raises an exception inside the per-key lock? The lock is released (via `async with`), the exception propagates to the caller, and subsequent requests retry the computation.
- What happens with 100,000+ unique (user, subject, version) combinations creating per-key locks? At ~100 bytes per `asyncio.Lock`, 500k locks consume ~50MB — acceptable for the server's memory budget. Locks are cleaned up on worker restart.
- What happens when a worker restarts? Both in-process caches and per-key lock dicts are cleared. The first request after restart incurs a one-time parse cost (hierarchy) or compute cost (stats), then subsequent requests benefit from the caches.
- What happens when multiple uvicorn workers run? Each worker has its own independent in-process cache and lock dict. With 4 workers, hierarchy JSON is parsed at most 4 times (once per worker) instead of thousands per minute.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: HierarchyService MUST maintain an in-process cache of parsed SubjectHierarchy objects, keyed by subject_id, with a configurable TTL (default 5 minutes).
- **FR-002**: HierarchyService MUST check the in-process cache before checking Redis, returning the cached object immediately if present and not expired.
- **FR-003**: HierarchyService MUST populate the in-process cache whenever it parses a hierarchy from Redis or Frappe.
- **FR-004**: HierarchyService `invalidate()` MUST clear both the Redis key and the corresponding in-process cache entry.
- **FR-005**: HierarchyService `invalidate_all()` MUST clear all in-process cache entries in addition to scanning and deleting Redis keys.
- **FR-006**: StatsService `get_or_recompute()` MUST use per-key `asyncio.Lock` to ensure only one concurrent computation per (user, subject, version) key.
- **FR-007**: `get_or_recompute()` MUST implement double-check locking — after acquiring the per-key lock, it re-checks the cache before computing.
- **FR-008**: `get_or_recompute()` MUST use a fast path that returns cached stats without acquiring any lock when the cache is populated and valid.
- **FR-009**: The existing semaphore throttling inside `get_or_recompute()` MUST be preserved as system-wide backpressure, coexisting with the new per-key lock (per-key lock first, then semaphore).
- **FR-010**: All existing tests MUST continue to pass after these changes.

### Key Entities

- **In-Process Cache Entry**: A tuple of (parsed SubjectHierarchy object, expiration timestamp using monotonic clock), stored in a module-level dict in `hierarchy.py` (not instance-level, since `deps.py` creates new instances per request).
- **Per-Key Lock**: An `asyncio.Lock` instance stored in a module-level dict in `stats.py`, keyed by the stats Redis key string. Created on first access, reused for subsequent concurrent requests to the same key.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Repeated hierarchy lookups for the same subject within a 5-minute window complete in under 1ms (down from 50-100ms per-request JSON parse overhead).
- **SC-002**: Under 1,000 concurrent users with 10,000-lesson subjects, p50 response latency for progress endpoints drops below 50ms (down from 310ms observed in load testing).
- **SC-003**: When 50 concurrent requests trigger a stats cold-start for the same (user, subject, version) key, the compute function executes exactly once (not 50 times).
- **SC-004**: Memory overhead per worker process is under 50MB for both caches combined (5 subjects at ~2MB parsed model + lock dict).
- **SC-005**: All existing test suites pass with zero regressions: hierarchy service, stats service, progress service, and progress endpoint tests.
- **SC-006**: Cache invalidation (via `invalidate()` or `invalidate_all()`) clears both Redis and in-process state, ensuring content updates propagate within the local TTL window.

### Assumptions

- The system runs with a small, fixed number of uvicorn workers (typically 4). Each worker having its own independent in-process cache is acceptable — no cross-worker cache sharing is needed.
- Hierarchy data changes infrequently (only on admin content rebuilds), so a 5-minute local TTL with immediate invalidation on pub/sub is sufficient.
- The per-key lock dict growth is bounded by the number of active (user, subject, version) combinations in a single worker process. At 100k users x 5 subjects, worst case is ~500k entries (~50MB), which is acceptable.
- The existing `get_or_recompute()` semaphore pattern in StatsService is enhanced in-place with per-key locking. The semaphore remains as system-wide backpressure; the per-key lock prevents duplicate computation for the same cache key.
