# Tasks: 100k Concurrency Scaling Optimizations

**Input**: Design documents from `/specs/029-concurrency-scaling/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Constitution VIII requires test coverage for new code paths. Targeted tests added for bitmap decode, fail-closed rate limiter, and parallel broadcast. FR-014 additionally requires all existing tests continue to pass.

**Organization**: Tasks grouped by user story. US2 (Settings) is foundational since all other stories consume settings fields.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Foundational — Configurable Scaling Settings (US2, Priority: P1)

**Goal**: Add all 6 scaling settings fields to the Settings class so every subsequent story can read its configuration from environment variables with development-safe defaults.

**Independent Test**: Start the app with no new env vars — defaults apply (pool=20, sequential broadcast, fail-open). Set `REDIS_MAX_CONNECTIONS=200` — verify it's reflected in startup log.

**Why foundational**: Every other user story consumes at least one of these settings fields. No story can be implemented until Settings is updated.

- [x] T001 [US2] Add 6 scaling settings fields with pydantic validators to Settings class in `fastapi_app/core/config.py`. Include cross-field validator: `frappe_max_keepalive` MUST be ≤ `frappe_max_connections` (raise `ValueError` otherwise)
- [x] T002 [P] [US2] Update `.env.example` with documentation for all new scaling environment variables
- [x] T003 [P] [US2] Create `production.env.example` with recommended production values for ALL scaling parameters — both new (REDIS_MAX_CONNECTIONS, WS_BROADCAST_CONCURRENCY, RATE_LIMIT_FAIL_OPEN, FRAPPE_TIMEOUT, FRAPPE_MAX_CONNECTIONS, FRAPPE_MAX_KEEPALIVE) and pre-existing (GLOBAL_RATE_LIMIT, GLOBAL_RATE_LIMIT_WINDOW, REVIEWS_RATE_LIMIT)

**Checkpoint**: Settings class has all 6 new fields. `get_settings()` returns correct defaults. Environment variable overrides work.

---

## Phase 2: US1 — Redis Connection Pool Scaling (Priority: P1)

**Goal**: Make the Redis connection pool size configurable from settings and log the configured size at startup for operational visibility.

**Independent Test**: Set `REDIS_MAX_CONNECTIONS=200`, restart service, verify startup log shows `pool_size=200`. Unset the var, verify default `20` is logged.

- [x] T004 [US1] Make Redis pool size configurable from `settings.redis_max_connections` and log pool size at startup in `fastapi_app/core/redis.py`

**Checkpoint**: Redis pool respects configured size. Startup log includes pool size.

---

## Phase 3: US3 — Single-Fetch Bitmap Decode (Priority: P1)

**Goal**: Replace the N-GETBIT pipeline in `get_completed_bits()` with a single `GET` + client-side latin-1 bitmap decode, reducing Redis command volume by ~99.8% for large subjects.

**Independent Test**: Request progress for a subject with 500 lessons — verify a single Redis GET is issued (not 500 GETBIT commands). Verify correct bits returned for empty, sparse, and full bitmaps.

- [x] T005 [US3] Replace N-GETBIT pipeline with single GET + latin-1 client-side bitmap decode in `get_completed_bits()` in `fastapi_app/services/progress.py`

**Checkpoint**: `get_completed_bits()` uses 1 Redis command regardless of lesson count. Edge cases handled (empty key, bit_range=0, sparse bitmap, all byte values 0-255).

---

## Phase 4: US4 + US5 — WebSocket Scaling (Priority: P2)

**Goal**: Replace the global WebSocket connection lock with per-user locks (US5) and add configurable parallel broadcast (US4), eliminating cross-user contention and cascading delay from slow clients.

**Independent Test (US5)**: Connect/disconnect operations for user A and user B execute without mutual blocking. Verify lock cleanup when last connection removed.

**Independent Test (US4)**: With `WS_BROADCAST_CONCURRENCY=50`, verify `send_to_plan()` dispatches sends concurrently. With `=0`, verify sequential behavior.

- [x] T006 [US5] Replace global `_lock` with per-user `_user_locks` dict, add `_lock_guard`, implement `_get_user_lock()`, and add lock cleanup on last disconnect in `fastapi_app/core/ws_manager.py`
- [x] T007 [US4] Add configurable parallel broadcast using `asyncio.gather()` with semaphore to `send_to_user()` and `send_to_plan()` in `fastapi_app/core/ws_manager.py`
- [x] T008 [US4] Pass `broadcast_concurrency` from `settings.ws_broadcast_concurrency` to ConnectionManager constructor in `fastapi_app/main.py`

**Checkpoint**: Per-user locks eliminate global contention. Parallel broadcast dispatches concurrently when configured. Sequential mode preserved when `ws_broadcast_concurrency=0`.

---

## Phase 5: US6 — Parallel Progress Summary (Priority: P2)

**Goal**: Parallelize per-subject lookups in the progress summary endpoint using `asyncio.gather()`, reducing wall-clock time from ~10ms × N subjects to ~10ms total.

**Independent Test**: Request progress summary for a student enrolled in 8 subjects — verify all 8 fetched concurrently (total time ~10ms, not ~80ms). Verify one subject failure doesn't prevent others from returning.

- [x] T009 [US6] Wrap per-subject work in async helper and parallelize with `asyncio.gather(*tasks, return_exceptions=True)` in `fastapi_app/api/v1/endpoints/progress.py`

**Checkpoint**: Progress summary for 8 subjects completes in ~single-subject latency. Individual subject failures logged as warnings; other subjects still returned.

---

## Phase 6: US7 — Rate Limiter Fail Behavior (Priority: P3)

**Goal**: Make the rate limiter's behavior during Redis outages configurable — fail-open (development default, current behavior) or fail-closed (production, returns 503).

**Independent Test**: Simulate Redis unavailability with `fail_open=True` — request passes through with warning log. With `fail_open=False` — returns 503 with `Retry-After: 5` header.

- [x] T010 [US7] Add `fail_open` parameter to `GlobalRateLimitMiddleware.__init__()` and implement fail-closed 503 response path in `fastapi_app/middleware/rate_limit.py`
- [x] T011 [US7] Pass `settings.rate_limit_fail_open` to middleware registration in `fastapi_app/main.py`

**Checkpoint**: Fail-open preserves current behavior. Fail-closed returns 503 + Retry-After on Redis error.

---

## Phase 7: US8 — Upstream API Client Scaling (Priority: P3)

**Goal**: Make FrappeClient timeout and connection pool limits configurable from settings, enabling production tuning for cache-miss hydration storms.

**Independent Test**: Set `FRAPPE_TIMEOUT=10.0` and `FRAPPE_MAX_CONNECTIONS=200`, restart service, verify the HTTP client uses those values instead of hardcoded defaults.

- [x] T012 [US8] Replace hardcoded timeout, max_connections, and max_keepalive_connections with `settings.frappe_timeout`, `settings.frappe_max_connections`, and `settings.frappe_max_keepalive` in `fastapi_app/services/frappe_client.py`

**Checkpoint**: FrappeClient respects all 3 configurable values. Defaults match previous hardcoded values (30.0, 100, 20).

---

## Phase 8: Targeted Tests for New Code Paths (Constitution VIII)

**Goal**: Test new production code paths that existing tests don't exercise. Uses pytest + real Redis (no mocking), consistent with project testing conventions.

**Why needed**: Constitution VIII mandates test coverage for all production code. Three new code paths — bitmap decode (latin-1 edge cases), fail-closed rate limiter (503 response), and parallel broadcast — are not exercised by existing tests (which run with default/unchanged settings).

- [x] T013 [US3] Add pytest tests for `get_completed_bits()` single-fetch bitmap decode in `fastapi_app/tests/test_bitmap_decode.py`: (1) empty/missing key → empty set, (2) bit_range=0 → empty set, (3) sparse bitmap (5 of 500 bits set) → correct 5-element set, (4) full byte coverage (all values 0x00-0xFF present) → lossless round-trip, (5) partial bitmap (bit_range exceeds stored bytes) → out-of-range bits treated as 0
- [x] T014 [P] [US7] Add pytest tests for fail-closed rate limiter in `fastapi_app/tests/test_rate_limit_fail_closed.py`: (1) with `fail_open=False` and mocked Redis error → 503 status + `Retry-After: 5` header, (2) with `fail_open=True` and mocked Redis error → request passes through (existing behavior), (3) normal operation (Redis available) → 200 with rate limit headers
- [x] T015 [P] [US4] Add pytest tests for parallel broadcast in `fastapi_app/tests/test_ws_broadcast.py`: (1) with `broadcast_concurrency=0` → sends are sequential, (2) with `broadcast_concurrency=50` → sends dispatched via gather with semaphore, (3) slow connection doesn't block other connections when parallel enabled

**Checkpoint**: All 3 test files pass. New code paths have explicit coverage. Existing tests unaffected.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Verify no regressions and validate the complete feature end-to-end.

- [ ] T016 Verify all existing tests pass with no regressions
- [ ] T017 Run quickstart.md validation steps (health check, startup logs, rate limit headers)
- [ ] T018 Restart FastAPI server and verify all changes are active

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Foundational/US2)**: No dependencies — start immediately. **BLOCKS all other phases.**
- **Phases 2–7 (US1, US3, US4+US5, US6, US7, US8)**: All depend on Phase 1 completion. Can then proceed **in parallel** (different files).
- **Phase 8 (Tests)**: Each test task depends on its corresponding implementation phase: T013 depends on Phase 3 (T005), T014 depends on Phase 6 (T010+T011), T015 depends on Phase 4 (T007+T008). Tests for different stories can run **in parallel**.
- **Phase 9 (Polish)**: Depends on all story phases AND test phase being complete.

### Cross-Phase File Conflicts

- **`fastapi_app/main.py`**: Modified by T008 (US4) and T011 (US7). These add different lines — safe to run sequentially in any order, but NOT in parallel.
- **`fastapi_app/core/ws_manager.py`**: Modified by T006 (US5) and T007 (US4). T006 MUST complete before T007 (lock changes before broadcast changes).
- All other files are modified by exactly one task — no conflicts.
- **Test files (Phase 8)**: T013, T014, T015 create new files — no conflicts with each other or implementation tasks.

### User Story Dependencies

| Story | Depends On | Files Modified |
|-------|-----------|----------------|
| US2 (Settings) | None | `config.py`, `.env.example`, `production.env.example` |
| US1 (Pool) | US2 | `redis.py` |
| US3 (Bitmap) | US2 | `services/progress.py` |
| US4 (WS Broadcast) | US2, US5 | `ws_manager.py`, `main.py` |
| US5 (WS Locks) | US2 | `ws_manager.py` |
| US6 (Summary) | US2 | `endpoints/progress.py` |
| US7 (Rate Limiter) | US2 | `rate_limit.py`, `main.py` |
| US8 (Frappe Client) | US2 | `frappe_client.py` |

### Within-Phase Order

- **Phase 4 (US4+US5)**: T006 → T007 → T008 (lock changes → broadcast changes → main.py wiring)
- All other phases: tasks can run in listed order or parallel where marked [P]

### Parallel Opportunities

After Phase 1 completes, the following can run **simultaneously**:

```
Phase 2 (US1: redis.py)     ─┐
Phase 3 (US3: progress.py)  ─┤
Phase 4 (US4+US5: ws_manager)├─→ Phase 8 (Tests) ─→ Phase 9 (Polish)
Phase 5 (US6: endpoints/)   ─┤
Phase 6 (US7: rate_limit.py)─┤
Phase 7 (US8: frappe_client) ┘
```

---

## Parallel Example: After Phase 1

```bash
# All these can launch simultaneously (different files):
Task: "T004 [US1] Redis pool size in redis.py"
Task: "T005 [US3] Bitmap decode in services/progress.py"
Task: "T006 [US5] Per-user locks in ws_manager.py"
Task: "T009 [US6] Parallel summary in endpoints/progress.py"
Task: "T010 [US7] Fail behavior in rate_limit.py"
Task: "T012 [US8] Frappe client in frappe_client.py"
```

---

## Implementation Strategy

### MVP First (P1 Stories Only)

1. Complete Phase 1: Settings (US2) — **BLOCKS everything**
2. Complete Phase 2: Redis Pool (US1)
3. Complete Phase 3: Bitmap Decode (US3)
4. **STOP and VALIDATE**: Run existing tests, verify health check, check startup logs
5. Deploy/demo — system handles larger pool + 99.8% fewer Redis commands

### Incremental Delivery

1. Phase 1 (US2) → Settings ready
2. Phases 2+3 (US1+US3) → Pool + bitmap decode → **MVP deployed**
3. Phase 4 (US4+US5) → WebSocket scaling → Test with concurrent connections
4. Phase 5 (US6) → Parallel summary → Test dashboard latency
5. Phases 6+7 (US7+US8) → Rate limiter + Frappe client → Full production readiness
6. Phase 8 → Targeted tests for new code paths
7. Phase 9 → Polish → Final validation

### Rollback

Remove production environment variables → system reverts to development defaults. No code changes needed (FR-013).

---

## Notes

- 3 new test files (Phase 8) cover new code paths per Constitution VIII; FR-014 additionally requires existing tests pass
- FR-002 (configurable rate limits) is already implemented — no task needed
- No new Redis keys — all changes to existing patterns
- No database/DocType changes — purely in-memory config and service logic
- `decode_responses=True` constraint addressed by latin-1 encoding in US3
- All defaults match current hardcoded values — zero-config dev continues unchanged
