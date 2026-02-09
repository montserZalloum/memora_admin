---
status: resolved
trigger: "Users getting 403 NO_ACCESS errors after Redis cache clear, despite having active subscriptions in MariaDB. Access grants not auto-hydrating from MariaDB when Redis is empty."
created: 2026-02-09T00:00:00Z
updated: 2026-02-09T00:05:00Z
---

## Current Focus

hypothesis: CONFIRMED AND FIXED - AccessService had no hydration mechanism. Added ensure_hydrated() following WalletService pattern.
test: Deleted Redis access set, called progress endpoint, verified 200 response and Redis re-populated.
expecting: N/A - fix verified.
next_action: Archive session and commit.

## Symptoms

expected: Users with active Player Subscriptions in MariaDB (is_active=1) should be able to access their subscribed subjects even after Redis cache is cleared. Access service should auto-hydrate from MariaDB similar to how WalletService.ensure_hydrated() works.
actual: Users getting 403 "NO_ACCESS" errors when trying to access subjects they previously had access to. Redis key memora:access:{player_id} is empty or doesn't exist.
errors: 403 {"detail":{"code":"NO_ACCESS","message":"Content access required"}} on GET /api/v1/progress/{subject_id}
reproduction: 1) User has active subscription in MariaDB 2) Redis cache cleared 3) Redis key empty 4) Access check fails with 403
started: After Redis cache clear events; system not resilient to cache loss

## Eliminated

## Evidence

- timestamp: 2026-02-09T00:00:30Z
  checked: AccessService (fastapi_app/services/access.py)
  found: No ensure_hydrated() method. No FrappeClient dependency. Only Redis operations (SISMEMBER, SADD, SREM, SMEMBERS). No MariaDB fallback anywhere.
  implication: When Redis is cleared, access grants are permanently lost until manually re-synced or subscription doc is updated.

- timestamp: 2026-02-09T00:00:30Z
  checked: WalletService (fastapi_app/services/wallet.py)
  found: Has ensure_hydrated() at line 111. Checks redis.exists(key), if missing calls frappe_client.call("memora_admin.api.wallet.get_player_wallet") to restore from MariaDB. Called before every award_xp(), update_streak(), and in get_wallet().
  implication: WalletService solved this exact same problem. AccessService needs the same pattern.

- timestamp: 2026-02-09T00:00:30Z
  checked: Dependency injection (fastapi_app/api/deps.py)
  found: get_access_service() creates AccessService(redis_client) -- NO FrappeClient. Compare with get_wallet_service() which passes frappe_client=frappe_client.
  implication: AccessService cannot call Frappe API even if we add hydration code -- FrappeClient is not injected.

- timestamp: 2026-02-09T00:00:45Z
  checked: All access check call sites in progress.py and sessions.py
  found: check_access() and check_access_with_plan() are called in 7+ endpoints. None have MariaDB fallback. All will 403 if Redis key is missing.
  implication: Fix must be at service level (ensure_hydrated before check), not endpoint level.

- timestamp: 2026-02-09T00:00:50Z
  checked: MariaDB subscription data
  found: 1 active subscription (moonzalloum19@gmail.com -> SUB-SUBJ-00028). Currently in Redis too (likely manually fixed).
  implication: Current data is synced but will break again on next cache clear.

- timestamp: 2026-02-09T00:00:50Z
  checked: Frappe API (memora_admin/api/subscriptions.py)
  found: Only has create_subscription(). No get_player_subscriptions() endpoint for hydration.
  implication: Need to create a new Frappe whitelisted API endpoint for fetching active subscriptions.

- timestamp: 2026-02-09T00:03:00Z
  checked: Verification test 1 - DELETE Redis key, call GET /progress/SUBJ-00028
  found: HTTP 200, full progress response returned, Redis key restored with SUB-SUBJ-00028
  implication: Hydration works for check_access_with_plan() path (used by progress detail endpoint)

- timestamp: 2026-02-09T00:03:30Z
  checked: Verification test 2 - DELETE Redis key, call GET /progress/ (summary)
  found: HTTP 200, returned [{"subject_id":"SUBJ-00028",...}], Redis key restored
  implication: Hydration works for get_player_grants() path (used by progress summary endpoint)

- timestamp: 2026-02-09T00:04:00Z
  checked: Fast path performance - call with Redis key present
  found: 3.6ms response time, no Frappe API call made
  implication: Redis EXISTS check is O(1) and does not add measurable latency when cache is warm

## Resolution

root_cause: AccessService (fastapi_app/services/access.py) had no hydration mechanism to restore access grants from MariaDB when Redis keys are lost. Unlike WalletService which has ensure_hydrated() that loads from MariaDB via FrappeClient when Redis wallet hash is missing, AccessService only talked to Redis. When Redis is cleared (bench clear-cache, restart, etc.), memora:access:{player_id} sets were lost and never restored, causing 403 NO_ACCESS for all users with subscriptions.

fix: Added MariaDB hydration to AccessService following the WalletService.ensure_hydrated() pattern:
1. Created Frappe API endpoint get_player_access_keys() to fetch active subscriptions from MariaDB
2. Added ensure_hydrated() method to AccessService that checks Redis EXISTS, and if missing loads from MariaDB via Frappe API
3. Wired FrappeClient into AccessService dependency injection
4. ensure_hydrated() is called by check_access() and get_player_grants() - covers all access check paths

verification:
- Test 1: Deleted Redis key -> called /progress/SUBJ-00028 -> HTTP 200, Redis hydrated with SUB-SUBJ-00028
- Test 2: Deleted Redis key -> called /progress/ (summary) -> HTTP 200, subject included in response
- Test 3: Warm cache call -> 3.6ms response, no Frappe API overhead
- Frappe API endpoint tested directly: returns ["SUB-SUBJ-00028"] for test user

files_changed:
- fastapi_app/services/access.py: Added ensure_hydrated(), FrappeClient dependency, structlog logging
- fastapi_app/api/deps.py: Updated get_access_service() to inject FrappeClient
- memora_admin/api/subscriptions.py: Added get_player_access_keys() whitelisted API endpoint
