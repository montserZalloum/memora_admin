---
status: resolved
trigger: "Progress and access hydration mechanisms work in test scripts but fail in the real user login/API flow. After Redis FLUSHDB, user moonzalloum19@gmail.com shows 0% progress despite MariaDB having 33.33% completion data."
created: 2026-02-09T00:00:00Z
updated: 2026-02-09T16:20:00Z
---

## Current Focus

hypothesis: CONFIRMED and FIXED
test: All progress endpoints tested after simulated FLUSHDB
expecting: All show correct 33.3% completion
next_action: Archive session

## Symptoms

expected: After Redis flush, when user hits progress endpoints, hydration should kick in and restore progress from MariaDB. User moonzalloum19@gmail.com should show 33.33% completion for SUBJ-00028.
actual: Progress shows 0% after real login. Test scripts work fine but the real API flow doesn't trigger hydration correctly.
errors: No explicit errors - just wrong data (0% instead of 33.33%)
reproduction: 1. FLUSHDB on Redis 2. Login as moonzalloum19@gmail.com 3. Hit progress endpoints 4. See 0% instead of 33.33%
started: After implementing hydration in access.py and progress.py

## Eliminated

## Evidence

- timestamp: 2026-02-09T16:05:00Z
  checked: Frappe API endpoints (get_player_progress, get_player_access_keys) via curl
  found: Both return correct data - progress has bitset "80" and 33.33%, access has ["SUB-SUBJ-00028"]
  implication: Frappe side works perfectly, issue is in FastAPI service wiring

- timestamp: 2026-02-09T16:08:00Z
  checked: git diff -w HEAD -- fastapi_app/api/deps.py (whitespace-ignored diff)
  found: get_access_service() and get_progress_service() were NOT passing frappe_client to services in HEAD commit. Original code was `return AccessService(redis_client)` and `return ProgressService(redis_client)` without frappe_client parameter.
  implication: ROOT CAUSE IDENTIFIED - services had self.frappe = None, so ensure_hydrated() silently skipped with "no_frappe_client" warning log

- timestamp: 2026-02-09T16:10:00Z
  checked: Live endpoint test after simulated FLUSHDB with fixed deps.py
  found: GET /progress/ returns [{"percentage":33.3,"completed":1,"total":3}], GET /progress/SUBJ-00028 returns completed=1,total=3,pct=33.3
  implication: Primary fix in deps.py resolves the main issue

- timestamp: 2026-02-09T16:12:00Z
  checked: get_topic_lessons endpoint code (progress.py line 587-591)
  found: Bypasses ensure_hydrated() by directly constructing Redis key and using pipeline GETBIT without hydration check
  implication: Secondary bug - topic lessons would show all-incomplete after flush if called in isolation

- timestamp: 2026-02-09T16:18:00Z
  checked: Topic lessons endpoint after fix and re-deleting progress key
  found: GET /progress/SUBJ-00028/topics/TPC-00038/lessons returns completed=1 correctly, Redis key restored
  implication: Secondary fix works - ensure_hydrated() now called before pipeline GETBIT

- timestamp: 2026-02-09T16:19:00Z
  checked: GET /api/v1/subscriptions
  found: Returns {"grants":["SUB-SUBJ-00028"],"plan_subjects":[]}
  implication: plan_subjects:[] is correct - both plans have ONLY premium subjects (is_premium=1), so no free plan subjects exist. Working as designed.

## Resolution

root_cause: |
  PRIMARY: fastapi_app/api/deps.py - get_access_service() and get_progress_service() dependency
  injection functions were NOT passing FrappeClient to the service constructors. Both AccessService
  and ProgressService received frappe_client=None, causing ensure_hydrated() to silently skip
  hydration with a "no_frappe_client" warning log. Test scripts worked because they manually
  constructed services with a FrappeClient instance.

  SECONDARY: fastapi_app/api/v1/endpoints/progress.py - get_topic_lessons() endpoint bypassed
  ensure_hydrated() by directly constructing the Redis key and using pipeline GETBIT without
  checking if the bitmap exists.

  SUBSCRIPTIONS (not a bug): plan_subjects:[] is correct behavior. Both plans have only premium
  subjects (is_premium=1). plan_subjects only returns free subjects (is_premium=0).

fix: |
  1. fastapi_app/api/deps.py: Added `frappe_client = await get_frappe_client()` and passed it
     to AccessService and ProgressService constructors in their dependency functions.
  2. fastapi_app/api/v1/endpoints/progress.py: Added `await progress_service.ensure_hydrated()`
     call before the pipeline GETBIT in get_topic_lessons() endpoint.

verification: |
  Simulated FLUSHDB by deleting all user keys + hierarchy cache + stats cache.
  Tested with freshly generated JWT token:
  - GET /progress/ -> 33.3% (1/3) PASS
  - GET /progress/SUBJ-00028 -> completed=1, total=3, 33.3% PASS
  - GET /progress/SUBJ-00028/topics/TPC-00038/lessons -> 1/2 completed (50%) PASS
  - Redis keys auto-restored after each endpoint call PASS
  - GET /subscriptions -> grants=["SUB-SUBJ-00028"] hydrated correctly PASS

files_changed:
  - fastapi_app/api/deps.py (frappe_client injection)
  - fastapi_app/services/access.py (ensure_hydrated + frappe_client param)
  - fastapi_app/services/progress.py (ensure_hydrated + frappe_client param)
  - fastapi_app/api/v1/endpoints/progress.py (ensure_hydrated in get_topic_lessons)
  - memora_admin/api/subscriptions.py (Frappe API data providers)
