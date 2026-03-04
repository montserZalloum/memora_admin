# `fastapi_app/` Performance + Maintainability Audit

## Scope And Baseline

- Scope reviewed: the full `fastapi_app/` tree (`api/`, `services/`, `models/`, `core/`, `middleware/`, `tests/`).
- Test baseline: `pytest fastapi_app/tests/ -v` passed (`628 passed in 47.36s`).
- Lint baseline: `pre-commit run --all-files` does not pass cleanly today, but the failures are repo-wide and mostly outside `fastapi_app/` (YAML, Ruff, ESLint, formatting). Treat that as repository debt, not a FastAPI regression signal.
- Blocking I/O review: no obvious synchronous `requests`, file I/O, or `time.sleep()` calls were found inside async request paths. The code is already using async Redis and `httpx.AsyncClient`. The biggest remaining costs are extra network round-trips, repeated cache-aside code, and a few expensive Redis scans.

## 1. Architecture Map

### Dependency Flow

`FastAPI router -> shared deps in fastapi_app/api/deps.py -> per-feature service objects -> Redis + Frappe HTTP APIs -> MariaDB/Redis on the Frappe side`

There is no local repository layer. `fastapi_app` talks directly to:

- Redis for hot-path state, caching, rate limits, leaderboards, sessions, and pub/sub.
- Frappe HTTP endpoints through `FrappeClient` (`fastapi_app/services/frappe_client.py`) and `FrappeAuthService` (`fastapi_app/services/frappe.py`).
- MariaDB only indirectly, through Frappe whitelisted methods and SQL proxy methods such as `memora_admin.api.practice.execute_practice_query`.

### Shared Dependencies

| Dependency | Purpose | Main locations |
| --- | --- | --- |
| `CurrentUser` | JWT auth + Redis-backed single-session validation | `fastapi_app/api/deps.py:81-176` |
| `RequireAdmin` | Admin-only guard | `fastapi_app/api/deps.py:184-194` |
| `ActiveSeasonDep` | Season gate for gameplay/review mutations | `fastapi_app/api/deps.py:449-464` |
| `require_rate_limit(scope)` | Per-player Redis-backed rate limiting | `fastapi_app/api/deps.py:422-443` |
| `RedisClient` / `get_redis()` | Shared Redis access via app pool | `fastapi_app/api/deps.py:60-79` |
| `get_frappe_client()` | Shared app-level Frappe API client singleton | `fastapi_app/api/deps.py:279-294` |
| Service deps (`*ServiceDep`) | Thin factories over Redis + Frappe | `fastapi_app/api/deps.py:200-406` |

### Endpoint Inventory

| Router | Routes | Primary deps | Primary downstreams |
| --- | --- | --- | --- |
| `health.py` | `GET /health/live`, `GET /health/ready`, `GET /health/redis` | `RedisClient` (ready/redis) | Redis only |
| `announcements.py` | `GET /announcements/` | `CurrentUser`, `AnnouncementServiceDep` | Redis cache, Frappe announcements API |
| `auth.py` | `POST /auth/player/login`, `POST /auth/admin/login`, `POST /auth/refresh`, `GET /auth/registration-options`, `POST /auth/player/register`, `POST /auth/player/register/verify`, `POST /auth/player/register/resend`, `POST /auth/player/password-reset/request`, `POST /auth/player/password-reset/verify`, `POST /auth/player/password-reset/confirm` | `RedisClient`, `SettingsDep`, app state `ConnectionManager` | Redis sessions, OTP state, devices, wallet, Frappe auth/profile APIs |
| `catalog.py` | `GET /catalog/` | `CurrentUser`, `CatalogServiceDep` | Redis cache, Redis sets, Frappe catalog API |
| `purchase.py` | `POST /purchase/` | `CurrentUser`, `PurchaseServiceDep` | Redis pending set, Frappe purchase API |
| `access.py` | `POST /access/grants`, `DELETE /access/grants`, `GET /access/grants/{player_id}` | `RequireAdmin`, `AccessServiceDep` | Redis grant sets |
| `leaderboard.py` | `GET /leaderboard/{lb_type}`, `GET /leaderboard/{lb_type}/me` | `CurrentUser`, `LeaderboardServiceDep`, `ProfileServiceDep` | Redis ZSETs + metadata, Redis profile cache, Frappe profile batch API |
| `plans.py` | `GET /plans/{plan_id}` | `PlanService` via `Depends(get_plan_service)` | Redis cache, Frappe plan API |
| `progress.py` | `GET /progress/`, `GET /progress/{subject}/tracks`, `GET /progress/{subject}/tracks/{track_id}`, `GET /progress/{subject}/tracks/{track_id}/units/{unit_id}`, `GET /progress/{subject}/topics/{topic_id}/lessons`, `GET /progress/{subject}` | `CurrentUser`, `HierarchyServiceDep`, `AccessServiceDep`, `ProgressServiceDep`, `StatsServiceDep` | Redis hierarchy cache, progress bitmaps, stats hashes, Frappe hierarchy hydration |
| `sessions.py` | `GET /sessions/current`, `POST /sessions/start`, `POST /sessions/end` | `CurrentUser`, `ActiveSeasonDep`, multiple service deps, per-player rate limit | Redis sessions, progress, wallet, leaderboard, hierarchy cache, Frappe only via hydrated services |
| `settings.py` | `GET /settings/gamification` | `SettingsServiceDep` | Redis cache, Frappe settings API |
| `subscriptions.py` | `GET /subscriptions` | `CurrentUser`, `AccessServiceDep` | Redis access sets |
| `wallet.py` | `GET /wallet`, `GET /wallet/{player_id}` | `CurrentUser`, `RequireAdmin`, `WalletServiceDep` | Redis wallet, Frappe wallet hydration |
| `webhooks.py` | `POST /webhooks/payment` | `RedisClient`, `BackgroundTasks` | Redis idempotency + queue, Frappe grants/subscriptions APIs |
| `notifications.py` | `WS /notifications/ws` | WebSocket JWT parse, app state `ConnectionManager` | Redis pub/sub listener, in-memory websocket manager |
| `reviews.py` | `GET /reviews`, `GET /reviews/{subject}`, `POST /reviews/{subject}/submit` | `CurrentUser`, `ActiveSeasonDep`, `ReviewServiceDep`, `WalletServiceDep`, `LeaderboardServiceDep`, per-player rate limit | Redis review overview cache, Frappe review APIs, Redis wallet/ZSETs |
| `profile.py` | `GET /profile`, `GET /profile/stats`, `GET /profile/mastery`, `GET /profile/activity`, `PUT /profile/avatar`, `POST /profile/logout` | `CurrentUser`, `ProfilePageServiceDep` | Redis wallet/profile/mastery/activity caches, Frappe profile APIs |
| `reports.py` | `POST /reports` | `CurrentUser`, `RedisClient` | Redis cooldown, Frappe report API |
| `voucher.py` | `POST /voucher/preview`, `POST /voucher/redeem` | `CurrentUser`, `VoucherServiceDep` | Redis rate-limit keys, Frappe voucher APIs |
| `practice.py` | `GET /practice/hierarchy`, `POST /practice/start`, `POST /practice/submit`, `POST /practice/continue` | `CurrentUser`, `ActiveSeasonDep`, `PracticeServiceDep`, per-player rate limit | Redis practice session, hierarchy cache, progress bitmaps, Frappe SQL proxy methods |
| `plan_change.py` | `POST /plans/change`, `GET /plans/available` | `CurrentUser`, `PlanChangeServiceDep` | Redis freeze/cooldown/cache cleanup, Frappe plan-change API |

## 2. Top Issues (Prioritized)

Scoring uses `Impact x Ease x Safety` on a 1-5 scale, where higher is better. `Ease=5` means low effort. `Safety=5` means low rollout risk.

| Score | Issue | Evidence | Why it matters | How to measure |
| --- | --- | --- | --- | --- |
| `100` | `PracticeService` is wired to the slow bitmap path even though the app already has a raw Redis pool | `fastapi_app/api/deps.py:369-385`, especially `ProgressService(... raw_redis=None)` at `:377`; raw fast path lives at `fastapi_app/services/progress.py:216-329`; `PracticeService` uses it in `fastapi_app/services/practice.py:456-474` | `GET /practice/hierarchy` and completed-filter practice flows pay multi-command `BITFIELD` work instead of a single binary-safe `GET`. This is a direct, hot-path latency penalty on read-heavy practice endpoints. | Benchmark `/api/v1/practice/hierarchy?subject_id=...&filter=completed` with large bitmaps. Track Redis commands/request, p50/p95, and CPU. Expect fewer Redis commands and lower p95. |
| `80` | `ProfileService` truncates cache misses above 50 IDs instead of chunking them | `fastapi_app/services/profile.py:138-150` truncates; `fastapi_app/api/v1/endpoints/leaderboard.py:35-36` allows `limit<=100`, and `:77-92` depends on batch profile enrichment | On a cold/partial cache miss, top-100 leaderboard pages silently downgrade the second half of profiles to fallback identities. It also guarantees repeated misses for the dropped IDs on future requests. | Warm-cache clear profile keys, then hit `/api/v1/leaderboard/daily?limit=100`. Compare fallback profile count, Frappe batch calls, and p95 before/after chunking. |
| `64` | `FrappeAuthService` still uses per-request HTTP client construction and bypasses the shared pooled `FrappeClient` pattern | `fastapi_app/services/frappe.py:37-205` creates `httpx.AsyncClient` via `async with` in every public method; `fastapi_app/api/v1/endpoints/auth.py:273-275` constructs a new `FrappeAuthService` per admin login; shared pooled client already exists in `fastapi_app/services/frappe_client.py:20-45` | Admin login and any future auth lookups lose cross-request connection reuse, pay more TCP/TLS setup cost, and maintain a second network abstraction that drifts from the shared client’s timeout/limits policy. | Run a focused admin-login load test (`POST /api/v1/auth/admin/login`) with Frappe available. Compare outbound connect count, p95 latency, and timeout rate before/after pooling. |
| `60` | Plan change cleanup is O(total matching keys) and scans global leaderboard space on every plan change | `fastapi_app/services/plan_change.py:145-176` scans progress keys; `:205-265` scans six player key patterns plus every `memora:lb:*` leaderboard key | Plan change is not frequent, but when it runs it can block a request on global keyspace scans. The worst-case cost grows with total cached content and total leaderboard keys, not just one player’s data. | Add timing around `PlanChangeService.execute()` plus counters for `scan_calls`, `keys_deleted`, `zrem_calls`. Run plan change against a seeded Redis dataset and compare p95/p99 duration pre/post cleanup indexing. |
| `48` | Cache-aside JSON logic is repeated in several services with inconsistent hydration guarantees | `fastapi_app/api/v1/endpoints/auth.py:388-405`, `fastapi_app/services/announcements.py:21-37`, `fastapi_app/services/review.py:28-50`, `fastapi_app/services/catalog.py:39-77`, `fastapi_app/services/plan.py:49-84` | The repetition increases drift: some paths decode bytes, some do not; some coalesce misses, others do not; some cache empty results, others do not. That raises both maintenance cost and risk of thundering-herd regressions. | Track cache-hit ratio, miss fan-out (upstream Frappe calls per miss burst), and code size before/after extracting a common helper. Expect lower duplicated code and fewer concurrent identical misses. |

### High-Value Non-Issues

- `sessions.end_session` is already well optimized for a hot write path, using Lua plus pipelining (`fastapi_app/api/v1/endpoints/sessions.py:202-422`).
- `progress` endpoints already prefer stats hashes over full bitmap decode when the hash is fresh (`fastapi_app/api/v1/endpoints/progress.py:312-329`, `:413-429`, `:523-539`, `:659-675`, `:846-860`).
- No evidence yet justifies switching to `orjson`; JSON serialization is not the primary bottleneck visible in this codebase today.

## 3. Duplication Table

| Pattern | Locations (file:line) | Proposed abstraction | Expected gain | Effort |
| --- | --- | --- | --- | --- |
| Client IP extraction duplicated three times | `fastapi_app/api/v1/endpoints/auth.py:46-52`, `fastapi_app/api/v1/endpoints/voucher.py:40-45`, `fastapi_app/middleware/rate_limit.py:31-39` | `fastapi_app/core/request_meta.py::get_client_ip(request)` | One trust model for `X-Forwarded-For`, less drift, easier security review | Low |
| Fixed-window `INCR` + conditional `EXPIRE` Lua duplicated | `fastapi_app/services/rate_limit.py:11-17`, `fastapi_app/services/otp.py:30-37`, `fastapi_app/services/voucher.py:28-35` | Shared script registry in `fastapi_app/core/redis_scripts.py` | Fewer script copies, one place to tune TTL semantics and error handling | Low |
| Cache-aside JSON get/set repeated with minor variations | `fastapi_app/api/v1/endpoints/auth.py:388-405`, `fastapi_app/services/announcements.py:21-37`, `fastapi_app/services/review.py:28-50`, `fastapi_app/services/catalog.py:39-77`, `fastapi_app/services/plan.py:49-84` | `get_or_fill_json()` helper with serializer, TTL, and optional miss coalescing | Less duplicate code, fewer cache miss race regressions, easier instrumentation | Medium |
| Unlock/completion tree traversal duplicated | `fastapi_app/services/unlock.py:11-23`, `fastapi_app/api/v1/endpoints/progress.py:44-101`, `fastapi_app/api/v1/endpoints/progress.py:199-247` | `fastapi_app/services/progress_tree.py` for shared tree math | Lower maintenance risk, consistent unlock semantics, simpler tests | Medium |
| Practice SQL selection logic split between batched and legacy code paths | `fastapi_app/services/practice.py:1227-1294`, `fastapi_app/services/practice.py:1377-1446` | Shared SQL builder plus one selector strategy wrapper | Easier query tuning, simpler rollout cleanup, less divergence in filters | Medium |
| Repeated Frappe “does phone exist?” lookup in auth flows | `fastapi_app/api/v1/endpoints/auth.py:439-455`, `fastapi_app/api/v1/endpoints/auth.py:615-629`, `fastapi_app/api/v1/endpoints/auth.py:666-681` | `AuthLookupService.check_phone_exists()` | One error-handling policy, easier per-endpoint timing and caching if added later | Low |

## 4. Quick Wins (1-2 Days)

- Pass `raw_redis` into `PracticeService` so practice endpoints use the existing single-GET bitmap decoder instead of the fallback `BITFIELD` path.
- Replace profile batch truncation with deterministic chunking in `ProfileService`; keep chunking sequential to protect Frappe from sudden fan-out.
- Add request timing middleware and Frappe RPC timing logs before changing performance-sensitive code. This gives a reliable before/after baseline.
- Centralize client-IP parsing and the shared rate-limit Lua script to cut obvious duplication with near-zero behavioral risk.

## 5. Mid Refactors (About 1 Week)

- Pool `FrappeAuthService` the same way `FrappeClient` is pooled, and align timeout/connection-limit policy across both.
- Extract a shared cache helper for cache-aside JSON reads; use `SettingsService` as the durability/coalescing reference implementation.
- Replace `BaseHTTPMiddleware` in `RequestIDMiddleware` and `GlobalRateLimitMiddleware` with lightweight ASGI middleware. Both run on every request (`fastapi_app/middleware/request_id.py:11-22`, `fastapi_app/middleware/rate_limit.py:42-127`), so per-request overhead compounds.
- Consolidate progress-tree helpers so unlock rules only live in one place.

## 6. Bigger Bets (Optional)

- Stop scanning the full leaderboard namespace during plan change. Maintain a per-player index of leaderboard memberships, or move the expensive cleanup to an async background task.
- Remove the legacy per-topic practice selection fallback once production telemetry shows the batched selector is stable. That eliminates the N+1 SQL fallback path entirely.
- Introduce OpenTelemetry only after request and Frappe timing logs identify the few endpoints worth tracing deeply. Tracing everything first will add overhead without focus.

## 7. Instrumentation And Measurement Plan

### Minimal Instrumentation

1. Add request timing middleware:
   - Log `path`, `method`, `status_code`, `duration_ms`, `request_id`.
   - Apply before business middleware so total request time is visible.
2. Add Frappe timing in `FrappeClient._call_method()`:
   - Log `method`, `status_code`, `duration_ms`, `timeout_hit`, `arg_keys`.
   - This is the closest useful proxy for MariaDB timing because `fastapi_app` does not hold a local DB connection.
3. Add explicit timers around:
   - `PracticeService.get_practice_hierarchy()`
   - `ProfileService.get_profiles_batch()`
   - `PlanChangeService.execute()`
4. Optional next step:
   - Add OpenTelemetry spans around request handling and Frappe RPC calls only after the timing logs show stable hotspots.

### Benchmark Scenarios

| Scenario | Tool | Command / setup | Metrics |
| --- | --- | --- | --- |
| Practice hierarchy, warm cache, large bitmap | `hey` or existing `load_tests/` user flow | Seed a player with a dense subject bitmap, then hit `GET /api/v1/practice/hierarchy?subject_id=...&filter=completed` | p50/p95/p99, Redis commands per request, CPU |
| Leaderboard top 100 with cold profile cache | `hey` | Clear relevant profile keys, then hit `GET /api/v1/leaderboard/daily?limit=100` | p50/p95, fallback profile count, Frappe batch calls |
| Admin login auth path | `hey` | Repeated `POST /api/v1/auth/admin/login` against a reachable Frappe instance | p50/p95/p99, outbound connect count, timeout/error rate |
| Plan change on large Redis dataset | `locust` or a targeted script | Seed progress/stat/mastery keys and leaderboard keys, then invoke `POST /api/v1/plans/change` | total duration, `SCAN` count, keys touched, p95 |
| Middleware overhead | `hey` | Compare a lightweight route before/after ASGI middleware conversion | p50/p95, throughput, CPU |

### Verification Rules

- Always measure both warm-cache and cold-cache behavior.
- Capture request latency and upstream call volume together; latency alone will hide shifted work.
- Define rollback thresholds before rollout. Example: if p95 worsens by more than 5% or error rate increases, revert.

## 8. Proposed Code Changes (PR-Ready Diffs)

These patches are intentionally low-risk and do not change request or response contracts.

### Patch A: Use Raw Redis Fast Path In Practice

**Why**

`PracticeService` is already calling `ProgressService.get_completed_bits()`, but the dependency factory forces `raw_redis=None`, so the service always falls back to chunked `BITFIELD`.

**Diff**

```diff
diff --git a/fastapi_app/api/deps.py b/fastapi_app/api/deps.py
@@
 async def get_practice_service(
     redis_client: RedisClient,
+    raw_redis: Annotated[redis.Redis | None, Depends(get_redis_raw)],
     settings: SettingsDep,
 ) -> PracticeService:
     """Get PracticeService with all required dependencies."""
     frappe_client = await get_frappe_client()
     hierarchy_service = HierarchyService(redis_client, frappe_client)
     access_service = AccessService(redis_client, frappe_client=frappe_client)
-    progress_service = ProgressService(redis_client, frappe_client=frappe_client, raw_redis=None)
+    progress_service = ProgressService(
+        redis_client,
+        frappe_client=frappe_client,
+        raw_redis=raw_redis,
+    )
     return PracticeService(
         redis_client,
         frappe_client,
         settings,
         hierarchy_service,
         access_service,
         progress_service,
     )
```

**How To Measure**

- Benchmark `GET /api/v1/practice/hierarchy?subject_id=...&filter=completed`.
- Compare Redis commands per request and p95 latency.
- Expected result: lower Redis command count and lower p95 on completed-filter practice views.

**Risk**

- Very low. The raw path already exists and is used elsewhere.

**Rollback**

- Revert the dependency signature and pass `raw_redis=None` again.

### Patch B: Chunk Profile Fetches Instead Of Truncating At 50

**Why**

`ProfileService` currently drops any cache misses beyond `MAX_FRAPPE_BATCH`, which causes fallback identities on valid leaderboard rows and guarantees repeated misses.

**Diff**

```diff
diff --git a/fastapi_app/services/profile.py b/fastapi_app/services/profile.py
@@
-        # Limit batch size per RESEARCH.md pitfall
-        batch = player_ids[: self.MAX_FRAPPE_BATCH]
-        if len(player_ids) > self.MAX_FRAPPE_BATCH:
-            logger.warning(
-                "profile_batch_truncated",
-                requested=len(player_ids),
-                fetched=self.MAX_FRAPPE_BATCH,
-            )
-
-        try:
-            result = await self.frappe.call(
-                "memora_admin.api.profile.get_profiles_batch",
-                {"player_ids": batch},
-            )
-        except Exception as e:
-            logger.error("profile_frappe_fetch_error", error=str(e))
-            return {}
-
-        if not result:
-            return {}
+        batches = [
+            player_ids[i : i + self.MAX_FRAPPE_BATCH]
+            for i in range(0, len(player_ids), self.MAX_FRAPPE_BATCH)
+        ]
+        results: list[dict] = []
+        if len(batches) > 1:
+            logger.info(
+                "profile_batch_chunked",
+                requested=len(player_ids),
+                chunks=len(batches),
+                chunk_size=self.MAX_FRAPPE_BATCH,
+            )
+
+        for batch in batches:
+            try:
+                batch_result = await self.frappe.call(
+                    "memora_admin.api.profile.get_profiles_batch",
+                    {"player_ids": batch},
+                )
+            except Exception as e:
+                logger.error("profile_frappe_fetch_error", error=str(e), batch_size=len(batch))
+                continue
+            if batch_result:
+                results.extend(batch_result)
+
+        if not results:
+            return {}
@@
-        for item in result:
+        for item in results:
```

**How To Measure**

- Clear profile cache, then hit `GET /api/v1/leaderboard/daily?limit=100`.
- Compare fallback profile count, p95 latency, and number of Frappe batch calls.
- Expected result: no dropped profiles; one extra upstream call only when the miss set exceeds 50.

**Risk**

- Low. The response contract is unchanged.

**Rollback**

- Restore the truncation logic if Frappe cannot tolerate chunked misses.

### Patch C: Add Minimal Request And Frappe Timing

**Why**

The code already has several optimized paths. Before taking larger refactors, add instrumentation that shows where latency is actually coming from.

**Diff**

```diff
diff --git a/fastapi_app/middleware/request_metrics.py b/fastapi_app/middleware/request_metrics.py
new file mode 100644
--- /dev/null
+++ b/fastapi_app/middleware/request_metrics.py
@@
+import time
+
+import structlog
+from starlette.requests import Request
+
+logger = structlog.get_logger()
+
+
+class RequestMetricsMiddleware:
+    def __init__(self, app):
+        self.app = app
+
+    async def __call__(self, scope, receive, send):
+        if scope["type"] != "http":
+            await self.app(scope, receive, send)
+            return
+
+        start = time.perf_counter()
+        status_code = 500
+
+        async def send_wrapper(message):
+            nonlocal status_code
+            if message["type"] == "http.response.start":
+                status_code = message["status"]
+            await send(message)
+
+        try:
+            await self.app(scope, receive, send_wrapper)
+        finally:
+            duration_ms = round((time.perf_counter() - start) * 1000, 2)
+            request = Request(scope)
+            logger.info(
+                "http_request_timed",
+                method=request.method,
+                path=request.url.path,
+                status_code=status_code,
+                duration_ms=duration_ms,
+            )
diff --git a/fastapi_app/main.py b/fastapi_app/main.py
@@
-from fastapi_app.middleware.request_id import RequestIDMiddleware
+from fastapi_app.middleware.request_id import RequestIDMiddleware
+from fastapi_app.middleware.request_metrics import RequestMetricsMiddleware
@@
+app.add_middleware(RequestMetricsMiddleware)
diff --git a/fastapi_app/services/frappe_client.py b/fastapi_app/services/frappe_client.py
@@
-import httpx
+import time
+
+import httpx
@@
-        response = await client.post(url, json=kwargs)
+        started = time.perf_counter()
+        response = await client.post(url, json=kwargs)
+        duration_ms = round((time.perf_counter() - started) * 1000, 2)
+        logger.info(
+            "frappe_api_timed",
+            method=method,
+            status_code=response.status_code,
+            duration_ms=duration_ms,
+        )
```

**How To Measure**

- Compare p50/p95/p99 latency before and after each later optimization.
- Track `frappe_api_timed.duration_ms` distribution to distinguish app latency from upstream latency.

**Risk**

- Low. Logging volume increases; sample logs if traffic is very high.

**Rollback**

- Remove the middleware and timing log lines.

### Patch D: Centralize Client IP Parsing

**Why**

The same `X-Forwarded-For` parsing logic exists in three places. Centralizing it removes drift and makes it easier to harden or extend later.

**Diff**

```diff
diff --git a/fastapi_app/core/request_meta.py b/fastapi_app/core/request_meta.py
new file mode 100644
--- /dev/null
+++ b/fastapi_app/core/request_meta.py
@@
+from starlette.requests import Request
+
+
+def get_client_ip(request: Request) -> str:
+    forwarded = request.headers.get("X-Forwarded-For")
+    if forwarded:
+        return forwarded.split(",")[0].strip()
+    return request.client.host if request.client else "unknown"
diff --git a/fastapi_app/api/v1/endpoints/auth.py b/fastapi_app/api/v1/endpoints/auth.py
@@
-from fastapi_app.core.redis_keys import registration_options_key
+from fastapi_app.core.redis_keys import registration_options_key
+from fastapi_app.core.request_meta import get_client_ip
@@
-def _get_client_ip(request: Request) -> str:
-    ...
-
 ...
-    client_ip = _get_client_ip(request)
+    client_ip = get_client_ip(request)
diff --git a/fastapi_app/api/v1/endpoints/voucher.py b/fastapi_app/api/v1/endpoints/voucher.py
@@
+from fastapi_app.core.request_meta import get_client_ip
@@
-def _get_client_ip(request: Request) -> str:
-    ...
-
 ...
-    client_ip = _get_client_ip(request)
+    client_ip = get_client_ip(request)
diff --git a/fastapi_app/middleware/rate_limit.py b/fastapi_app/middleware/rate_limit.py
@@
+from fastapi_app.core.request_meta import get_client_ip
@@
-def _extract_client_ip(request: Request) -> str:
-    ...
-
 ...
-        client_ip = _extract_client_ip(request)
+        client_ip = get_client_ip(request)
```

**How To Measure**

- No direct latency win is expected. Verify via existing endpoint tests and a small smoke test behind a proxy.
- Primary win is correctness and easier future instrumentation.

**Risk**

- Low, but proxy behavior should be revalidated in staging.

**Rollback**

- Inline the helper again if proxy-specific behavior needs divergence.

## 9. Top 5 Next Steps

1. Ship Patch A (`raw_redis` in practice) first. It is the cleanest immediate latency win on an already hot endpoint family.
2. Ship Patch B (profile chunking) next. It fixes a real user-visible degradation on leaderboard pages without changing any contract.
3. Ship Patch C before deeper refactors so future work is measured, not guessed.
4. Use the new timings to decide whether pooled `FrappeAuthService` is worth immediate work in your environment.
5. Schedule the plan-change cleanup redesign only after the low-risk wins are in, because it has the highest implementation surface area.
