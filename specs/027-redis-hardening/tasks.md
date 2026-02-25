# Tasks: Redis Hardening

**Input**: Design documents from `/specs/027-redis-hardening/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (health-redis.yaml, monitoring-task.yaml, ttl-policy.yaml)

**Tests**: Included per Constitution Principle VIII (Test-First Coverage). New production code has corresponding test tasks written before implementation. Tests for `get_memora_redis()` fallback, health endpoint contract, monitoring task thresholds, and leaderboard cleanup retention logic.

**Organization**: Tasks grouped by user story (5 stories). US1 (Data Isolation) and US2 (AOF Persistence) are both P1 — US2's infrastructure is fully set up alongside US1 in Phase 1 Setup, so its phase is verification-only.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Infrastructure)

**Purpose**: Set up the dedicated Redis instance with AOF persistence, systemd service, and update application configuration files. Covers infrastructure for both US1 (port isolation) and US2 (AOF crash recovery).

- [x] T001 Set up dedicated Redis instance per quickstart.md — create config `/etc/redis/redis-memora.conf` (port 13001, `appendonly yes`, `appendfsync everysec`, `maxmemory 128mb`, `maxmemory-policy volatile-ttl`, `aof-use-rdb-preamble yes`), create systemd service `/etc/systemd/system/redis-memora.service` (Restart=always, LimitNOFILE=65535), create data dir `/var/lib/redis-memora/` owned by redis:redis, then `systemctl daemon-reload && systemctl enable --now redis-memora`
- [x] T002 [P] Update REDIS_URL from `redis://127.0.0.1:13000` to `redis://127.0.0.1:13001` in `/home/corex/aurevia-bench/apps/memora_admin/.env`
- [x] T003 [P] Add `redis_memora` key to Frappe site config via `bench --site x.conanacademy.com set-config redis_memora "redis://127.0.0.1:13001"`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create shared utilities, constants, and models required by all user stories. Includes tests per Constitution Principle VIII.

**CRITICAL**: No user story work can begin until this phase is complete.

### Tests (write first, verify they fail)

- [x] T004 Write test for `get_memora_redis()` fallback behavior in `memora_admin/tests/test_redis_connection.py` — test that function returns a `redis.Redis` client with `decode_responses=True`; test that it reads `redis_memora` from `frappe.conf` when available; test that it falls back to `redis_cache` when `redis_memora` is not configured; use `unittest.mock.patch` on `frappe.conf`

### Implementation

- [x] T005 Create `memora_admin/utils/__init__.py` (empty) and `get_memora_redis()` utility function in `memora_admin/utils/redis_connection.py` — reads `frappe.conf.get("redis_memora", frappe.conf.redis_cache)`, returns synchronous `redis.Redis` client with `decode_responses=True`; include docstring noting fallback behavior for backward compatibility
- [x] T006 [P] Add TTL constants to `fastapi_app/core/redis_keys.py` — `WALLET_KEY_TTL = 172800` (48h), `PROGRESS_KEY_TTL = 172800` (48h), `ACCESS_KEY_TTL = 86400` (24h), `PLAN_FREE_SUBJECTS_TTL = 43200` (12h); add docstring block explaining TTL policy and which keys are protected (no TTL); add cross-reference comment: `# NOTE: WALLET_KEY_TTL is duplicated as literal 172800 in Lua scripts: wallet.py STREAK_UPDATE_SCRIPT, game_session.py SESSION_COMPLETE_SCRIPT. Update both locations if value changes.` and similar for `PROGRESS_KEY_TTL`
- [x] T007 [P] Create `RedisHealthReport` Pydantic model in `fastapi_app/models/health.py` — fields: `status: Literal["healthy", "degraded", "unhealthy"]`, `used_memory_mb: float`, `max_memory_mb: float`, `memory_usage_percent: float`, `interaction_buffer_length: int`, `dirty_wallets_count: int`, `dirty_progress_count: int`, `connected_clients: int`, `aof_enabled: bool`, `uptime_seconds: int`, `total_keys: int`

**Checkpoint**: Foundation ready — `get_memora_redis()` tested and passing, TTL constants with Lua cross-references, and health model available for all stories.

---

## Phase 3: User Story 1 — Data Isolation from Frappe Cache Flushes (Priority: P1) MVP

**Goal**: All Memora Redis operations (FastAPI + Frappe side) target port 13001. `bench clear-cache` on Frappe's Redis (13000) has zero impact on game data.

**Independent Test**: Write canary key to 13001 (`redis-cli -p 13001 SET memora:test:canary alive`), run `bench clear-cache`, verify canary survives (`redis-cli -p 13001 GET memora:test:canary` returns "alive"). Hit any game API endpoint and verify it reads/writes to 13001.

### Frappe Background Tasks (get_redis -> get_memora_redis)

- [x] T008 [P] [US1] Update `memora_admin/tasks/sync.py` — replace local `get_redis()` function with import of `get_memora_redis` from `memora_admin.utils.redis_connection`; update all call sites within the file
- [x] T009 [P] [US1] Update `memora_admin/tasks/leaderboard_reset.py` — replace local `get_redis()` function with import of `get_memora_redis` from `memora_admin.utils.redis_connection`
- [x] T010 [P] [US1] Update `memora_admin/tasks/session_cleanup.py` — replace Redis connection logic with import of `get_memora_redis` from `memora_admin.utils.redis_connection`
- [x] T011 [P] [US1] Update `memora_admin/tasks/streak_reset.py` — replace Redis connection logic with import of `get_memora_redis` from `memora_admin.utils.redis_connection`

### Frappe API Endpoints (frappe.conf.redis_cache -> get_memora_redis)

- [x] T012 [P] [US1] Update `memora_admin/api/profile.py` — replace `redis.from_url(frappe.conf.redis_cache)` with `get_memora_redis()` from `memora_admin.utils.redis_connection`
- [x] T013 [P] [US1] Update `memora_admin/api/reviews.py` — replace `redis.from_url(frappe.conf.redis_cache)` with `get_memora_redis()` from `memora_admin.utils.redis_connection`
- [x] T014 [P] [US1] Update `memora_admin/api/utils.py` — replace `redis.from_url(frappe.conf.redis_cache)` with `get_memora_redis()` from `memora_admin.utils.redis_connection`

### Test Configuration

- [x] T015 [P] [US1] Update `fastapi_app/tests/conftest.py` — change hardcoded Redis URL from `redis://127.0.0.1:13000` to `redis://127.0.0.1:13001`
- [x] T016 [P] [US1] Update `memora_admin/tests/sync_test_base.py` — replace `frappe.conf.redis_cache` Redis connection with `get_memora_redis()` pattern from `memora_admin.utils.redis_connection`

**Checkpoint**: All Memora services use port 13001. `bench clear-cache` on 13000 is safe. All 9 Frappe-side files + 1 FastAPI test config migrated.

---

## Phase 4: User Story 2 — Crash Recovery via AOF Persistence (Priority: P1)

**Goal**: Redis data survives process crashes and server restarts via AOF persistence with max 1-second data loss window.

**Independent Test**: Write test data to port 13001, restart the `redis-memora` service, verify data survives the restart.

> **Note**: AOF persistence is fully configured in Phase 1 Setup (`redis-memora.conf` includes `appendonly yes`, `appendfsync everysec`, `aof-use-rdb-preamble yes`). This phase is verification-only — no code changes required.

- [x] T017 [US2] Verify AOF persistence — run `redis-cli -p 13001 INFO persistence` and confirm `aof_enabled:1`; write test key `SET memora:test:aof-check 1`, restart redis-memora service (`systemctl restart redis-memora`), confirm key survived via `GET memora:test:aof-check`; clean up test key

**Checkpoint**: AOF persistence verified. Data survives Redis restart with max 1s loss window.

---

## Phase 5: User Story 3 — Bounded Memory via Key TTLs (Priority: P2)

**Goal**: All cacheable keys have TTLs to bound memory growth proportional to active (not total) users. Protected keys (dirty sets, buffer, alltime leaderboard) never get TTL. `volatile-ttl` eviction policy ensures protected keys survive memory pressure.

**Independent Test**: Award XP to a player, then run `redis-cli -p 13001 TTL memora:wallet:{player}` — should return ~172800. Check `TTL memora:dirty:wallets` — should return -1 (no TTL). Run leaderboard cleanup and verify old keys deleted.

### FastAPI Services (TTL on writes)

- [x] T018 [P] [US3] Add EXPIRE to wallet writes in `fastapi_app/services/wallet.py` — add `pipe.expire(wallet_key, WALLET_KEY_TTL)` in `award_xp` pipeline after HINCRBY; add EXPIRE in `ensure_hydrated` after HSET; add `redis.call('EXPIRE', KEYS[1], 172800)` to `STREAK_UPDATE_SCRIPT` Lua script after wallet writes (literal required — Lua cannot import Python constants; cross-referenced in `redis_keys.py` per T006); import `WALLET_KEY_TTL` from `fastapi_app.core.redis_keys`
- [x] T019 [P] [US3] Add EXPIRE to progress writes in `fastapi_app/services/progress.py` — add `pipe.expire(progress_key, PROGRESS_KEY_TTL)` after SETBIT in `complete_lesson`; add EXPIRE in `ensure_hydrated` after SETRANGE; import `PROGRESS_KEY_TTL` from `fastapi_app.core.redis_keys`
- [x] T020 [P] [US3] Add EXPIRE to access hydration in `fastapi_app/services/access.py` — add `pipe.expire(access_key, ACCESS_KEY_TTL)` after SADD in `ensure_hydrated`; import `ACCESS_KEY_TTL` from `fastapi_app.core.redis_keys`
- [x] T021 [US3] Add EXPIRE in `SESSION_COMPLETE_SCRIPT` Lua in `fastapi_app/services/game_session.py` — add `redis.call('EXPIRE', KEYS[2], 172800)` after the SETBIT call to refresh progress key TTL atomically within the Lua script (literal required — cross-referenced in `redis_keys.py` per T006)

### Frappe Event Handlers (TTL on writes)

- [x] T022 [P] [US3] Add EXPIRE to grant operations in `memora_admin/events/access_sync.py` — add `r.expire(access_key, ACCESS_KEY_TTL)` after SADD in `on_subscription_change`; add `r.expire(plan_key, PLAN_FREE_SUBJECTS_TTL)` after plan free subjects rebuild; import TTL constants from `fastapi_app.core.redis_keys`
- [x] T023 [P] [US3] Add EXPIRE to plan sync in `memora_admin/tasks/plan_sync.py` — add `r.expire(plan_key, PLAN_FREE_SUBJECTS_TTL)` after `sync_all_plan_subjects_to_redis` rebuilds plan free subject sets; import `PLAN_FREE_SUBJECTS_TTL` from `fastapi_app.core.redis_keys`

### Leaderboard Cleanup Task

#### Test (write first, verify it fails)

- [x] T024 [US3] Write test for leaderboard cleanup in `memora_admin/tests/test_leaderboard_cleanup.py` — test date extraction from key names (daily keys use `YYYY-MM-DD` format, weekly keys use `YYYY-Www` ISO week format — verify by reading `fastapi_app/services/leaderboard.py` key format); test that keys older than retention threshold (30d daily, 90d weekly) are deleted; test that `memora:lb:alltime*` keys are never deleted; test that empty SCAN (no keys to delete) completes without error; use real Redis on port 13001 with test-prefixed keys

#### Implementation

- [x] T025 [US3] Create leaderboard cleanup task in `memora_admin/tasks/leaderboard_cleanup.py` — `cleanup_old_leaderboards()` function using `get_memora_redis()`; SCAN for `memora:lb:daily:*` (delete >30d, date format `YYYY-MM-DD`), `memora:lb:weekly:*` (delete >90d, date format `YYYY-Www`), `memora:lb:archive:daily:*` (delete >90d), `memora:lb:archive:weekly:*` (delete >90d); extract date from key name via regex, pipeline DEL in batches; never touch `memora:lb:alltime*`; log count of deleted keys per category
- [x] T026 [US3] Register leaderboard cleanup in `memora_admin/hooks.py` — add `cleanup_old_leaderboards` as daily scheduled job at `03:00` (cron: `0 3 * * *`)

**Checkpoint**: `redis-cli TTL memora:wallet:{player}` returns ~172800. `TTL memora:dirty:wallets` returns -1. Leaderboard cleanup tested and registered.

---

## Phase 6: User Story 4 — Memory Monitoring and Buffer Backlog Detection (Priority: P2)

**Goal**: Operators have real-time Redis health visibility via API endpoint and periodic threshold-based alerting logs. Interaction buffer flush adapts batch size to buffer depth.

**Independent Test**: `curl http://127.0.0.1:8002/api/v1/health/redis` returns valid JSON with all fields per contract. Frappe error log shows periodic `redis_monitor` entries every 5 minutes.

### Tests (write first, verify they fail)

- [x] T027 [P] [US4] Write contract test for `GET /api/v1/health/redis` in `fastapi_app/tests/test_health_redis.py` — test healthy response (200, all metrics within thresholds); test degraded response when buffer is large (mock LLEN >10000); test unhealthy/503 response when Redis is unreachable (mock connection error); test that endpoint requires no authentication; test response matches `RedisHealthReport` schema (all 11 fields present); use `httpx.AsyncClient` with FastAPI test app
- [x] T028 [P] [US4] Write test for monitoring task in `memora_admin/tests/test_redis_monitor.py` — test that `monitor_redis_health()` logs INFO with all metrics on every run; test WARNING log when memory exceeds 80% (mock Redis INFO response); test WARNING log when dirty set count exceeds 1000 (mock SCARD response); test CRITICAL log when buffer exceeds 10000 (mock LLEN response); use `unittest.mock.patch` for Redis client and `frappe.logger()`

### Health Endpoint

- [x] T029 [US4] Add `GET /api/v1/health/redis` endpoint in `fastapi_app/api/v1/endpoints/health.py` — no-auth endpoint returning `RedisHealthReport`; query `INFO memory` (used_memory, maxmemory), `LLEN` via `interaction_buffer_key()`, `SCARD` via `dirty_wallets_key()` and `dirty_progress_key()` from `fastapi_app.core.redis_keys`, `INFO clients` (connected_clients), `INFO persistence` (aof_enabled), `INFO server` (uptime_in_seconds), `DBSIZE` (total_keys); determine status: healthy (memory <80%, buffer <10000, dirty <1000), degraded (memory 80-95% OR buffer 10000-50000 OR dirty >1000), unhealthy (memory >95% OR buffer >50000); return 503 with unhealthy report if Redis unreachable

### Monitoring Task

- [x] T030 [US4] Create monitoring task in `memora_admin/tasks/redis_monitor.py` — `monitor_redis_health()` function using `get_memora_redis()`; collect: used_memory_mb, max_memory_mb, memory_pct, buffer_len (LLEN), dirty_wallets (SCARD), dirty_progress (SCARD), total_keys (DBSIZE); always log INFO with all metrics; log WARNING if memory_pct >80; log WARNING if dirty_wallets >1000 OR dirty_progress >1000; log CRITICAL if buffer_len >10000
- [x] T031 [US4] Register monitoring task in `memora_admin/hooks.py` — add `monitor_redis_health` as every-5-minutes scheduled job (cron: `*/5 * * * *`)

### Dynamic Batch Sizing

- [x] T032 [US4] Add dynamic batch sizing to `flush_interaction_buffer` in `memora_admin/tasks/sync.py` — check buffer length via LLEN at start; use `batch_size=1000` by default (reduced from 5000 — intentional: 1000/min is sufficient under normal load; dynamic scaling to 5000 at >50k compensates under backlog), scale to `batch_size=5000` when buffer >50000; add `frappe.logger().critical(f"redis_buffer_backlog buffer_len={buffer_len}")` when buffer >10000

**Checkpoint**: Health endpoint contract test passes. Monitoring task test passes. `curl http://127.0.0.1:8002/api/v1/health/redis` returns valid JSON. Monitoring task appears in Frappe scheduler. Dynamic batch sizing active.

---

## Phase 7: User Story 5 — Production Deployment Guide (Priority: P3)

**Goal**: Comprehensive step-by-step documentation enabling a sysadmin to replicate the dual-Redis setup on a fresh production server without prior codebase knowledge.

**Independent Test**: A person unfamiliar with the codebase can follow the guide on a fresh server and verify all services work correctly.

- [x] T033 [P] [US5] Add "Redis Hardening Deployment Guide" section to end of `README.md` — include: Redis config file setup, systemd service creation, directory permissions, AOF settings, application config updates (.env + site_config.json), flush-then-switch migration steps, verification commands, monitoring setup; include dev vs production config comparison table (maxmemory, tcp-backlog, timeout)
- [x] T034 [P] [US5] Update `CLAUDE.md` Redis architecture documentation — update "Redis Resilience" section header and table to document dual-Redis architecture (Frappe on 13000, Memora on 13001); document `get_memora_redis()` utility in "FastAPI Patterns" section; update Redis port references throughout; update "Redis Keys Reference" table with TTL column

**Checkpoint**: README.md has complete deployment guide. CLAUDE.md accurately reflects dual-Redis architecture.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Final verification, cleanup, and configuration sync.

- [x] T035 Run full FastAPI test suite via `python -m pytest fastapi_app/tests/ -v` and verify all tests pass on port 13001 (including new health endpoint tests)
- [x] T036 Run canary isolation test — `redis-cli -p 13001 SET memora:test:canary alive`, then `bench clear-cache`, then verify `redis-cli -p 13001 GET memora:test:canary` returns "alive"
- [x] T037 Restart FastAPI (`pkill -f "uvicorn fastapi_app.main:app"`) and Frappe (`bench restart`), then run quickstart.md end-to-end verification steps
- [x] T038 [P] Update `.env.example` and `.env.notes` to reflect `redis://127.0.0.1:13001` as default REDIS_URL

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (Redis running on 13001)
- **US1 (Phase 3)**: Depends on Phase 2 (`get_memora_redis()` utility)
- **US2 (Phase 4)**: Depends on Phase 1 only (infrastructure verification)
- **US3 (Phase 5)**: Depends on Phase 2 (TTL constants) — can run in parallel with US1
- **US4 (Phase 6)**: Depends on Phase 2 (RedisHealthReport model, `get_memora_redis()`) — can run in parallel with US1/US3
- **US5 (Phase 7)**: Depends on US1–US4 completion (documents final architecture)
- **Polish (Phase 8)**: Depends on all user stories

### User Story Dependencies

- **US1 (P1)**: After Foundational — no dependencies on other stories
- **US2 (P1)**: After Setup — no code dependencies, purely infrastructure verification
- **US3 (P2)**: After Foundational — independent of US1 (different files for TTL vs port migration)
- **US4 (P2)**: After Foundational — independent of US1/US3 (new files + different modification points)
- **US5 (P3)**: After US1–US4 — documents the final state

### Within Each User Story

- **Tests written FIRST** — verify they fail before implementation (Constitution Principle VIII)
- Utilities/models before services
- Services before endpoints
- Core implementation before Lua script modifications
- Hooks registration after task creation
- Protected keys verified after TTL implementation

### Parallel Opportunities

- T002 + T003 (Phase 1 config updates — different targets)
- T006 + T007 (Phase 2 foundational — different files)
- T008 through T016 (all US1 port migration — each touches a different file)
- T018 + T019 + T020 (US3 FastAPI TTL — different service files)
- T022 + T023 (US3 Frappe TTL — different files)
- T027 + T028 (US4 tests — different files)
- T033 + T034 (US5 documentation — different files)
- **Cross-story**: US1 + US3 + US4 can proceed in parallel after Phase 2

---

## Parallel Examples

### Phase 3 (US1) — All tasks touch different files

```
Parallel batch: T008, T009, T010, T011, T012, T013, T014, T015, T016
All are [P] — each modifies a separate file with the same pattern (get_redis → get_memora_redis)
```

### Phase 5 (US3) — Grouped by architecture layer

```
Parallel batch 1 (FastAPI services):  T018, T019, T020
Sequential after batch 1:            T021 (Lua script in game_session.py)
Parallel batch 2 (Frappe handlers):  T022, T023
Sequential (TDD):                    T024 (test) → T025 (impl) → T026 (hooks)
```

### Phase 6 (US4) — Tests first, then implementation

```
Parallel batch 1 (tests):    T027, T028
Sequential (implementation): T029 (health endpoint) → T030 (monitor task) → T031 (hooks) → T032 (batch sizing)
```

### Cross-Story Parallelism (after Phase 2)

```
Track A: US1 (T008–T016) — Port migration across 9 files
Track B: US3 (T018–T026) — TTL implementation across 9 tasks
Track C: US4 (T027–T032) — Monitoring (tests + new files + sync.py batch sizing)
```

---

## Implementation Strategy

### MVP First (US1 + US2 Only)

1. Complete Phase 1: Setup (Redis instance running on 13001 with AOF)
2. Complete Phase 2: Foundational (`get_memora_redis()` tested + constants)
3. Complete Phase 3: US1 — All services migrated to port 13001
4. Complete Phase 4: US2 — AOF verified
5. **STOP and VALIDATE**: `bench clear-cache` is safe, data survives restart
6. Deploy if ready — core data protection is in place

### Incremental Delivery

1. Setup + Foundational -> Infrastructure ready
2. US1 + US2 -> Data protection achieved (MVP)
3. US3 -> Memory bounded, scales safely to 100k+ users
4. US4 -> Monitoring + alerting operational, buffer backlog detected
5. US5 -> Documentation complete for production deployment
6. Each increment adds protection without breaking previous work

### Suggested Execution Order (Solo Developer)

Phase 1 -> Phase 2 -> Phase 3 (US1) -> Phase 4 (US2) -> Phase 5 (US3) -> Phase 6 (US4) -> Phase 7 (US5) -> Phase 8 (Polish)

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- **Tests written FIRST** within each phase — verify they fail, then implement (Constitution Principle VIII)
- `.env` and `site_config.json` changes in Phase 1 affect ALL subsequent work — complete first
- After code changes: restart FastAPI (`pkill -f "uvicorn fastapi_app.main:app"`) and Frappe workers (`bench restart`)
- Protected keys (dirty sets, buffer, alltime LB) must NEVER receive TTL — verify after US3
- `get_memora_redis()` must use `decode_responses=True` to match existing `redis.from_url()` patterns
- Frappe event handlers using `get_fastapi_redis()` (reads .env) auto-pick up port 13001 — no code changes needed for those files
- `profile_cache.py` and `plan_sync.py` use `get_fastapi_redis()` — auto-pick up new port, but T023 adds TTL to plan_sync
- **Lua TTL literals**: Lua scripts use hardcoded `172800` (cannot import Python constants). Cross-reference comments in `redis_keys.py` (T006) document which Lua scripts duplicate each value. Update both locations if TTL values change.
