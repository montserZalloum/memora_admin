# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-01)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Phase 4 - Progress Tracking (in progress)

## Current Position

Phase: 4 of 7 (Progress Tracking)
Plan: 1 of 5 in current phase
Status: In progress
Last activity: 2026-02-02 - Completed 04-01-PLAN.md (Progress Models & Service)

Progress: [####------] 47% (15 plans / ~32 estimated total)

## Performance Metrics

**Velocity:**
- Total plans completed: 15
- Average duration: 2.0min
- Total execution time: 0.50 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-infrastructure-foundation | 4 | 8min | 2.0min |
| 02-authentication | 3 | 9min | 3.0min |
| 03-access-control | 7 | 13min | 1.9min |
| 04-progress-tracking | 1 | 2min | 2.0min |

**Recent Trend:**
- Last 5 plans: 3min, 1min, 2min, 1min, 2min
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

### Pending Todos

None yet.

### Blockers/Concerns

None yet.

## Session Continuity

Last session: 2026-02-02T11:47:25Z
Stopped at: Completed 04-01-PLAN.md (Progress Models & Service)
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
