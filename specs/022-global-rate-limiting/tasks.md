# Tasks: Global API Rate Limiting

**Input**: Design documents from `/specs/022-global-rate-limiting/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rate-limit-api.md, quickstart.md

**Tests**: Included (spec.md mandates test scenarios per user story)

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Configuration)

**Purpose**: Add rate limit settings to existing config infrastructure

- [X] T001 Add rate limit settings (`global_rate_limit`, `global_rate_limit_window`, `reviews_rate_limit`, `session_rate_limit`, `ws_max_connections_per_user`) to `Settings` class in `fastapi_app/core/config.py`
- [X] T002 Add test settings overrides for rate limit values in `fastapi_app/tests/conftest.py`: use low values for fast tests (`global_rate_limit=10`, `global_rate_limit_window=60`, `reviews_rate_limit=5`, `session_rate_limit=3`, `ws_max_connections_per_user=3`). Add `memora:global_rl:*` and `memora:rl:*` to `cleanup_keys` patterns

---

## Phase 2: Foundational (GlobalRateLimiter Service)

**Purpose**: Core rate limiting service that MUST be complete before middleware or dependency can use it

**CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Create `GlobalRateLimiter` service in `fastapi_app/services/global_rate_limit.py` — single-key Lua script (INCR + conditional EXPIRE), returns `(allowed: bool, count: int, ttl: int)`, fail-open on Redis errors with structlog warning

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Global Per-IP Rate Limit (Priority: P1) MVP

**Goal**: All API endpoints (except health checks and payment webhooks) protected by a global 100 req/min per-IP rate limit

**Independent Test**: Send `global_rate_limit + 1` requests from the same IP within 60 seconds to any endpoint. The last returns 429 with `Retry-After` header. (Test uses `global_rate_limit=10`, so 11th request triggers 429.)

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T004 [P] [US1] Test: Normal requests under limit pass through — send N requests (N < test `global_rate_limit` of 10), assert all return 200, assert `X-RateLimit-Remaining` header decrements correctly, in `fastapi_app/tests/test_global_rate_limit.py`
- [X] T005 [P] [US1] Test: 429 returned when limit exceeded — send `global_rate_limit + 1` requests (11 with test settings), assert last returns 429 with `{"error": "RATE_LIMITED", "retry_after": <int>}` body and `Retry-After` header, in `fastapi_app/tests/test_global_rate_limit.py`
- [X] T006 [P] [US1] Test: Health endpoints exempt — send requests to `/api/v1/health/live` and `/api/v1/health/ready`, assert no `X-RateLimit-Limit` header present, in `fastapi_app/tests/test_global_rate_limit.py`
- [X] T007 [P] [US1] Test: Payment webhook exempt — send request to `/api/v1/webhooks/payment`, assert no rate limit headers, in `fastapi_app/tests/test_global_rate_limit.py`
- [X] T008 [P] [US1] Test: Fail-open on Redis unavailable — mock Redis to raise `ConnectionError`, assert request passes through with no rate limit headers, in `fastapi_app/tests/test_global_rate_limit.py`

### Implementation for User Story 1

- [X] T009 [US1] Create `GlobalRateLimitMiddleware` in `fastapi_app/middleware/rate_limit.py` — extract client IP from `X-Forwarded-For` (first entry) or `request.client.host`, check exempt paths (`/api/v1/health/`, `/api/v1/webhooks/payment`), call `GlobalRateLimiter`, add `X-RateLimit-Limit`/`X-RateLimit-Remaining`/`X-RateLimit-Reset` headers to all non-exempt responses, return 429 JSON when exceeded
- [X] T010 [US1] Register `GlobalRateLimitMiddleware` in `fastapi_app/main.py` — add after `RequestIDMiddleware`, pass Redis pool and settings from `app.state`

**Checkpoint**: Global per-IP rate limiting active on all non-exempt endpoints

---

## Phase 4: User Story 2 — Per-Player Rate Limit on Write Endpoints (Priority: P2)

**Goal**: Write endpoints (reviews submit, session start/end) have tighter per-player rate limits using `player_id` from JWT

**Independent Test**: Using a valid JWT, send `reviews_rate_limit + 1` requests to `POST /reviews/{subject}/submit`. The last returns 429. (Test uses `reviews_rate_limit=5`, so 6th request triggers 429.)

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T011 [P] [US2] Test: Reviews submit allows up to `reviews_rate_limit` (5) requests per player then returns 429 — use `authed_client`, mock `ReviewService` to return success, send valid request bodies, send 6 `POST /api/v1/reviews/SUB-TEST/submit` requests, assert 6th returns 429, in `fastapi_app/tests/test_global_rate_limit.py`
- [X] T012 [P] [US2] Test: Session start allows up to `session_rate_limit` (3) requests per player then returns 429 — use `authed_client`, mock `GameSessionService` to return success, send valid request bodies, send 4 `POST /api/v1/sessions/start` requests, assert 4th returns 429, in `fastapi_app/tests/test_global_rate_limit.py`
- [X] T013 [P] [US2] Test: Session end allows up to `session_rate_limit` (3) requests per player then returns 429 — use `authed_client`, mock services to return success, send valid request bodies, send 4 `POST /api/v1/sessions/end` requests, assert 4th returns 429, in `fastapi_app/tests/test_global_rate_limit.py`
- [X] T014 [P] [US2] Test: Per-player limit is independent of global IP limit — different players from the same IP each get their own per-player counter, in `fastapi_app/tests/test_global_rate_limit.py`

### Implementation for User Story 2

- [X] T015 [US2] Add `require_rate_limit(scope: str)` dependency factory in `fastapi_app/api/deps.py` — returns async dependency that uses `GlobalRateLimiter` with key `memora:rl:{scope}:{player_id}`, reads limit from settings, raises `RateLimitExceeded` on exceeded (handled by exception handler in `main.py`)
- [X] T016 [P] [US2] Add `Depends(require_rate_limit("reviews"))` to `submit_reviews` endpoint in `fastapi_app/api/v1/endpoints/reviews.py`
- [X] T017 [P] [US2] Add `Depends(require_rate_limit("session_start"))` to `start_session` and `Depends(require_rate_limit("session_end"))` to `end_session` in `fastapi_app/api/v1/endpoints/sessions.py`

**Checkpoint**: Per-player write limits active on reviews and session endpoints

---

## Phase 5: User Story 3 — WebSocket Connection Limiting (Priority: P3)

**Goal**: No player can open more than 5 concurrent WebSocket connections

**Independent Test**: Open `ws_max_connections_per_user + 1` connections with the same player JWT. The last is rejected with close code 4029. (Test uses `ws_max_connections_per_user=3`, so 4th connection is rejected.)

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T018 [P] [US3] Test: Up to `ws_max_connections_per_user` (3) WebSocket connections accepted for same player — use mock WebSockets with `ConnectionManager(max_connections_per_user=3)`, verify all 3 accepted, in `fastapi_app/tests/test_global_rate_limit.py`
- [X] T019 [P] [US3] Test: 4th WebSocket connection rejected with close code 4029 and reason "Too many connections" — verify `websocket.close()` called before `websocket.accept()`, in `fastapi_app/tests/test_global_rate_limit.py`

### Implementation for User Story 3

- [X] T020 [US3] Add `max_connections_per_user` parameter (default from settings) to `ConnectionManager.__init__()` and enforce limit check in `connect()` — reject with `websocket.close(code=4029, reason="Too many connections")` before `websocket.accept()` if limit reached, in `fastapi_app/core/ws_manager.py`
- [X] T021 [US3] Pass `settings.ws_max_connections_per_user` when creating `ConnectionManager` in `fastapi_app/main.py` lifespan

**Checkpoint**: All three rate limiting layers active and independently testable

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation and cleanup across all stories

- [X] T022 Run existing test suite (`python -m pytest fastapi_app/tests/ -v`) to verify zero regressions (SC-007)
- [X] T023 Run quickstart.md validation — restart FastAPI, verify health endpoint has no rate limit headers, verify non-exempt endpoint has `X-RateLimit-*` headers
- [X] T024 Verify all rate limit Redis keys have TTLs (NFR-002) — inspect `GlobalRateLimiter` and `require_rate_limit` ensure EXPIRE is always set
- [X] T025 [P] Benchmark rate limit middleware latency (SC-006) — send 100 requests via `app_client`, measure per-request time, assert p99 < 2ms. Can use `time.perf_counter()` around each request in `fastapi_app/tests/test_global_rate_limit.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (needs settings)
- **US1 (Phase 3)**: Depends on Phase 2 (`GlobalRateLimiter` service)
- **US2 (Phase 4)**: Depends on Phase 2 (`GlobalRateLimiter` service) — can run in parallel with US1
- **US3 (Phase 5)**: No dependency on Phases 2-4 (in-memory, no Redis) — can run in parallel with US1/US2
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Needs `GlobalRateLimiter` from Phase 2 — no dependency on other stories
- **US2 (P2)**: Needs `GlobalRateLimiter` from Phase 2 — no dependency on US1 or US3
- **US3 (P3)**: Fully independent — in-memory counter, no shared service

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Service/dependency before endpoint wiring
- Core implementation before integration

### Parallel Opportunities

- T004-T008: All US1 tests can be written in parallel (same file, different test functions)
- T011-T014: All US2 tests can be written in parallel
- T018-T019: All US3 tests can be written in parallel
- T016-T017: Endpoint wiring for reviews and sessions can be done in parallel (different files)
- US1, US2, US3 phases can proceed in parallel after Phase 2 completes

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests together (they're in the same file but independent functions):
Task: "Test normal requests under limit" in test_global_rate_limit.py
Task: "Test 429 on limit exceeded" in test_global_rate_limit.py
Task: "Test health exempt" in test_global_rate_limit.py
Task: "Test fail-open" in test_global_rate_limit.py

# After tests fail, implement:
Task: "Create GlobalRateLimitMiddleware" in middleware/rate_limit.py
Task: "Register middleware" in main.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Settings
2. Complete Phase 2: `GlobalRateLimiter` service
3. Complete Phase 3: Global per-IP middleware + tests
4. **STOP AND VALIDATE**: `global_rate_limit + 1` request from same IP returns 429
5. Deploy if ready — this alone covers the highest-risk gap

### Incremental Delivery

1. Setup + Foundation -> Rate limiter service ready
2. Add US1 (Global IP limit) -> Test independently -> Deploy (MVP!)
3. Add US2 (Per-player write limits) -> Test independently -> Deploy
4. Add US3 (WebSocket connection limit) -> Test independently -> Deploy
5. Each story adds defense-in-depth without breaking previous layers

### Key Technical Notes

- **Redis port**: Must use `redis://127.0.0.1:13000` (same as Frappe)
- **Key prefix isolation**: `memora:global_rl:` (global), `memora:rl:` (per-player) — distinct from existing `memora:ratelimit:` (login)
- **Lua script**: Same INCR + conditional EXPIRE pattern as existing `RateLimiter` in `services/rate_limit.py`
- **Fail-open**: All Redis errors must allow request through (FR-008)
- **Performance**: Single Redis round-trip per check via Lua script (<2ms target)
