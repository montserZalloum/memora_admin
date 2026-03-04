# FastAPI App Performance + Maintainability Audit

Date: 2026-03-04
Scope: `fastapi_app/`

## Baseline

- Test baseline: `pytest fastapi_app/tests -q`
  - Result: `615 passed`, `10 failed`, `43.04s`
  - Current failures cluster in:
    - leaderboard rank correctness (`fastapi_app/services/leaderboard.py`)
    - raw Redis fast-path instability in progress/session flows (`fastapi_app/services/progress.py`)
    - settings cache hydration miss path (`fastapi_app/services/settings.py`, `fastapi_app/services/hydration.py`)
- Lint baseline: `ruff check fastapi_app`
  - Result: `468` findings
  - Most are style/import issues, but a few highlight real cleanup targets (`zip(..., strict=...)`, unused unpacked values, repeated dependency factories).

## 1. Architecture Map

### Runtime Flow

1. `fastapi_app/main.py:34-186`
   - Lifespan builds Redis pools, a shared `FrappeClient`, selected long-lived services, WebSocket manager, and pub/sub listeners.
   - Middleware chain is `CORSMiddleware` -> `RequestIDMiddleware` -> `GlobalRateLimitMiddleware` (last added runs first).
2. `fastapi_app/api/deps.py:60-566`
   - Shared dependencies provide:
     - Redis clients (`get_redis`, `get_redis_raw`)
     - auth (`get_current_user`, `require_admin`)
     - per-request service construction
     - season/content gating
     - per-player rate limiting
3. Endpoint modules call service-layer orchestration.
4. Service layer is Redis-first and uses Frappe HTTP as the external source of truth and write-through boundary.
5. MariaDB is never accessed directly by FastAPI; it is reached through Frappe whitelisted methods or Frappe REST endpoints.

### Shared Infrastructure

- Redis
  - Primary online state store: sessions, progress bitmaps, stats hashes, access sets, wallets, leaderboards, caches, buffers.
  - Key entrypoints: `fastapi_app/core/redis.py:11-53`, `fastapi_app/core/redis_keys.py`
- Frappe HTTP
  - Generic client: `fastapi_app/services/frappe_client.py:19-154`
  - Admin auth-specific client: `fastapi_app/services/frappe.py:18-208`
- Hydration / cache fill control
  - `fastapi_app/services/hydration.py:1-118`
- Pub/Sub + WebSockets
  - `fastapi_app/core/pubsub.py`, `fastapi_app/core/ws_manager.py:17-179`, `fastapi_app/api/v1/endpoints/notifications.py:22-85`

### Endpoint Inventory (54 HTTP + 1 WebSocket)

| Area | Routes | Primary deps/services | Main backing stores |
| --- | --- | --- | --- |
| `health` | `GET /health/live`, `/health/ready`, `/health/redis` | `RedisClient` | Redis |
| `announcements` | `GET /announcements/` | `AnnouncementServiceDep` | Redis cache, Frappe |
| `auth` | `POST /auth/player/login`, `/admin/login`, `/refresh`, `GET /registration-options`, `POST /player/register`, `/player/register/verify`, `/player/register/resend`, `/player/password-reset/request`, `/verify`, `/confirm` | direct `RedisClient`, `SettingsDep`, `get_frappe_client`, `SessionService`, `DeviceService`, `OTPService`, `WalletService`, `SettingsService` | Redis, Frappe, WS manager |
| `catalog` | `GET /catalog/` | `CurrentUser`, `CatalogServiceDep` | Redis, Frappe |
| `purchase` | `POST /purchase/` | `CurrentUser`, `PurchaseServiceDep` | Redis, Frappe |
| `access` | `POST /access/grants`, `DELETE /access/grants`, `GET /access/grants/{player_id}` | `RequireAdmin`, `AccessServiceDep` | Redis, Frappe hydrate |
| `leaderboard` | `GET /leaderboard/{lb_type}`, `GET /leaderboard/{lb_type}/me` | `LeaderboardServiceDep`, `ProfileServiceDep` | Redis |
| `plans` | `GET /plans/{plan_id}/manifest` | `PlanService` | Redis, Frappe |
| `plan_change` | `POST /plans/change`, `GET /plans/available` | `PlanChangeServiceDep` | Redis, Frappe |
| `progress` | `GET /progress/`, `/{subject}/tracks`, `/{subject}/tracks/{track_id}`, `/{subject}/tracks/{track_id}/units/{unit_id}`, `/{subject}/topics/{topic_id}/lessons`, `/{subject}` | `ProgressServiceDep`, `HierarchyServiceDep`, `AccessServiceDep`, `StatsServiceDep` | Redis, Frappe hydrate |
| `practice` | `GET /practice/hierarchy`, `POST /practice/start`, `POST /practice/submit`, `POST /practice/continue` | `PracticeServiceDep`, `CurrentUser`, `ActiveSeasonDep` | Redis, Frappe |
| `sessions` | `GET /sessions/current`, `POST /sessions/start`, `POST /sessions/end` | `GameSessionServiceDep`, `HierarchyServiceDep`, `AccessServiceDep`, `WalletServiceDep`, `LeaderboardServiceDep`, `SettingsServiceDep`, `ProgressServiceDep`, `StatsServiceDep` | Redis, Frappe hydrate |
| `settings` | `GET /settings/gamification` | `SettingsServiceDep` | Redis, Frappe |
| `subscriptions` | `GET /subscriptions` | `CurrentUser`, `AccessServiceDep` | Redis |
| `wallet` | `GET /wallet`, `GET /wallet/{player_id}` | `CurrentUser` / `RequireAdmin`, `WalletServiceDep` | Redis, Frappe hydrate |
| `reviews` | `GET /reviews`, `GET /reviews/{subject}`, `POST /reviews/{subject}/submit` | `ReviewServiceDep`, `WalletServiceDep`, `LeaderboardServiceDep`, `ActiveSeasonDep` | Redis, Frappe |
| `profile` | `GET /profile`, `/stats`, `/mastery`, `/activity`, `PUT /avatar`, `POST /logout` | `ProfilePageServiceDep` | Redis, Frappe |
| `reports` | `POST /reports` | `CurrentUser`, `RedisClient` | Redis buffer, Frappe |
| `voucher` | `POST /voucher/preview`, `POST /voucher/redeem` | `CurrentUser`, `VoucherServiceDep` | Redis, Frappe |
| `webhooks` | `POST /webhooks/payment` | direct Redis + background retry flow | Redis, Frappe |
| `notifications` | `WS /notifications/ws` | app-state `ConnectionManager` | JWT decode, Redis pub/sub |

## 2. Top Issues (Prioritized)

Scoring key:
- Impact: 1 low, 5 high
- Effort: 1 low, 5 high
- Risk: 1 low, 5 high
- Priority = `(Impact x (6 - Effort) x (6 - Risk))`

| Priority | Issue | Impact | Effort | Risk | Evidence |
| --- | --- | --- | --- | --- | --- |
| 100 | Leaderboard `/me` trusts stale tier metadata and returns incorrect dense ranks / `xp_to_next` | 5 | 2 | 1 | `fastapi_app/services/leaderboard.py:480-519`; failing tests in `test_leaderboard_bugs.py`, `test_leaderboard_diagnostic.py` |
| 80 | Progress raw-Redis fast path is not fail-safe; when the raw client/pool is invalid, progress and session endpoints can 500 | 5 | 2 | 2 | `fastapi_app/services/progress.py:250-283`; `fastapi_app/api/deps.py:65-74`; failing tests in `test_progress_endpoints.py`, `test_session_endpoints.py` |
| 80 | `FrappeClient` logs full request payloads at `info`, including large SQL strings and parameter arrays on hot paths | 4 | 1 | 1 | `fastapi_app/services/frappe_client.py:63-80`; large payload sources in `fastapi_app/services/practice.py:1034-1045, 1261-1290, 1415-1435, 1460-1466` |
| 64 | Progress endpoints duplicate the same hierarchy/access/stats fallback pipeline four times, increasing drift and branch cost in the hottest read surface | 4 | 3 | 2 | `fastapi_app/api/v1/endpoints/progress.py:387-457, 502-583, 635-715, 838-961` |
| 64 | Settings hydration can suppress valid cache rehydration after an empty/failed fill and currently fails baseline tests | 4 | 2 | 2 | `fastapi_app/services/settings.py:49-72`; `fastapi_app/services/hydration.py:112-117`; failing `test_settings_service.py::test_tc_set_02_cache_miss_fetches_and_caches` |
| 48 | Frappe timeout policy is too coarse (single 30s total timeout, no pool timeout / no selective retries), which keeps coroutines occupied too long under upstream slowness | 4 | 3 | 3 | `fastapi_app/services/frappe_client.py:31-43`; `fastapi_app/core/config.py:79-82` |
| 36 | Auth and registration reimplement the same player sign-in orchestration twice, adding drift and wasted maintenance on a user-facing hot path | 3 | 2 | 2 | `fastapi_app/api/v1/endpoints/auth.py:92-231` and `fastapi_app/api/v1/endpoints/auth.py:466-629` |

### Why these are first

- The first three already show up as either failing tests or clear hot-path overhead.
- All of them can be validated with existing tests plus a focused benchmark.
- None require API contract changes; they either fix broken behavior or reduce internal overhead.

## 3. Duplication Table

| Pattern | Locations (file:line) | Proposed abstraction | Expected gain | Effort |
| --- | --- | --- | --- | --- |
| Progress access + stats-first fallback + bitmap fallback | `fastapi_app/api/v1/endpoints/progress.py:387-457`, `502-583`, `635-715`, `838-961` | Add `ProgressReadContext` loader in service layer: resolve hierarchy, access, partial/full stats, and `completed_bits` once | Remove ~150-220 repeated lines; cut repeated branch logic in 4 endpoints; benchmark target: lower `p95` for `/progress/*` by 5-10% under mixed hot/cold cache | Medium |
| Unlock helper logic duplicated in endpoint layer and service layer | `fastapi_app/api/v1/endpoints/progress.py:77-247`; `fastapi_app/services/unlock.py:11-111` | Reuse `services.unlock` or move all unlock reads into one shared module that handles both bitmap and stats modes | Reduce behavioral drift risk; validation metric: same unlock tests + add one shared helper test file | Low |
| Player sign-in flow (device registration, wallet read, WS kick, session create, token build) | `fastapi_app/api/v1/endpoints/auth.py:162-231`; `fastapi_app/api/v1/endpoints/auth.py:556-629` | Extract `complete_player_sign_in(...)` helper/service | Remove ~70 duplicated lines; benchmark target: after adding one internal `asyncio.gather`, reduce login `p95` by 1 Redis RTT equivalent | Low |
| Cache-aside JSON patterns (`GET` -> `json.loads` -> Frappe -> `json.dumps`) | `fastapi_app/services/announcements.py:21-37`, `fastapi_app/services/catalog.py:37-78`, `fastapi_app/services/review.py:28-50`, `fastapi_app/services/plan.py:38-85`, `fastapi_app/api/v1/endpoints/auth.py:382-400` | Shared `JsonCacheLoader` helper with optional TTL, parser, and empty-result policy | Reduce repeated cache policy bugs; measurable by LOC drop and fewer divergent cache semantics in review | Medium |
| Frappe-backed service factory boilerplate | `fastapi_app/api/deps.py:200-406` | Generic dependency factory or app-state service registry (`get_service(name)`) | Remove ~120+ lines of constructor duplication; lower per-request object allocation count; benchmark via `tracemalloc` allocations/request | Medium |
| Client IP extraction | `fastapi_app/api/v1/endpoints/auth.py:45-51`; `fastapi_app/middleware/rate_limit.py:31-39` | Shared `network.py` helper | Remove drift in proxy handling; validation: one unit test for `X-Forwarded-For` parsing | Low |
| Atomic INCR+EXPIRE rate-limit scripts | `fastapi_app/services/global_rate_limit.py:17-76`; `fastapi_app/services/rate_limit.py:9-87`; `fastapi_app/services/voucher.py:18-126` | Shared Redis script wrapper returning `(count, ttl)` | Reduce duplicate Lua maintenance; blocked-path benchmark: remove one extra `TTL` RTT in login limiter | Medium |

## 4. Performance Review

### Hot Paths

#### A. Progress read endpoints

- Files:
  - `fastapi_app/api/v1/endpoints/progress.py`
  - `fastapi_app/services/progress.py`
  - `fastapi_app/services/stats.py`
  - `fastapi_app/services/hierarchy.py`
- Current strengths:
  - stats-first caching
  - Redis pipelines for lesson bit reads
  - local TTL caches in hierarchy/stats
- Current risks:
  - duplicated fallback logic increases drift risk and branch cost
  - raw Redis fast path is treated as mandatory once configured, even though it is only an optimization
  - repeated full-tree scans for track/unit/topic lookup remain in handler code

#### B. Practice session flow

- Files:
  - `fastapi_app/services/practice.py`
- Current strengths:
  - batched topic candidate selection (`_select_candidates_for_topics`)
  - Redis session hash + pipelines
- Current risks:
  - very large SQL strings and param arrays are passed through `FrappeClient.call()`, which currently logs full payloads
  - session state serializes several large JSON arrays into Redis hash fields (`fastapi_app/services/practice.py:557-570`, `828-832`, `895-904`)
  - completed-mode scans still require full hierarchy traversal

#### C. Session end / XP award flow

- Files:
  - `fastapi_app/api/v1/endpoints/sessions.py:188-445`
  - `fastapi_app/services/game_session.py`
  - `fastapi_app/services/wallet.py`
  - `fastapi_app/services/leaderboard.py`
- Current strengths:
  - Redis Lua combines session delete + progress set + dirty queueing
  - pipelined XP and stats writes
- Current risks:
  - the fallback call to `progress_service.get_completed_bits()` on cold stats path inherits the raw-Redis failure mode
  - leaderboard rank metadata bugs mean write path and read path can diverge

#### D. External Frappe traffic

- Files:
  - `fastapi_app/services/frappe_client.py`
  - `fastapi_app/services/frappe.py`
- Current risks:
  - no split connect/read/pool timeouts
  - no retry policy for safe reads
  - payload logging is too expensive on hot SQL-heavy calls
  - `FrappeAuthService` recreates an `httpx.AsyncClient` per call (`fastapi_app/services/frappe.py:53, 156, 187`)

### Blocking I/O / Async Safety

- No obvious synchronous `requests`, `time.sleep`, or file reads were found in request paths.
- Real async safety risk is resource ownership:
  - `fastapi_app/api/deps.py:65-74` constructs a raw Redis client from an app-state pool that is not treated as optional when used.
  - When that client/pool is invalid for the current loop, the request fails instead of degrading to the existing BITFIELD fallback.

### Serialization / Validation Overhead

- `FrappeClient._call_method()` logs full `kwargs` before every request (`fastapi_app/services/frappe_client.py:63`).
  - For practice SQL calls this means serializing:
    - full SQL text
    - long `params` arrays
  - This creates avoidable CPU work and excessive log volume.
- Cache-aside services repeatedly do `json.loads` / `json.dumps` on large payloads.
  - This is acceptable for now, but the policy is duplicated and inconsistent.

### Query / Data-Shaping Risks

- Practice service still constructs large `IN (...)` SQL queries against Frappe:
  - `fastapi_app/services/practice.py:1025-1045`
  - `fastapi_app/services/practice.py:1240-1290`
  - `fastapi_app/services/practice.py:1397-1435`
- These are already better than N+1 query loops, but they should remain under explicit size guardrails.
- Plan change cleanup scans all leaderboard keys (`fastapi_app/services/plan_change.py:247-264`).
  - This is acceptable at low volume, but it is O(total leaderboard keys) and becomes expensive as subject and date cardinality grow.

## 5. Instrumentation & Measurement Plan

### Minimal Instrumentation

1. Add endpoint timing middleware
   - Record:
     - route template
     - method
     - status code
     - elapsed ms
     - request id
   - Emit a structured log or metrics counter/histogram.
   - Recommended implementation: pure ASGI middleware, not another `BaseHTTPMiddleware`.

2. Add Redis timing wrappers for hot paths
   - Start with:
     - `HierarchyService.get_hierarchy`
     - `StatsService.get_stats` / `get_partial_stats` / `get_or_recompute`
     - `ProgressService.get_completed_bits`
     - `LeaderboardService.get_top` / `get_my_rank`
   - Emit:
     - redis command group
     - elapsed ms
     - cache hit/miss path

3. Add Frappe timing in `FrappeClient._call_method`
   - Record:
     - method name
     - status code
     - elapsed ms
     - payload size summary, not raw payload

4. Optional tracing
   - Add OpenTelemetry spans only after the timing middleware is in place.
   - Span boundaries:
     - request
     - Frappe call
     - Redis hot-path call groups

### Before / After Benchmark Plan

Use the same scenarios before and after each change.

Tools:
- `hey` for quick HTTP latency/RPS
- `locust` for mixed-user flows (already aligned with repo practices)
- existing `pytest` tests as correctness gates

Core scenarios:

1. Progress read mix
   - 70% `GET /api/v1/progress/{subject}`
   - 20% `GET /api/v1/progress/{subject}/tracks/{track_id}`
   - 10% `GET /api/v1/progress/{subject}/topics/{topic_id}/lessons`
   - Metrics:
     - `p50`, `p95`, `p99`
     - RPS
     - 5xx rate
     - Redis ops/request

2. Practice continue
   - `POST /api/v1/practice/continue`
   - Metrics:
     - `p50`, `p95`, `p99`
     - Frappe call ms
     - log bytes/request
     - CPU %

3. Leaderboard read
   - `GET /api/v1/leaderboard/weekly`
   - `GET /api/v1/leaderboard/weekly/me`
   - Metrics:
     - correctness (rank consistency against `get_top`)
     - `p95`
     - Redis commands/request

4. Auth login
   - `POST /api/v1/auth/player/login`
   - Metrics:
     - `p95`
     - device registration failures
     - Frappe call ms
     - WS kick ms

Pass/fail targets for the recommended changes:
- No API schema or status-code regressions.
- No new failing tests.
- Measurable reduction in one of:
  - latency
  - log volume
  - 5xx rate
  - Redis round-trips
  - code-path duplication / branch count

## 6. Quick Wins (1-2 Days)

1. Make `ProgressService` raw bitmap reads fail open
   - Files: `fastapi_app/services/progress.py:250-254`
   - Why: the raw client is an optimization, but today it can take endpoints down.
   - Measure:
     - rerun the currently failing progress/session tests
     - compare 5xx rate on progress/session endpoints under forced raw-client failure

2. Stop logging full `FrappeClient` payloads at `info`
   - Files: `fastapi_app/services/frappe_client.py:63-80`
   - Why: this is hot-path CPU and log I/O on practice flows.
   - Measure:
     - bytes written per request in `practice/start` and `practice/continue`
     - CPU % under the same load

3. Repair/validate leaderboard tier metadata before trusting it
   - Files: `fastapi_app/services/leaderboard.py:478-519`
   - Why: current baseline has rank correctness failures.
   - Measure:
     - rerun leaderboard failing tests
     - compare `GET /leaderboard/{type}` vs `/me` rank agreement for seeded fixtures

4. Fix settings hydration empty-fill behavior
   - Files: `fastapi_app/services/settings.py:49-72`, `fastapi_app/services/hydration.py:112-117`
   - Why: baseline failure and repeated fallback to defaults.
   - Measure:
     - rerun `test_settings_service.py`
     - confirm cache key is written on first miss

## 7. Mid Refactors (About 1 Week)

1. Move progress read orchestration into a shared service helper
   - Collapse repeated hierarchy/access/stats fallback logic into one loader object.
   - Measure:
     - endpoint handler LOC reduction
     - reduced branch count
     - `p95` for progress endpoints

2. Extract player sign-in orchestration from auth endpoints
   - One helper for:
     - settings read
     - device registration
     - wallet hydration
     - WS session kick
     - session creation
     - token assembly
   - Measure:
     - duplicated LOC removed
     - identical auth endpoint test pass rate
     - login `p95`

3. Unify cache-aside loaders
   - Reuse `guarded_hydrate` more consistently and centralize empty-result policy.
   - Measure:
     - LOC removed
     - cache hit/miss logs become standardized

4. Replace `BaseHTTPMiddleware` with pure ASGI middleware for request ID / rate limiting
   - Files: `fastapi_app/middleware/request_id.py`, `fastapi_app/middleware/rate_limit.py`
   - Measure:
     - `hey -z 30s` against `/api/v1/health/live` and `/api/v1/catalog/`
     - compare RPS and task allocations

## 8. Bigger Bets (Optional)

1. Add lazy lookup indexes to `SubjectHierarchy`
   - Add cached maps for `track_id`, `unit_id`, `topic_id`, and `lesson_id`.
   - Use them in progress and practice instead of repeated tree scans.
   - Measure:
     - micro-benchmark lookup latency
     - `p95` on deep progress routes with large hierarchies

2. Split Frappe timeout policy and add selective retries
   - Example:
     - short connect timeout
     - bounded pool timeout
     - one retry for idempotent reads only
   - Measure:
     - p95 under injected upstream latency
     - event-loop concurrency under upstream stalls

3. Replace leaderboard-key scans in plan change cleanup with an index of touched leaderboards per player
   - Current cleanup is O(total leaderboard keys).
   - Measure:
     - plan change runtime before/after with synthetic high leaderboard-key counts

## 9. Proposed Code Changes (PR-Ready Patches)

### Patch 1: Make Raw Progress Reads Fail Open

Rationale:
- Fixes current request failures without changing the response contract.
- Keeps the raw-GET path as an optimization only.

How to measure:
- Re-run:
  - `pytest fastapi_app/tests/test_progress_endpoints.py -q`
  - `pytest fastapi_app/tests/test_session_endpoints.py -q`
- Add a targeted test that forces `_get_completed_bits_raw()` to raise and assert the BITFIELD path still returns 200.

Risk:
- Slightly slower fallback when the raw path is unhealthy.

Rollback:
- Revert the try/except wrapper.

```diff
diff --git a/fastapi_app/services/progress.py b/fastapi_app/services/progress.py
@@
-        if self._raw_redis is not None:
-            return await self._get_completed_bits_raw(key, bit_range, num_bytes)
+        if self._raw_redis is not None:
+            try:
+                return await self._get_completed_bits_raw(key, bit_range, num_bytes)
+            except Exception as e:
+                logger.warning(
+                    "progress_raw_read_failed_falling_back",
+                    key=key,
+                    bit_range=bit_range,
+                    error=str(e),
+                )
 
         # Fallback: chunked BITFIELD (for contexts without raw client)
         return await self._get_completed_bits_bitfield(key, bit_range, num_bytes)
```

### Patch 2: Validate Leaderboard Tier Metadata Before Using It

Rationale:
- Current read path trusts metadata if the keys merely exist.
- Baseline tests show that stale or incomplete metadata returns wrong ranks.

How to measure:
- Re-run the failing leaderboard tests.
- Add a seed that creates a leaderboard ZSET without metadata and verify `get_my_rank()` repairs and returns the same dense rank as `get_top()`.

Risk:
- One small extra Redis read on `/leaderboard/{type}/me`.

Rollback:
- Remove the additional validation and go back to existence-only checks.

```diff
diff --git a/fastapi_app/services/leaderboard.py b/fastapi_app/services/leaderboard.py
@@
-            tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)
+            tieridx_key, tiercnt_key = lbmeta_keys_from_lb_key(key)
+            version_key = self._tiermeta_version_key(tiercnt_key)
 
             pipe = self.redis.pipeline()
             pipe.zrange(key, start, stop, desc=True, withscores=True)
             pipe.exists(tieridx_key)
             pipe.exists(tiercnt_key)
+            pipe.exists(version_key)
+            pipe.hvals(tiercnt_key)
             pipe.zcount(tieridx_key, f"({xp}", "+inf")
             pipe.zrangebyscore(tieridx_key, f"({xp}", "+inf", withscores=True, start=0, num=1)
-            neighbors_raw, tieridx_exists, tiercnt_exists, idx_distinct_above, min_above_entries = await pipe.execute()
+            (
+                neighbors_raw,
+                tieridx_exists,
+                tiercnt_exists,
+                version_exists,
+                tier_counts_raw,
+                idx_distinct_above,
+                min_above_entries,
+            ) = await pipe.execute()
 
-            if tieridx_exists and tiercnt_exists:
+            tier_count_sum = sum(int(v) for v in tier_counts_raw or [])
+            metadata_healthy = (
+                tieridx_exists
+                and tiercnt_exists
+                and version_exists
+                and tier_count_sum == total
+            )
+
+            if metadata_healthy:
                 # Indexed path: O(log T) via tier index ZSET.
                 distinct_above = idx_distinct_above
                 min_above = int(min_above_entries[0][1]) if min_above_entries else -1
                 fallback_used = False
                 repair_used = False
             else:
                 repair_used = await self._repair_tier_metadata_for_key(key)
```

### Patch 3: Stop Logging Full Frappe Payloads on Hot Paths

Rationale:
- `PracticeService` sends large SQL strings and param arrays through `FrappeClient.call()`.
- Logging `args=kwargs` at `info` serializes those large payloads on every call.

How to measure:
- Run a fixed `practice/continue` load test before/after.
- Compare:
  - log bytes/request
  - CPU %
  - `p95`

Risk:
- Lower observability for payload contents in normal logs.

Rollback:
- Restore the old logging line.

```diff
diff --git a/fastapi_app/services/frappe_client.py b/fastapi_app/services/frappe_client.py
@@
-        logger.info("frappe_api_call", method=method, args=kwargs)
+        logger.debug(
+            "frappe_api_call",
+            method=method,
+            arg_keys=sorted(kwargs.keys()),
+            sql_length=len(kwargs.get("sql", "")) if isinstance(kwargs.get("sql"), str) else 0,
+            params_count=len(kwargs.get("params", [])) if isinstance(kwargs.get("params"), list) else 0,
+        )
```

### Patch 4: Make Hydration Sentinel Conditional for Settings

Rationale:
- The current pattern can leave the cache empty and still mark the key as recently hydrated.
- This matches the failing settings cache-miss baseline.

How to measure:
- Re-run `pytest fastapi_app/tests/test_settings_service.py -q`
- Add an assertion that the settings cache key exists immediately after the first successful miss.

Risk:
- Small signature change in `guarded_hydrate`.

Rollback:
- Revert to the old `Awaitable[None]` contract.

```diff
diff --git a/fastapi_app/services/hydration.py b/fastapi_app/services/hydration.py
@@
-    hydrate_fn: Callable[[], Awaitable[None]],
+    hydrate_fn: Callable[[], Awaitable[bool]],
@@
-            async with sem:
-                await hydrate_fn()
+            async with sem:
+                wrote_data = await hydrate_fn()
     finally:
-        await redis_client.set(sentinel_key, "1", ex=sentinel_ttl)
+        if not locals().get("wrote_data", False):
+            await redis_client.set(sentinel_key, "1", ex=sentinel_ttl)
         await redis_client.delete(lock_key)
diff --git a/fastapi_app/services/settings.py b/fastapi_app/services/settings.py
@@
-    async def _hydrate_from_frappe(self) -> None:
+    async def _hydrate_from_frappe(self) -> bool:
@@
-            return
+            return False
@@
-            return
+            return False
@@
         await self.redis.set(
             gamification_settings_key(),
             settings.model_dump_json(),
         )
@@
         logger.info(
             "settings_cached",
             base_xp=settings.base_lesson_xp,
             replay_xp=settings.replay_xp,
             max_streak=settings.max_streak_multiplier_percent,
         )
+        return True
```

### Patch 5: Extract Player Sign-In Finalization

Rationale:
- `player_login()` and `player_register_verify()` repeat the same device-registration, wallet-read, WS-kick, session-create, and token-build flow.
- A shared helper removes drift and makes it easy to parallelize the independent wallet read and WebSocket kick.

How to measure:
- Re-run `pytest fastapi_app/tests/test_auth_endpoints.py -q`
- Compare login `p95` before/after with a fixed `POST /api/v1/auth/player/login` load.

Risk:
- Low-to-moderate because both public auth flows depend on the shared helper.

Rollback:
- Inline the helper back into the two handlers.

```diff
diff --git a/fastapi_app/api/v1/endpoints/auth.py b/fastapi_app/api/v1/endpoints/auth.py
@@
+import asyncio
@@
+async def _complete_player_sign_in(
+    *,
+    request: Request,
+    redis: RedisClient,
+    settings: SettingsDep,
+    frappe_client,
+    profile: dict,
+    player_id: str,
+    device_id: str,
+    season_id: str,
+) -> PlayerLoginResponse | JSONResponse:
+    settings_service = SettingsService(redis, frappe_client)
+    game_settings = await settings_service.get_gamification_settings()
+
+    device_service = DeviceService(redis)
+    device_result = await device_service.register_device(
+        user_id=player_id,
+        device_id=device_id,
+        user_agent=request.headers.get("User-Agent", "Unknown"),
+        max_devices=game_settings.max_devices_per_player,
+        platform_hint=request.headers.get("X-Platform"),
+    )
+    if not device_result.success:
+        return JSONResponse(
+            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
+            content={
+                "code": "DEVICE_LIMIT_EXCEEDED",
+                "message": f"Device limit reached ({device_result.current_count}/{device_result.max_count}). Contact support to manage your devices.",
+            },
+        )
+
+    wallet_service = WalletService(redis, frappe_client=frappe_client)
+    wallet, _ = await asyncio.gather(
+        wallet_service.get_wallet(player_id),
+        _force_kick_old_sessions(request, player_id),
+    )
+
+    session_service = SessionService(redis)
+    family_id = await session_service.create_session(
+        player_id,
+        plan_id=profile["plan"],
+        ttl_days=game_settings.session_timeout_days,
+        season_id=season_id,
+    )
+    evict_session_cache(player_id)
+
+    return PlayerLoginResponse(
+        access_token=create_access_token(
+            user_id=player_id,
+            mobile=profile["mobile"],
+            plan_id=profile["plan"],
+            display_name=profile.get("display_name", ""),
+            family_id=family_id,
+            season_id=season_id,
+            expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
+        ),
+        refresh_token=create_refresh_token(
+            user_id=player_id,
+            family_id=family_id,
+            expires_delta=timedelta(days=game_settings.session_timeout_days),
+        ),
+        profile=LoginProfile(
+            display_name=profile.get("display_name", ""),
+            avatar=profile.get("avatar") or "default_avatar",
+            xp=wallet.get("xp", 0),
+        ),
+    )
@@
+    return await _complete_player_sign_in(
+        request=request,
+        redis=redis,
+        settings=settings,
+        frappe_client=frappe_client,
+        profile=profile,
+        player_id=player_id,
+        device_id=device_id,
+        season_id=profile.get("season", ""),
+    )
@@
+    return await _complete_player_sign_in(
+        request=request,
+        redis=redis,
+        settings=settings,
+        frappe_client=frappe_client,
+        profile=profile,
+        player_id=player_id,
+        device_id=device_id,
+        season_id=season,
+    )
```

## 10. Top 5 Next Steps

1. Fix `LeaderboardService.get_my_rank()` metadata validation first, because it is already returning wrong ranks and breaking tests.
2. Make `ProgressService.get_completed_bits()` fail open to BITFIELD when the raw optimization path breaks.
3. Reduce `FrappeClient` payload logging immediately; it is low-risk and should improve hot-path CPU and log volume.
4. Fix the settings hydration sentinel behavior so cache misses can repopulate correctly.
5. After the correctness fixes, extract the shared player sign-in helper and then move on to the bigger progress read refactor.
