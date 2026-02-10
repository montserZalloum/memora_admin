---
phase: 26-profile-page-api
plan: 02
subsystem: profile-api
tags: [profile-page, fastapi-endpoints, aggregation-service, redis-pipeline, mastery-cache]

dependency_graph:
  requires:
    - "26-01 (level system constants, Pydantic models, Frappe APIs)"
  provides:
    - "ProfilePageService aggregation service (7 methods)"
    - "7 FastAPI profile page endpoints (hero, stats, mastery, activity, avatar, avatars, logout)"
    - "ProfilePageServiceDep dependency injection"
  affects:
    - "Client profile page UI (all endpoints ready)"

tech_stack:
  added: []
  patterns:
    - "Aggregation service composing existing services (no logic duplication)"
    - "Redis pipeline for batch ZSCORE (7 daily keys in 1 round-trip)"
    - "Mastery Redis cache with 5-min TTL and Frappe API fallback"
    - "Subject-filtered stats via leaderboard ZSETs with int(score) composite stripping"

key_files:
  created:
    - "fastapi_app/services/profile_page.py"
    - "fastapi_app/api/v1/endpoints/profile.py"
  modified:
    - "fastapi_app/api/deps.py"
    - "fastapi_app/api/v1/router.py"

decisions:
  - id: "AGGREGATE-SVC"
    decision: "ProfilePageService composes existing services, does not duplicate logic"
    rationale: "Single responsibility; WalletService, ProfileService, etc. already handle their domains"
  - id: "WEEKLY-PIPELINE"
    decision: "Redis pipeline with 7 ZSCORE calls for weekly activity"
    rationale: "Single round-trip instead of 7 sequential calls; matches leaderboard key pattern"
  - id: "THIN-ENDPOINTS"
    decision: "Endpoints delegate all logic to ProfilePageService"
    rationale: "Follows existing pattern (reviews.py); keeps endpoints testable and thin"

metrics:
  duration: "~3 minutes"
  completed: "2026-02-10"
---

# Phase 26 Plan 02: ProfilePageService and FastAPI Endpoints Summary

**7-method aggregation service composing WalletService/ProfileService/SessionService + 7 FastAPI endpoints with subject filtering, mastery cache, and Redis pipeline for weekly activity**

## What Was Done

### Task 1: ProfilePageService Aggregation Service
- Created `fastapi_app/services/profile_page.py` with `ProfilePageService` class
- 7 methods: `get_hero`, `get_stats`, `get_weekly_activity`, `get_mastery`, `update_avatar`, `get_avatar_options`, `logout`
- `get_hero`: Composes WalletService (XP) + ProfileService (display_name, avatar) + calculate_level() for level info
- `get_stats`: Streak always global (wallet), XP from leaderboard ZSETs per-subject or wallet global, items_learned from stats hashes
- `get_weekly_activity`: Redis pipeline with 7 ZSCORE calls (Mon-Sun), Asia/Amman timezone, subject-filtered daily keys
- `get_mastery`: Redis cache (5-min TTL) with Frappe API fallback for memory state classification
- `update_avatar`: Frappe API call + profile cache invalidation
- `get_avatar_options`: Frappe API call for DocType meta-driven options
- `logout`: SessionService.invalidate_session() + optional DeviceService.remove_device()

### Task 2: FastAPI Endpoints, Dependency Injection, Router Wiring
- Created `fastapi_app/api/v1/endpoints/profile.py` with 7 routes:
  - `GET /api/v1/profile` -> HeroResponse
  - `GET /api/v1/profile/stats?subject=X` -> StatsResponse
  - `GET /api/v1/profile/mastery?subject=X` -> MemoryMasteryResponse
  - `GET /api/v1/profile/activity?subject=X` -> WeeklyActivityResponse
  - `PUT /api/v1/profile/avatar` -> AvatarUpdateResponse
  - `GET /api/v1/profile/avatars` -> AvatarOptionsResponse
  - `POST /api/v1/profile/logout` -> LogoutResponse
- Added `ProfilePageServiceDep` to `deps.py` (Redis + FrappeClient injection)
- Added `profile` import and `router.include_router(profile.router)` to `router.py`
- Avatar update catches FrappeAPIError and returns 400
- Logout reads X-Device-ID header for optional device removal
- All endpoints require JWT authentication (401 without token)

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| AGGREGATE-SVC | ProfilePageService composes existing services | No logic duplication; each service owns its domain |
| WEEKLY-PIPELINE | Redis pipeline for 7 daily ZSCORE calls | Single round-trip vs 7 sequential; matches leaderboard patterns |
| THIN-ENDPOINTS | All logic delegated to service layer | Follows reviews.py pattern; keeps endpoints thin |

## Deviations from Plan

None -- plan executed exactly as written.

## Verification Results

- All 7 endpoints registered in FastAPI router (verified via route listing)
- Health check returns 200 after restart
- Unauthenticated request returns 401 (JWT required)
- Existing endpoints (reviews, auth) still work after router changes
- Weekly activity uses Redis pipeline (confirmed via source inspection)
- Mastery uses Redis cache with MASTERY_CACHE_TTL (confirmed via source inspection)
- Logout invalidates session + removes device (confirmed via source inspection)
- Avatar update invalidates profile cache (confirmed via source inspection)
- No new pip dependencies required
- Ruff lint and format checks pass on all files

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | `1e22eea` | ProfilePageService aggregation service (7 methods) |
| 2 | `aca7d25` | Profile endpoints, dependency injection, and router wiring |

## Next Phase Readiness

Phase 26 (Profile Page API) is complete. All endpoints are functional and ready for client integration:
- Hero section with level system (XP progress bar data)
- Subject-filtered stats, mastery, and weekly activity
- Avatar selection with DocType validation
- Logout with session + device cleanup

No blockers identified.
