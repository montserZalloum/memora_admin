# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-01)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Phase 5 - Wallet & Gamification (in progress)

## Current Position

Phase: 5 of 7 (Wallet & Gamification)
Plan: 4 of 4 in current phase
Status: Phase complete
Last activity: 2026-02-02 - Completed 05-04-PLAN.md

Progress: [#######---] 69% (22 plans / ~32 estimated total)

## Performance Metrics

**Velocity:**
- Total plans completed: 22
- Average duration: 2.0min
- Total execution time: 0.75 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-infrastructure-foundation | 4 | 8min | 2.0min |
| 02-authentication | 3 | 9min | 3.0min |
| 03-access-control | 7 | 13min | 1.9min |
| 04-progress-tracking | 4 | 10min | 2.5min |
| 05-wallet-gamification | 4 | 7min | 1.75min |

**Recent Trend:**
- Last 5 plans: 4min, 1min, 2min, 2min, 2min
- Trend: stable/fast

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: 7 phases derived from 30 v1 requirements (depth: comprehensive)
- [Roadmap]: Session/Interaction flow, Leaderboards, Achievements deferred to v2
- [01-01]: Use pydantic-settings with SettingsConfigDict for env file loading
- [01-01]: Environment-based logging (JSON prod, colored dev console)
- [01-01]: Request ID middleware using structlog contextvars
- [01-02]: Redis pool stored in app.state for dependency access across requests
- [01-02]: Fail-fast: verify_redis_connection raises RuntimeError if Redis unreachable
- [01-02]: Kubernetes-style health: /live (fast), /ready (checks dependencies)
- [01-03]: Upstream includes keepalive 32 for connection pooling
- [01-03]: Location block as commented example (requires manual server block insertion)
- [01-04]: Documentation-only plan - no code changes required
- [01-04]: 5-phase migration approach for clarity and safety
- [02-01]: Use PyJWT (not python-jose) for JWT operations - cleaner API
- [02-01]: Rich access token payload (sub, email, role, tz, name, fid) for stateless auth
- [02-01]: FrappeAuthService returns None on any failure (generic response)
- [02-02]: IP rate limit checked before account (fails fast on distributed attacks)
- [02-02]: Email normalized to lowercase for consistent rate limiting
- [02-02]: Handles both bytes and str Redis responses for compatibility
- [02-03]: HTTPBearer for token extraction from Authorization header
- [02-03]: Generic 'Invalid credentials' for all auth failures (no enumeration)
- [02-03]: Refresh token not rotated (reusable per CONTEXT.md)
- [03-01]: SeasonMeta computed properties (is_active, is_expired, is_started) for O(1) validation
- [03-01]: Single HSET with mapping dict for atomic Redis hash updates
- [03-02]: Redis key pattern memora:access:{user_id} for player grants
- [03-02]: Direct cache.sadd/srem in doc_events for sub-second sync
- [03-03]: Free bypass checked FIRST in Gate 2 (avoids unnecessary Redis lookup)
- [03-03]: Structured error detail {code, message} for 403 responses
- [03-04]: Webhook idempotency via Redis key with 24h TTL (processing/completed state)
- [03-04]: FastAPI BackgroundTasks for webhook processing (fast acknowledgment)
- [03-04]: Redis list retry queue for failed webhook payloads
- [03-05]: Idempotent subscription creation (check exists, return existing info)
- [03-05]: Frappe API module pattern: memora_admin.api.{resource}
- [03-05]: Grant key format: SUB-{subject_name}, TRK-{track_name}
- [03-06]: Singleton FrappeClient for HTTP connection reuse
- [03-06]: Graceful degradation: Redis grant succeeds even if MariaDB subscription fails
- [03-06]: Far-future date (2099-12-31) for permanent grants
- [03-07]: Actions group button for standard Frappe pattern
- [03-07]: Default expires_at to season end_date for subscription grants
- [04-01]: Use computed_field decorator for percentage calculation
- [04-02]: Sequential bit_index allocation starting from 0 for dense bitmap storage
- [04-02]: 1 hour cache TTL for hierarchy (balances freshness vs. performance)
- [04-02]: Public call() method added to FrappeClient for generic API calls
- [04-03]: Compute unlock state on-demand (avoids stale cached unlock states)
- [04-03]: Use SUB-{subject} access key for Gate 2 check (consistent pattern)
- [04-03]: Log replay status but don't return (wallet integration in Phase 5)
- [04-04]: subject_name uses subject_id as placeholder (Frappe name fetch deferred)
- [04-04]: Unlock state computed inline using existing helper functions
- [05-01]: Use Redis hash for wallet storage (allows atomic HINCRBY for XP)
- [05-01]: Lua script for atomic streak update with date comparison
- [05-01]: Asia/Amman timezone for streak boundaries (single timezone per CONTEXT.md)
- [05-01]: No streak_date in WalletResponse (client doesn't need it)
- [05-02]: 5-minute cache TTL for settings (shorter than hierarchy due to admin mutability)
- [05-02]: Fallback to defaults if Frappe unavailable (graceful degradation)
- [05-02]: Default max_streak_multiplier_percent = 50 (50% max bonus)
- [05-03]: System Manager role check for admin wallet endpoint
- [05-03]: Structured error detail {code: ADMIN_REQUIRED} for non-admin access
- [05-04]: Streak multiplier applies to both fresh and replay XP
- [05-04]: Replays do NOT count toward streak maintenance
- [05-04]: Floor XP result (int() not round()) for predictable minimum

### Pending Todos

None yet.

### Blockers/Concerns

None yet.

## Session Continuity

Last session: 2026-02-02T13:50:00Z
Stopped at: Completed 05-04-PLAN.md (Phase 5 complete)
Resume file: None

Previous plan summaries:

01-01: FastAPI scaffold with pydantic-settings configuration, structured logging, and health check endpoint
01-02: Async Redis connection pool with fail-fast startup, dependency injection, and readiness health check
01-03: Nginx upstream for FastAPI sidecar with X-Request-ID propagation and Frappe integration documentation
01-04: Server migration guide for relocating Redis/FastAPI to dedicated server with rollback procedures
02-01: JWT utilities with PyJWT, auth Pydantic models, and async Frappe credential verification service
02-02: Session management with token family ID for single-session enforcement and dual-key rate limiting with atomic Lua script
02-03: Login/refresh endpoints with dual rate limiting, session-based token family validation, and stateless JWT auth dependency
03-01: SeasonMeta model and SeasonService for Gate 1 validation with Redis hash caching
03-02: Redis set-based player access management with Frappe doc_events hooks for immediate subscription/season sync
03-03: Double-Gate FastAPI dependencies for content access control with free content bypass and structured error responses
03-04: Payment webhook with idempotency and background processing, admin grant/revoke endpoints with role-based access control
03-05: Frappe whitelisted API methods for subscription creation and Product Grant key extraction callable via frappe.call()
03-06: FrappeClient service with async httpx for Frappe API calls, wiring payment webhook to fetch grant keys and create subscriptions
03-07: Frappe Desk Grant Access button on Player Profile creating subscriptions that auto-sync to Redis
04-01: Redis bitmap-based progress tracking with O(1) SETBIT/GETBIT operations and nested Pydantic hierarchy models for unlock calculation
04-02: Frappe API for subject hierarchy with nested is_linear flags, HierarchyService with Redis caching for <20ms unlock calculations
04-03: POST /progress/complete endpoint with unlock state enforcement and Double-Gate access validation
04-04: GET /progress and GET /progress/{subject} endpoints with completion percentages and unlock states wired into API
05-01: Redis-backed WalletService with atomic HINCRBY for XP and Lua script for streak date comparison
05-02: SettingsService with 5-minute Redis cache for admin-configurable XP values and streak multiplier cap
05-03: GET /wallet and GET /wallet/{player_id} endpoints with role-based access control for XP and streak display
05-04: Extended completion endpoint with atomic XP and streak updates via WalletService
